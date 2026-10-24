"""Synthetic integration checks for the additive population delivery."""
import gzip
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts import build_collection_catalog as catalog
from scripts.official_cross_city.common import fetch, write_json


class PopulationCatalogTests(unittest.TestCase):
    def test_compressed_delivery_keeps_independent_review_pending(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            relative = "data/population/moi_village_age_2026-09.json.gz"
            target = root / relative
            target.parent.mkdir(parents=True)
            with gzip.open(target, "wt", encoding="utf-8") as stream:
                json.dump({"records": [{"area_id": "09007040002"}]}, stream)
            write_json(root / "raw_data/moi_village_age/delivery.json", {
                "datasets": [{"dataset_id": "moi_village_population_age_monthly",
                              "status": "normalized", "period": "2026-09",
                              "output_files": [relative]}]})
            with patch.object(catalog, "ROOT", root):
                result = catalog.build()
            row = result["records"][0]
            self.assertEqual(row["outputs"][0]["record_count"], 1)
            self.assertEqual(row["independent_review_status"], "pending")
            write_json(root / "data/quality/moi_village_age_review.json", {
                "records": [{"file": relative, "record_count": 2, "review_status": "passed"}]})
            with patch.object(catalog, "ROOT", root):
                result = catalog.build()
            self.assertEqual(result["records"][0]["outputs"][0]["independent_review"]["status"],
                             "pending_changed_record_count")
            write_json(root / "data/quality/moi_village_age_review.json", {
                "records": [{"file": relative, "record_count": 1, "review_status": "passed",
                             "reviewed_generated_at": "previous snapshot",
                             "source": {"downloaded_at": "previous download"}}]})
            with patch.object(catalog, "ROOT", root):
                result = catalog.build()
            self.assertEqual(result["records"][0]["outputs"][0]["independent_review"]["status"],
                             "pending_changed_snapshot")

    def test_source_namespace_isolated_and_offline_provenance_checked(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            url = "https://www.ris.gov.tw/example.csv"
            body = b"id\n09007040002\n"
            raw = root / "raw_data/moi_village_age"
            raw.mkdir(parents=True)
            (raw / "x.csv").write_bytes(body)
            write_json(raw / "x.csv.source.json", {
                "requested_url": url, "effective_url": url, "page_url": url,
                "bytes": len(body), "http_status": 200, "downloaded_at": "known",
                "sha256": hashlib.sha256(body).hexdigest()})
            path, _ = fetch(root, "x.csv", url, url, {"www.ris.gov.tw"}, offline=True,
                            raw_namespace="moi_village_age")
            self.assertEqual(path.parent, raw)
            with self.assertRaises(ValueError):
                fetch(root, "x.csv", url, url, {"www.ris.gov.tw"}, offline=True)
            for namespace in ["../population", "/tmp", "a/b", ""]:
                with self.subTest(namespace=namespace), self.assertRaises(ValueError):
                    fetch(root, "x.csv", url, url, {"www.ris.gov.tw"}, offline=True,
                          raw_namespace=namespace)


if __name__ == "__main__":
    unittest.main()
