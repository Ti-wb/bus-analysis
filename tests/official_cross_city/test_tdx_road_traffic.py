"""Offline boundary checks; live source evidence is recorded in the manifest."""
from datetime import date, datetime
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace
import io
from contextlib import redirect_stdout
from urllib.error import HTTPError
from zoneinfo import ZoneInfo

from scripts import tdx_road_traffic as road


class RoadTrafficTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.store = road.Store(self.root)

    def receipt(self, request, obj, *, jsonl=False):
        path = self.store.raw / "responses" / (request["source_id"] + (".jsonl" if jsonl else ".json"))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(json.dumps(r) for r in obj) if jsonl else json.dumps(obj))
        item = {"source_id": request["source_id"], "request": request, "raw_file": str(path.relative_to(self.root)),
            "status": "downloaded", "source_updated_at": None, "download_url": request["request_url"],
            "bytes": path.stat().st_size, "downloaded_at": "2026-10-24T10:00:00+08:00"}
        self.store.put(item)
        return item

    def output(self, item):
        result = road.parse_saved(self.store, item)
        return json.loads((self.root / result["output_file"]).read_text())

    def response(self, payload, headers=None):
        response = io.BytesIO(payload)
        response.status = 200
        response.headers = {"Content-Type": "application/json", "Content-Length": str(len(payload)), **(headers or {})}
        return response

    def args(self, **changes):
        return SimpleNamespace(**{"max_requests": 2, "max_response_mb": 1, "max_total_mb": 2,
                                  "min_request_interval": 0, **changes})

    def run_main_fetch(self, requests, responses, *options):
        with patch.object(road, "catalog", return_value=({}, [{"available_window_end": "2026-10-23"}])), \
                patch.object(road, "plan", return_value=requests), \
                patch.object(road, "token", return_value="synthetic-token"), \
                patch.object(road, "urlopen", side_effect=responses) as transport, \
                patch.object(road.time, "sleep"), \
                redirect_stdout(io.StringIO()):
            road.main(["--root", str(self.root), "--fetch", "--use-cached-catalog", *options])
        return transport

    def test_ids_and_flow_sentinel_survive_nested_normalization(self):
        value = road.normalize({"VDID": 123, "LinkFlows": [{"LinkID": "0001", "Lanes": [{"LaneID": 2,
            "Speed": -99, "Vehicles": [{"VehicleType": "S", "Volume": None}]}]}]})
        self.assertEqual(value["vd_id"], "123")
        lane = value["link_flows"][0]["lanes"][0]
        self.assertEqual(lane["lane_id"], "2")
        self.assertEqual(lane["speed"], -99)
        self.assertIsNone(lane["vehicles"][0]["volume"])

    def test_rejects_wrong_schema_and_missing_historical_day(self):
        request = road.plan("2026-10-23")[-1]
        for row in [{"SectionID": "s1", "InfoDate": "2026-10-23"}, {"VDID": "v1"}]:
            with self.assertRaises(ValueError):
                road.parse_payload(json.dumps(row).encode(), request)

    def test_rejects_provider_error_and_missing_items(self):
        request = next(r for r in road.plan("2026-10-23") if r["source_id"] == "current_vd")
        for obj in [{"Message": "Denied"}, {"Count": 42}]:
            with self.assertRaises(ValueError):
                road.parse_payload(json.dumps(obj).encode(), request)

    def test_full_current_requires_response_count_match(self):
        request = next(r for r in road.plan("2026-10-23") if r["source_id"] == "current_section")
        for count, status in [(1, "complete_requested_endpoint_by_response_count"), (2, "requested_scope_completeness_unverified")]:
            obj = self.output(self.receipt(request, {"Count": count, "Items": [{"SectionID": "001"}]}))
            self.assertEqual(obj["metadata"]["scope_status"], status)
            self.assertEqual(obj["metadata"]["data_kind"], "road_definition")

    def test_history_completeness_uses_independent_meta_count(self):
        requests = road.plan("2026-10-23")
        meta = next(r for r in requests if r["source_id"].endswith("_section_meta"))
        full = next(r for r in requests if r["source_id"] == "historical_2026-10-23_section")
        self.receipt(meta, {"Dates": "2026-10-23", "Count": 1, "SizeBytes": 90})
        output = self.output(self.receipt(full, [{"SectionID": "001", "InfoDate": "2026-10-23"}], jsonl=True))
        self.assertEqual(output["metadata"]["scope_status"], "complete_requested_date_by_meta_count")

    def test_sample_remains_sample_with_one_row(self):
        request = next(r for r in road.plan("2026-10-23") if r["source_id"].endswith("_section_live_sample"))
        output = self.output(self.receipt(request, [{"SectionID": "001", "InfoDate": "2026-10-23"}], jsonl=True))
        self.assertEqual(output["metadata"]["scope_status"], "sample_only")
        self.assertIsNone(output["records"][0]["travel_speed"])
        self.assertIsNone(output["records"][0]["travel_time"])
        self.assertIsNone(output["metadata"]["source_updated_at"])

    def test_vd_missing_measurement_is_null_and_interval_unknown(self):
        request = next(r for r in road.plan("2026-10-23") if r["source_id"] == "current_vd_live")
        payload = {"Count": 1, "Items": [{"VDID": "v1", "LinkFlows": [{"LinkID": "L1", "Lanes": [{"LaneID": 1,
            "Vehicles": [{"VehicleType": "S"}]}]}]}]}
        obj = self.output(self.receipt(request, payload))
        lane = obj["records"][0]["link_flows"][0]["lanes"][0]
        self.assertIsNone(lane["speed"])
        self.assertIsNone(lane["vehicles"][0]["volume"])
        self.assertIsNone(obj["metadata"]["units"]["volume"])

    def test_reference_catalog_obeys_date_bounds_month_and_seven_day_limit(self):
        refs = road.month_requests("section", date(2024, 1, 29), date(2024, 3, 4))
        self.assertEqual(refs[0]["start_date"], "2024-01-29")
        self.assertEqual(refs[-1]["end_date"], "2024-03-04")
        previous = None
        for row in refs:
            start, end = date.fromisoformat(row["start_date"]), date.fromisoformat(row["end_date"])
            self.assertLessEqual((end - start).days, 6)
            self.assertEqual(start.month, end.month)
            if previous is not None: self.assertEqual((start - previous).days, 1)
            previous = end

    def test_plan_separates_meta_definition_speed_and_flow(self):
        requests = road.plan("2026-10-23")
        self.assertEqual(len(requests), 10)
        self.assertEqual(sum(r["mode"] == "meta" for r in requests), 3)
        section = next(r for r in requests if r["source_id"] == "historical_2026-10-23_section")
        self.assertEqual(section["data_kind"], "road_definition")
        self.assertNotIn("TravelSpeed", section["request_url"])
        self.assertTrue(all("client_secret" not in r["request_url"] for r in requests))

    def test_failed_refresh_preserves_successful_source_and_offline_output(self):
        request = next(r for r in road.plan("2026-10-23") if r["source_id"] == "current_section")
        receipt = self.receipt(request, {"Count": 1, "Items": [{"SectionID": "001"}]})
        self.store.put({"source_id": request["source_id"], "request": request, "status": "provider_http_error", "http_status": 500})
        retained = self.store.receipt(request["source_id"])
        self.assertEqual(retained["status"], "downloaded")
        self.assertEqual(retained["last_failed_attempt"]["http_status"], 500)
        self.assertEqual(self.output(retained)["records"][0]["section_id"], "001")
        self.assertEqual(retained["raw_file"], receipt["raw_file"])

    def test_offline_rejects_changed_byte_count_and_source_url(self):
        request = next(r for r in road.plan("2026-10-23") if r["source_id"] == "current_section")
        receipt = self.receipt(request, {"Count": 1, "Items": [{"SectionID": "001"}]})
        for changed in [{**receipt, "bytes": 1}, {**receipt, "download_url": "https://example.org/wrong"},
            {**receipt, "request": {**request, "request_url": "https://example.org/wrong"}}]:
            with self.assertRaises(ValueError): self.output(changed)

    def test_truncated_legal_json_not_accepted_or_overwritten(self):
        request = next(r for r in road.plan("2026-10-23") if r["source_id"] == "current_section")
        original = self.receipt(request, {"Count": 1, "Items": [{"SectionID": "001"}]})
        old_bytes = (self.root / original["raw_file"]).read_bytes()
        payload = b'{"Count":0,"Items":[]}'
        response = io.BytesIO(payload)
        response.status = 200
        response.headers = {"Content-Type": "application/json", "Content-Length": str(len(payload) + 4)}
        args = SimpleNamespace(max_requests=1, max_response_mb=1, max_total_mb=1, min_request_interval=0)
        with patch.object(road, "token", return_value="secret-never-saved"), patch.object(road, "urlopen", return_value=response):
            road.fetch(self.store, [request], args)
        retained = self.store.receipt(request["source_id"])
        self.assertEqual(retained["status"], "downloaded")
        self.assertEqual((self.root / original["raw_file"]).read_bytes(), old_bytes)
        self.assertEqual(retained["last_failed_attempt"]["status"], "invalid_provider_response")
        failed = self.root / retained["last_failed_attempt"]["raw_file"]
        self.assertEqual(failed.read_bytes(), payload)
        self.assertNotIn("secret-never-saved", self.store.path.read_text())
        self.assertEqual(list(self.store.raw.rglob("*.part")), [])

    def test_only_non_sensitive_limit_headers_are_retained(self):
        headers = {"Retry-After": "60", "X-RateLimit-Remaining-Minute": "0", "Authorization": "secret", "Set-Cookie": "session"}
        self.assertEqual(road.usage_headers(headers), {"retry-after": "60", "x-ratelimit-remaining-minute": "0"})

    def test_observed_named_collections_are_explicit_and_count_checked(self):
        for key, collection in road.OBSERVED_COLLECTIONS.items():
            request = next(r for r in road.plan("2026-10-23") if r["source_id"] == "current_" + key)
            id_field = "VDID" if key.startswith("vd") else "SectionID"
            obj = self.output(self.receipt(request, {"Count": 1, collection: [{id_field: "001"}]}))
            self.assertEqual(obj["metadata"]["record_count"], 1)
            self.assertEqual(obj["metadata"]["response_collection_field"], collection)
            self.assertIsNotNone(obj["metadata"]["response_schema_deviation"])

    def test_historical_period_mismatch_is_preserved_and_flagged(self):
        request = next(r for r in road.plan("2026-10-23") if r["source_id"].endswith("vd_live_sample"))
        rows = [{"VDID": "001", "InfoDate": "2026-10-22", "DataCollectTime": "2026-10-22T15:58:00+00:00"}]
        obj = self.output(self.receipt(request, rows, jsonl=True))
        self.assertEqual(obj["records"][0]["info_date"], "2026-10-22")
        period = obj["metadata"]["statistical_period"]
        self.assertEqual(period["period_validation_status"], "contains_observations_outside_requested_taipei_date")
        self.assertEqual(period["data_collect_requested_taipei_day_mismatch_count"], 1)
        self.assertEqual(obj["metadata"]["scope_status"], "sample_only")
        self.assertEqual(obj["metadata"]["out_of_requested_date_count"], 1)
        self.assertEqual(obj["metadata"]["out_of_requested_date_ids"], ["001"])
        self.assertTrue(obj["metadata"]["requires_source_review"])

    def test_main_never_promotes_transport_rejected_legal_json(self):
        request = next(r for r in road.plan("2026-10-23") if r["source_id"] == "current_section")
        payload = b'{"Count":0,"Items":[]}'
        self.run_main_fetch([request], [self.response(payload, {"Content-Length": str(len(payload) + 4)})])
        retained = road.Store(self.root).receipt(request["source_id"])
        self.assertEqual(retained["status"], "invalid_provider_response")
        self.assertEqual(retained["failure_stage"], "transport")
        self.assertEqual(retained["rejection_reason"], "response_length_mismatch")
        delivery = json.loads((self.store.out / "delivery.json").read_text())
        self.assertEqual(delivery["records"], [])
        self.assertEqual(delivery["metadata"]["failures"][0]["source_id"], request["source_id"])
        self.assertFalse((self.store.out / "observations/current_section.json").exists())
        with patch.object(road, "catalog", return_value=({}, [{"available_window_end": "2026-10-23"}])), \
                patch.object(road, "plan", return_value=[request]), \
                patch.object(road, "urlopen", side_effect=AssertionError("offline must not fetch")), \
                redirect_stdout(io.StringIO()):
            road.main(["--root", str(self.root), "--offline"])
        self.assertEqual(road.Store(self.root).receipt(request["source_id"])["status"], "invalid_provider_response")
        self.assertEqual(road.Store(self.root).receipt(request["source_id"])["rejection_reason"], "response_length_mismatch")
        self.assertEqual(json.loads((self.store.out / "delivery.json").read_text())["records"], [])

    def test_main_request_budget_finalizes_completed_delivery(self):
        requests = [r for r in road.plan("2026-10-23") if r["source_id"] in {"current_section", "current_vd"}]
        transport = self.run_main_fetch(requests, [self.response(b'{"Count":1,"Items":[{"SectionID":"001"}]}')], "--max-requests", "1")
        self.assertEqual(transport.call_count, 1)
        delivery = json.loads((self.store.out / "delivery.json").read_text())
        self.assertEqual(len(delivery["records"]), 1)
        self.assertEqual(delivery["metadata"]["acquisition_stop_reason"], "request_budget_reached")
        self.assertEqual(delivery["metadata"]["not_requested_source_ids"], [requests[1]["source_id"]])
        self.assertIsNone(road.Store(self.root).receipt(requests[1]["source_id"]))

    def test_main_remaining_byte_budget_leaves_next_source_untouched(self):
        requests = [r for r in road.plan("2026-10-23") if r["source_id"] in {"current_section", "current_vd"}]
        payload = b'{"Count":1,"Items":[{"SectionID":"001"}]}'
        transport = self.run_main_fetch(requests, [self.response(payload)],
            "--max-response-mb", str(len(payload) / 1024**2),
            "--max-total-mb", str((len(payload) + 17) / 1024**2))
        self.assertEqual(transport.call_count, 1)
        delivery = json.loads((self.store.out / "delivery.json").read_text())
        self.assertEqual(len(delivery["records"]), 1)
        self.assertEqual(delivery["metadata"]["acquisition_stop_reason"], "total_response_budget_reached")
        self.assertEqual(delivery["metadata"]["not_requested_source_ids"], [requests[1]["source_id"]])
        self.assertIsNone(road.Store(self.root).receipt(requests[1]["source_id"]))

    def test_http_error_body_counts_toward_total_budget(self):
        requests = [r for r in road.plan("2026-10-23") if r["source_id"] in {"current_section", "current_vd"}]
        error = HTTPError(requests[0]["request_url"], 500, "server error", {}, io.BytesIO(b"12345678"))
        with patch.object(road, "token", return_value="synthetic"), patch.object(road, "urlopen", side_effect=[error]) as transport, redirect_stdout(io.StringIO()):
            stats = road.fetch(self.store, requests, self.args(max_response_mb=10 / 1024**2, max_total_mb=12 / 1024**2))
        self.assertEqual(transport.call_count, 1)
        self.assertEqual(stats["response_bytes"], 8)
        self.assertEqual(stats["stop_reason"], "total_response_budget_reached")
        self.assertEqual(self.store.receipt(requests[0]["source_id"])["bytes"], 8)
        self.assertIsNone(self.store.receipt(requests[1]["source_id"]))

    def test_oversized_http_error_counts_even_when_body_is_not_retained(self):
        request = next(r for r in road.plan("2026-10-23") if r["source_id"] == "current_section")
        error = HTTPError(request["request_url"], 500, "server error", {}, io.BytesIO(b"123456789012"))
        with patch.object(road, "token", return_value="synthetic"), patch.object(road, "urlopen", side_effect=error), redirect_stdout(io.StringIO()):
            stats = road.fetch(self.store, [request], self.args(max_response_mb=8 / 1024**2, max_total_mb=8 / 1024**2))
        self.assertEqual(stats["response_bytes"], 9)
        receipt = self.store.receipt(request["source_id"])
        self.assertIsNone(receipt["raw_file"])
        self.assertEqual(receipt["response_bytes_received"], 9)

    def test_http_error_backoff_applies_before_next_request_and_on_resume(self):
        requests = [r for r in road.plan("2026-10-23") if r["source_id"] in {"current_section", "current_vd"}]
        clock = [datetime(2026, 10, 24, 12, tzinfo=ZoneInfo("Asia/Taipei")).timestamp()]
        started = clock[0]
        calls = []
        headers = {"Retry-After": "90", "X-RateLimit-Remaining-Minute": "0"}
        def sleep(seconds): clock[0] += seconds
        def source_time(): return datetime.fromtimestamp(clock[0], ZoneInfo("Asia/Taipei")).isoformat(timespec="seconds")
        def transport(request, **kwargs):
            calls.append(clock[0])
            if len(calls) == 1:
                raise HTTPError(request.full_url, 503, "unavailable", headers, io.BytesIO(b"error"))
            return self.response(b'{"Count":0,"Items":[]}')
        with patch.object(road, "token", return_value="synthetic"), patch.object(road.time, "time", side_effect=lambda: clock[0]), \
                patch.object(road.time, "sleep", side_effect=sleep), patch.object(road, "now", side_effect=source_time), \
                patch.object(road, "urlopen", side_effect=transport), redirect_stdout(io.StringIO()):
            road.fetch(self.store, requests, self.args())
            self.assertGreaterEqual(calls[1] - started, 90)
            # A later invocation must reconstruct the backoff from the receipt,
            # including the previous failed attempt nested under a valid cache.
            old = self.receipt(requests[0], {"Count": 0, "Items": []})
            failed = {**old, "status": "provider_http_error", "checked_at": source_time(), "usage_headers": headers}
            self.store.put(failed)
            resume_started = clock[0]
            clock[0] += 5
            road.fetch(road.Store(self.root), [requests[1]], self.args())
            self.assertGreaterEqual(calls[-1] - resume_started, 90)

    def test_bad_normalized_refresh_preserves_raw_receipt_and_output(self):
        request = next(r for r in road.plan("2026-10-23") if r["source_id"] == "current_section")
        original = self.receipt(request, {"Count": 1, "Items": [{"SectionID": "original"}]})
        self.output(original)
        path = self.root / original["raw_file"]
        target = self.store.out / "observations/current_section.json"
        old_raw, old_output = path.read_bytes(), target.read_bytes()
        for row, stage, reason in [({"SectionID": "new", "FooBar": 1, "foo_bar": 2}, "normalization", "normalized_field_collision"),
                    ({"SectionID": "new", "TravelSpeed": float("nan")}, "normalization", "non_finite_source_number"),
                    ({"SectionID": float("nan")}, "normalization", "non_finite_source_number"),
                    ({"SectionID": "new", "Name": "\ud800"}, "serialization", "strict_json_serialization_failed")]:
            with self.subTest(row=row):
                response = self.response(json.dumps({"Count": 1, "Items": [row]}).encode())
                with patch.object(road, "token", return_value="synthetic"), patch.object(road, "urlopen", return_value=response), redirect_stdout(io.StringIO()):
                    road.fetch(self.store, [request], self.args())
                retained = self.store.receipt(request["source_id"])
                self.assertEqual(path.read_bytes(), old_raw)
                self.assertEqual(target.read_bytes(), old_output)
                self.assertEqual({k: retained[k] for k in original if k != "last_failed_attempt"}, {k: original[k] for k in original if k != "last_failed_attempt"})
                self.assertEqual(retained["last_failed_attempt"]["status"], "invalid_provider_response")
                self.assertEqual(retained["last_failed_attempt"]["failure_stage"], stage)
                self.assertEqual(retained["last_failed_attempt"]["rejection_reason"], reason)

    def test_nonfinite_historical_jsonl_id_cannot_replace_snapshot(self):
        request = next(r for r in road.plan("2026-10-23") if r["source_id"] == "historical_2026-10-23_section")
        original = self.receipt(request, [{"SectionID": "original", "InfoDate": "2026-10-23"}], jsonl=True)
        self.output(original)
        path = self.root / original["raw_file"]
        old_bytes = path.read_bytes()
        payload = b'{"SectionID":NaN,"InfoDate":"2026-10-23"}\n'
        with patch.object(road, "token", return_value="synthetic"), patch.object(road, "urlopen", return_value=self.response(payload)), redirect_stdout(io.StringIO()):
            road.fetch(self.store, [request], self.args())
        self.assertEqual(path.read_bytes(), old_bytes)
        retained = self.store.receipt(request["source_id"])
        self.assertEqual(retained["last_failed_attempt"]["rejection_reason"], "non_finite_source_number")
        self.assertEqual(self.output(retained)["records"][0]["section_id"], "original")

    def test_http_error_body_timeout_keeps_attempt_and_resume_backoff(self):
        class TimeoutBody(io.BytesIO):
            def read(self, size=-1): raise TimeoutError("synthetic private provider detail")
        parent = self.root
        requests = [r for r in road.plan("2026-10-23") if r["source_id"] in {"current_section", "current_vd"}]
        for status in [503, 200]:
            with self.subTest(http_status=status):
                self.root = parent / str(status)
                self.store = road.Store(self.root)
                if status == 503:
                    response = HTTPError(requests[0]["request_url"], status, "unavailable", {"Retry-After": "90"}, TimeoutBody())
                    reason = "http_error_body_read_failed"
                else:
                    response = TimeoutBody()
                    response.status = status
                    response.headers = {"Retry-After": "90", "Content-Length": "120"}
                    reason = "response_body_read_failed"
                transport = self.run_main_fetch(requests, [response])
                self.assertEqual(transport.call_count, 1)
                receipt = road.Store(self.root).receipt(requests[0]["source_id"])
                self.assertEqual(receipt["status"], "provider_http_error" if status == 503 else "invalid_provider_response")
                self.assertEqual(receipt["rejection_reason"], reason)
                self.assertEqual(receipt["error_type"], "TimeoutError")
                self.assertIsNone(receipt["raw_file"])
                self.assertIsNone(receipt["response_bytes_received"])
                self.assertNotIn("private provider detail", self.store.path.read_text())
                delivery = json.loads((self.store.out / "delivery.json").read_text())
                self.assertEqual(delivery["records"], [])
                self.assertEqual(delivery["metadata"]["failures"][0]["http_status"], status)
                self.assertEqual(delivery["metadata"]["acquisition_stop_reason"], reason)
                self.assertIsNone(delivery["metadata"]["response_bytes_received"])
                self.assertEqual(delivery["metadata"]["known_response_bytes_received"], 0)
                self.assertEqual(delivery["metadata"]["not_requested_source_ids"], [requests[1]["source_id"]])
                self.assertIsNone(road.Store(self.root).receipt(requests[1]["source_id"]))
                stamp = datetime.fromisoformat(receipt["checked_at"]).timestamp()
                clock = [stamp + 5]
                def sleep(seconds): clock[0] += seconds
                with patch.object(road, "token", return_value="synthetic"), patch.object(road.time, "time", side_effect=lambda: clock[0]), \
                        patch.object(road.time, "sleep", side_effect=sleep), patch.object(road, "urlopen", return_value=self.response(b'{"Count":0,"Items":[]}')), redirect_stdout(io.StringIO()):
                    stats = road.fetch(road.Store(self.root), [requests[1]], self.args())
                self.assertEqual(stats["requests_used"], 1)
                self.assertGreaterEqual(clock[0], stamp + 90)

    def test_catalog_three_year_window_clamps_february_29(self):
        class LeapDay(datetime):
            @classmethod
            def now(cls, tz=None): return cls(2024, 2, 29, 12, tzinfo=tz)
        def spec(historical):
            return {"servers": [{"url": road.HOST + "/api/historical"}], "components": {"schemas": {}},
                "paths": {road.path_for(key, historical).replace("Taipei", "{City}"):
                    {"get": {"summary": "fixture", "parameters": [], "responses": {}}} for key in road.ENDPOINTS}}
        supply = {"list": [{"domainL2Name": "縣市即時路況", "domainL3Name": label, "name": "臺北" + label,
            "dataStartTime": "2020-01-01", "dataEndTime": "2024-02-28", "pK_ServiceDetail": key, "version": "2"}
            for key, (_, label, _) in road.ENDPOINTS.items()]}
        with patch.object(road, "datetime", LeapDay), patch.object(self.store, "public", side_effect=[spec(False), spec(True), supply]):
            _, rows = road.catalog(self.store, offline=True)
        self.assertTrue(all(row["requested_three_year_window_start"] == "2021-02-28" for row in rows))


if __name__ == "__main__":
    unittest.main()
