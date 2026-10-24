import unittest

from scripts.review_tdx_bus_travel_time import audit_delivery_accounting


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


if __name__ == "__main__":
    unittest.main()
