import gzip
import json
from pathlib import Path
import tempfile
import unittest

from scripts.review_tdx_bus_travel_time import audit, audit_catalog_endpoint, audit_delivery_accounting, audit_taipei_endpoint_totals, audit_unavailable_source, read, read_source


def request(source_id, kind="travel_time"):
    result = {"source_id": source_id, "kind": kind, "city": "Taipei",
              "source_page": "https://tdx.transportdata.tw/api-service/swagger",
              "download_url": "https://tdx.transportdata.tw/example/" + source_id}
    result["reference_date" if kind == "route_reference" else "route_name"] = source_id
    return result


def receipt(query, *, reused=False, failed=False):
    return {**query, "status": "failed" if failed else "downloaded",
            "downloaded_at": None if failed else "2026-10-24T10:00:00+08:00",
            "raw_file": None if failed else "raw_data/example/" + query["source_id"] + ".json",
            "http_status": 429 if failed else 200, "reused_existing_response": reused}


def delivered(query, source=None):
    return {**query, "acquisition_status": source["status"] if source else "not_requested_in_this_bounded_stage",
            **{key: source.get(key) if source else None for key in ["downloaded_at", "raw_file", "http_status"]},
            "reused_existing_response": source.get("reused_existing_response", False) if source else False}


class LatestBatchReviewTest(unittest.TestCase):
    def test_full_audit_accepts_http_200_empty_array_without_claiming_nonempty_completeness(self):
        # Generate a small producer snapshot; the reviewer still independently
        # reads its retained raw source rather than invoking producer parsing.
        from scripts import tdx_bus_travel_time as producer
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = producer.Store(root)
            spec = {'servers': [{'url': producer.HOST + '/api/premium'}],
                    'paths': {path: {'get': {'parameters': [{'name': 'RouteName', 'in': 'path', 'required': True},
                               {'name': 'City', 'in': 'path', 'schema': {'enum': ['Taipei']}}]}}
                              for path in [producer.CITY_PATH, producer.INTERCITY_PATH]},
                    'components': {'schemas': {'TravelTime': {'properties': {
                        'RunTime': {'description': '秒'},
                        'ServiceTag': {'description': '0: 周末weekend; 1: 平日weekdays'}}}}}}
            producer.write(store.raw / 'specifications/travel_time.json', spec)
            reference = producer.route_request('2026-10-23')
            query = producer.travel_request(spec, 'Taipei', '空回應線')
            for req, body in [(reference, {'Count': 1, 'StartDate': '2026-10-01', 'EndDate': '2026-10-31', 'BusRoutes': [{'RouteUID': 'TPE001', 'RouteID': '001',
                                    'RouteName': {'Zh_tw': '空回應線'}, 'SubRoutes': []}]}), (query, [])]:
                path = store.raw / 'responses' / (req['source_id'] + '.json')
                producer.write(path, body)
                store.upsert({**req, 'status': 'downloaded', 'raw_file': str(path.relative_to(root)),
                              'bytes': path.stat().st_size, 'http_status': 200,
                              'downloaded_at': '2026-10-24T10:00:00+08:00'})
            store.manifest['metadata']['last_batch'] = {'planned_requests': [reference, query],
                'attempted_source_ids': [query['source_id']], 'successful_source_ids': [query['source_id']],
                'new_business_request_count': 1, 'stop_reason': None}
            store.save()
            store.rebuild(producer.date(2026, 10, 24))
            (root / 'docs').mkdir()
            (root / 'docs/tdx-bus-travel-time.md').write_text(producer.TERMS['url'])
            result = audit(root)
            self.assertEqual(result['metadata']['review_population']['successful_endpoint_responses'], 1)
            self.assertEqual(result['metadata']['review_population']['models'], 0)
            self.assertEqual(result['metadata']['review_population']['station_time_rows'], 0)
            coverage_path = store.out / 'coverage.json'
            coverage = json.loads(coverage_path.read_text())
            output_path = root / coverage['records'][0]['output_file']
            output = json.loads(output_path.read_text())
            # Both claims agreeing is insufficient when the actual source is [].
            coverage['records'][0]['scope_status'] = output['metadata']['scope_status'] = 'complete_unpaginated_requested_endpoint_response'
            producer.write(coverage_path, coverage)
            producer.write(output_path, output)
            with self.assertRaisesRegex(ValueError, 'completeness claim'):
                audit(root)

    def test_all_attempts_cannot_be_claimed_from_retained_route_count(self):
        rows = [{"download_url": "same", "endpoint_acquisition_status": "downloaded",
                 "endpoint_availability_status": "models_returned", "retained_files": ["observations.json"]},
                {"download_url": "same", "endpoint_acquisition_status": "downloaded",
                 "endpoint_availability_status": "models_returned", "retained_files": ["observations.json"]},
                {"download_url": "missing", "endpoint_acquisition_status": "not_requested",
                 "endpoint_availability_status": None, "retained_files": ["other-route-response.json"]}]
        progress = {"reference_route_count": 3, "unique_endpoint_count": 2, "attempted_endpoint_count": 1,
                    "successful_endpoint_count": 1, "source_unavailable_endpoint_count": 0,
                    "technical_failure_endpoint_count": 0, "not_requested_endpoint_count": 1,
                    "reference_route_uids_retained_count": 3, "all_reference_endpoints_attempted": False}
        catalog = {"metadata": {"taipei_endpoint_acquisition": progress}, "records": rows}
        coverage = {"metadata": {"taipei_endpoint_acquisition": progress}}
        delivery = {"metadata": {"taipei_endpoint_acquisition": progress,
                                "citywide_status": "partial_route_selection_not_citywide_travel_time_coverage"}}
        self.assertEqual(audit_taipei_endpoint_totals(catalog, coverage, delivery), progress)
        delivery["metadata"]["citywide_status"] = "all_taipei_reference_endpoints_attempted_response_coverage_explicit"
        with self.assertRaisesRegex(ValueError, "Citywide attempt"):
            audit_taipei_endpoint_totals(catalog, coverage, delivery)

    def test_route_catalog_preserves_unrequested_and_source_unavailability(self):
        row = {"route_name": "南港幹線", "download_url": "https://tdx.transportdata.tw/example",
               "endpoint_acquisition_status": "not_requested", "endpoint_response_empty": None,
               "endpoint_http_status": None, "endpoint_availability_status": None}
        audit_catalog_endpoint(row, [])
        endpoint = {"city": "Taipei", "route_name": row["route_name"], "download_url": row["download_url"],
                    "status": "failed", "http_status": 200, "availability_status": "published_no_data_message"}
        with self.assertRaisesRegex(ValueError, "availability/accounting"):
            audit_catalog_endpoint(row, [endpoint])
        row.update(endpoint_acquisition_status="failed", endpoint_http_status=200,
                   endpoint_availability_status="published_no_data_message")
        audit_catalog_endpoint(row, [endpoint])
        row["endpoint_response_empty"] = True
        with self.assertRaisesRegex(ValueError, "availability/accounting"):
            audit_catalog_endpoint(row, [endpoint])

    def test_source_unavailability_requires_explicit_retained_message(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            message = "目前沒有該縣市的[南港幹線]公車路線資料"
            path = root / "response.json"
            path.write_text(json.dumps({"message": message}), encoding="utf-8")
            source = {"raw_file": path.name, "http_status": 200, "bytes": path.stat().st_size,
                      "status": "failed", "complete_unparsed_response_retained": True,
                      "upstream_response_message": message}
            covered = {"availability_status": "published_no_data_message", "upstream_response_message": message}
            self.assertEqual(audit_unavailable_source(root, source, covered), message)
            covered["upstream_response_message"] = "different message"
            with self.assertRaisesRegex(ValueError, "classification/message"):
                audit_unavailable_source(root, source, covered)
            path.write_text(json.dumps({"message": "upstream temporarily unavailable"}), encoding="utf-8")
            source["bytes"] = path.stat().st_size
            with self.assertRaisesRegex(ValueError, "explicitly publish"):
                audit_unavailable_source(root, source, covered)

    def fixture(self, *, complete=False, zero_requests=False, failed=False):
        old_reference = request("2026-10-22", "route_reference")
        reference = request("2026-10-23", "route_reference")
        previous, current, pending, imported = map(request, ["306", "307西藏三民", "南京幹線", "307"])
        sources = [receipt(old_reference), receipt(reference), receipt(previous),
                   receipt(current, failed=failed), receipt(imported, reused=True)]
        planned = [reference, previous, current] if complete or zero_requests else [reference, previous, current, pending]
        attempted = [] if zero_requests else [current["source_id"]]
        manifest = {"metadata": {"last_batch": {"planned_requests": planned,
                    "attempted_source_ids": attempted, "successful_source_ids": [] if zero_requests or failed else attempted,
                    "new_business_request_count": len(attempted), "stop_reason": None}}, "records": sources}
        delivery_rows = [delivered(reference, sources[1]), delivered(previous, sources[2]), delivered(current, sources[3])]
        if not complete and not zero_requests:
            delivery_rows.append(delivered(pending))
        delivery_rows.append(delivered(imported, sources[4]))
        delivery = {"metadata": {"new_business_request_count": len(attempted), "reused_response_count": 1,
                    "not_requested_scope_ids": [pending["source_id"]] if not complete and not zero_requests else [],
                    "not_requested_is_failure": False, "acquisition_stop_reason": None,
                    "stage_status": "partial_bounded_acquisition" if not complete and not zero_requests else "completed_bounded_acquisition"},
                    "records": delivery_rows}
        return manifest, delivery

    def test_second_batch_all_cached_accepts_zero_new_requests(self):
        manifest, delivery = self.fixture(zero_requests=True)
        result = audit_delivery_accounting(manifest, delivery)
        self.assertEqual(result["new_business_requests_including_route_reference"], 0)
        self.assertEqual(result["latest_batch_attempted_source_ids"], [])
        self.assertEqual(result["cumulative_retained_travel_response_count"], 3)
        self.assertEqual(result["stage_status"], "completed_bounded_acquisition")

    def test_resume_counts_only_latest_attempts_and_preserves_previous_observations(self):
        manifest, delivery = self.fixture()
        result = audit_delivery_accounting(manifest, delivery)
        self.assertEqual(result["new_business_requests_including_route_reference"], 1)
        self.assertEqual(result["cumulative_retained_travel_response_count"], 3)
        self.assertEqual(result["delivery_endpoint_count_including_reuse"], 5)
        self.assertEqual(result["not_requested_scopes"], 1)
        self.assertEqual(result["stage_status"], "partial_bounded_acquisition")

    def test_new_batch_finishes_its_bounded_plan(self):
        manifest, delivery = self.fixture(complete=True)
        result = audit_delivery_accounting(manifest, delivery)
        self.assertEqual(result["new_business_requests_including_route_reference"], 1)
        self.assertEqual(result["not_requested_scopes"], 0)
        self.assertEqual(result["stage_status"], "completed_bounded_acquisition")
        # The older reference is retained by the manifest without being part
        # of the latest delivery plan or counted as a fresh HTTP request.
        self.assertEqual(result["planned_endpoint_count"], 3)
        self.assertEqual(result["delivery_endpoint_count_including_reuse"], 4)

    def test_failed_http_response_is_still_one_attempt_not_one_success(self):
        manifest, delivery = self.fixture(complete=True, failed=True)
        result = audit_delivery_accounting(manifest, delivery)
        self.assertEqual(result["new_business_requests_including_route_reference"], 1)
        self.assertEqual(result["cumulative_retained_travel_response_count"], 2)

    def test_rejects_attempt_count_mismatch_in_batch_or_delivery(self):
        for target in ["batch", "delivery"]:
            manifest, delivery = self.fixture()
            metadata = manifest["metadata"]["last_batch"] if target == "batch" else delivery["metadata"]
            metadata["new_business_request_count"] = 3
            with self.subTest(target=target), self.assertRaisesRegex(ValueError, "count mismatch"):
                audit_delivery_accounting(manifest, delivery)

    def test_rejects_duplicate_out_of_plan_or_missing_attempt_ids(self):
        for attempted in [["307西藏三民", "307西藏三民"], ["307"], None]:
            manifest, delivery = self.fixture()
            manifest["metadata"]["last_batch"]["attempted_source_ids"] = attempted
            with self.subTest(attempted=attempted), self.assertRaises(ValueError):
                audit_delivery_accounting(manifest, delivery)

    def test_rejects_success_outside_latest_batch_attempts(self):
        manifest, delivery = self.fixture(zero_requests=True)
        manifest["metadata"]["last_batch"]["successful_source_ids"] = ["306"]
        with self.assertRaisesRegex(ValueError, "Successful scope IDs"):
            audit_delivery_accounting(manifest, delivery)

    def test_rejects_incorrect_partial_or_completed_stage(self):
        for complete in [False, True]:
            manifest, delivery = self.fixture(complete=complete)
            delivery["metadata"]["stage_status"] = "partial_bounded_acquisition" if complete else "completed_bounded_acquisition"
            with self.subTest(complete=complete), self.assertRaisesRegex(ValueError, "Stage claim"):
                audit_delivery_accounting(manifest, delivery)

    def test_mixed_json_and_gzip_sources_validate_storage_and_decoded_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            body = json.dumps({"values": [None, 1.25, "001"]}).encode()
            for compressed in [False, True]:
                path = root / ("source.json.gz" if compressed else "source.json")
                path.write_bytes(gzip.compress(body) if compressed else body)
                source = {"raw_file": path.name, "http_status": 200, "bytes": len(body)}
                if compressed:
                    source.update(storage_encoding="gzip", stored_bytes=path.stat().st_size)
                self.assertEqual(read_source(root, source), json.loads(body))
                self.assertEqual(read(path), json.loads(body))
                source["bytes"] += 1
                with self.assertRaisesRegex(ValueError, "byte"):
                    read_source(root, source)

    def test_gzip_rejects_incorrect_stored_size_or_missing_encoding(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "source.json.gz"
            path.write_bytes(gzip.compress(b"[]"))
            source = {"raw_file": path.name, "http_status": 200, "bytes": 2,
                      "storage_encoding": "gzip", "stored_bytes": path.stat().st_size + 1}
            with self.assertRaisesRegex(ValueError, "stored-byte"):
                read_source(root, source)
            source.pop("storage_encoding")
            with self.assertRaisesRegex(ValueError, "encoding/suffix"):
                read_source(root, source)


if __name__ == "__main__":
    unittest.main()
