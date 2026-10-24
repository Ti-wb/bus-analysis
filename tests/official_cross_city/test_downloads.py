"""Mocked transport checks; not evidence of live government endpoint access."""
import io
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from scripts.official_cross_city import common, ntpc


class Response(io.BytesIO):
    def __init__(self, data, status=200, declared=None, content_type="text/csv", url="https://data.ntpc.gov.tw/x"):
        super().__init__(data)
        self.status = status
        self.headers = {"Content-Type": content_type, "Content-Length": str(len(data) if declared is None else declared)}
        self.url = url

    def geturl(self):
        return self.url


class DownloadTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def call(self, response, **kwargs):
        opener = SimpleNamespace(open=lambda *a, **k: response)
        with patch.object(common, "build_opener", return_value=opener):
            return common.fetch(self.root, "x.csv", "https://data.ntpc.gov.tw/x", "official-catalog", {"data.ntpc.gov.tw"}, offline=False, max_mb=1, **kwargs)

    def test_success_has_provenance_not_invented_source_time(self):
        path, receipt = self.call(Response(b"id,name\n1,test\n"))
        self.assertEqual(path.read_bytes(), b"id,name\n1,test\n")
        self.assertIsNone(receipt["source_data_time"])
        self.assertEqual(receipt["http_status"], 200)
        self.assertEqual(receipt["bytes"], path.stat().st_size)
        self.assertTrue((path.parent / "x.csv.source.json").exists())

    def test_offline_accepts_unchanged_download_and_rejects_same_length_edit(self):
        path, receipt = self.call(Response(b"id,name\n1,test\n"))
        self.assertEqual(receipt["sha256"], hashlib.sha256(path.read_bytes()).hexdigest())
        args = (self.root, "x.csv", receipt["requested_url"], receipt["page_url"], {"data.ntpc.gov.tw"})
        self.assertEqual(common.fetch(*args, offline=True), (path, receipt))
        path.write_bytes(b"id,name\n1,edit\n")
        with self.assertRaisesRegex(ValueError, "content"):
            common.fetch(*args, offline=True)

    def test_offline_legacy_receipt_requires_new_download(self):
        path, receipt = self.call(Response(b"id\n1\n"))
        receipt.pop("sha256", None)
        common.write_json(path.parent / "x.csv.source.json", receipt)
        with self.assertRaisesRegex(ValueError, "re-fetch"):
            common.fetch(self.root, "x.csv", receipt["requested_url"], receipt["page_url"],
                         {"data.ntpc.gov.tw"}, offline=True)

    def test_offline_ntpc_rejects_edited_csv_before_replacing_output(self):
        page, url = ntpc.urls("routes")
        data = b"id,providerid,namezh,pathattributeid\n1,7,Test,10\n"
        opener = SimpleNamespace(open=lambda *a, **k: Response(data, url=url))
        with patch.object(common, "build_opener", return_value=opener):
            path, receipt = common.fetch(self.root, "ntpc_routes.csv", url, page,
                                          {"data.ntpc.gov.tw"}, offline=False, max_mb=1)
        output = self.root / "data/official_cross_city/ntpc_routes.json"
        common.write_json(output, {"previous": "good"})
        path.write_bytes(data.replace(b"Test", b"Edit"))
        with self.assertRaisesRegex(ValueError, "content"):
            ntpc.main(["--root", str(self.root), "--kind", "routes", "--offline"])
        self.assertEqual(json.loads(output.read_text()), {"previous": "good"})
        self.assertEqual(json.loads((path.parent / "ntpc_routes.csv.source.json").read_text()), receipt)

    def test_digest_stays_in_raw_receipt(self):
        _, receipt = self.call(Response(b"id\n1\n"))
        self.assertIn("sha256", receipt)
        meta = common.metadata(receipt, "provider", "row", [])
        self.assertNotIn("sha256", meta["source"])
        self.assertEqual(meta["source"]["downloaded_at"], receipt["downloaded_at"])

    def test_partial_content_not_accepted_as_complete_export(self):
        with self.assertRaises(ValueError):
            self.call(Response(b"partial", status=206))

    def test_content_length_truncation_rejected(self):
        with self.assertRaises(ValueError):
            self.call(Response(b"truncated", declared=100))

    def test_empty_download_rejected(self):
        with self.assertRaises(ValueError):
            self.call(Response(b""))

    def test_html_response_preserves_previous_raw(self):
        path, receipt = self.call(Response(b"id\n1\n"))
        with self.assertRaises(ValueError):
            self.call(Response(b"<html>blocked</html>", content_type="text/html"))
        self.assertEqual(path.read_bytes(), b"id\n1\n")
        self.assertEqual(json.loads((path.parent / "x.csv.source.json").read_text()), receipt)

    def test_declared_download_budget(self):
        with self.assertRaises(ValueError):
            self.call(Response(b"x", declared=2 * 1024 * 1024))

    def test_stream_download_budget(self):
        response = Response(b"x" * (1024 * 1024 + 1))
        response.headers.pop("Content-Length")
        with self.assertRaises(ValueError):
            self.call(response)
        self.assertEqual(list((self.root / "raw_data/official_cross_city").glob("*.part")), [])

    def test_final_redirect_host_rejected(self):
        with self.assertRaises(ValueError):
            self.call(Response(b"id\n1", url="https://evil.example/data"))

    def test_parser_failure_does_not_replace_normalized_data(self):
        previous = self.root / "data/official_cross_city/ntpc_routes.json"
        common.write_json(previous, {"previous": "good"})
        source = self.root / "malformed.csv"
        source.write_text("wrong,schema\n1,2\n")
        with patch.object(ntpc, "fetch", return_value=(source, {})), self.assertRaises(ValueError):
            ntpc.main(["--root", str(self.root), "--kind", "routes", "--fetch"])
        self.assertEqual(json.loads(previous.read_text()), {"previous": "good"})

    def test_registry_distinguishes_sources_from_observations(self):
        registry = Path(__file__).resolve().parents[2] / "data/official_cross_city/source_registry.json"
        obj = json.loads(registry.read_text())
        self.assertEqual(len(obj["records"]), obj["metadata"]["record_count"])
        self.assertEqual(obj["metadata"]["kind"], "source_registry_not_transport_observations")
        self.assertEqual(obj["metadata"]["snapshot_status"], "additive_normalized_snapshots_with_independent_reports")
        implemented = [r for r in obj["records"] if r["collector_status"] == "implemented_previously_live_checked_snapshot_not_committed"]
        self.assertEqual({r["dataset_id"] for r in implemented},
                         {"ntpc_routes", "ntpc_stops", "ntpc_leapfrog", "metro_corridor_od"})
        self.assertTrue(all(r["last_live_validation_date"] == "2026-10-23" for r in implemented))
        self.assertTrue(all("not a current availability check" in r["validation_scope"] for r in implemented))
        self.assertEqual(obj["metadata"]["independent_review_status"], "see_dataset_reports")
        self.assertTrue(all(r["downloaded_at"] is None for r in implemented))
        self.assertTrue(all(not r["snapshot_committed"] for r in implemented))
        # A current snapshot claim must point at actual normalized rows and a
        # separate reviewer report; it cannot be satisfied by this registry.
        population_entry = next(r for r in obj["records"] if r["dataset_id"] == "regional_village_population")
        root = registry.parents[2]
        self.assertTrue(population_entry["snapshot_committed"])
        self.assertIsNotNone(population_entry["downloaded_at"])
        output = json.loads((root / population_entry["output_file"]).read_text(encoding="utf-8"))
        self.assertTrue(output["records"])
        self.assertEqual(output["dataset_id"], "regional_village_population")
        self.assertTrue((root / population_entry["quality_report"]).is_file())


if __name__ == "__main__":
    unittest.main()
