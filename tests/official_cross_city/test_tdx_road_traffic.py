"""Offline boundary checks; live source evidence is recorded in the manifest."""
from datetime import date
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace
import io

from scripts import tdx_road_traffic as road


class RoadTrafficTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.store = road.Store(self.root)

    def receipt(self, request, obj, *, jsonl=False):
        path = self.store.raw / (request["source_id"] + (".jsonl" if jsonl else ".json"))
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


if __name__ == "__main__":
    unittest.main()
