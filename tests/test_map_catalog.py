"""Catalogue integration checks for optional multi-source population geometry."""
from copy import deepcopy
import gzip
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import build_map_catalog as catalog


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


class MapCatalogPopulationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.map = self.root / "map-data"
        self.relative = "map-data/population/age.geojson.gz"
        self.doc = {"type": "FeatureCollection", "metadata": {
            "generated_at": "same generation",
            "sources": [{"requested_url": "https://official.example/roster", "downloaded_at": "one"},
                        {"requested_url": "https://official.example/boundary", "downloaded_at": "two"}],
            "population_period": "2026-09", "boundary_vintage": "2026-08-17",
        }, "features": [{"type": "Feature", "geometry": None, "properties": {"area_id": "63000010001"}}]}
        write(self.map / "admin/manifest.json", {"metadata": {"license": {"name": "existing licence"}}, "records": []})
        write(self.map / "osm/manifest.json", {"metadata": {
            "bbox": [121, 24, 122, 26], "license_url": "https://www.openstreetmap.org/copyright",
            "attribution": "OpenStreetMap contributors",
        }, "records": []})

    def tearDown(self):
        self.temporary.cleanup()

    def output_and_manifest(self):
        path = self.root / self.relative
        path.parent.mkdir(parents=True, exist_ok=True)
        encoded = json.dumps(self.doc).encode()
        path.write_bytes(gzip.compress(encoded, mtime=0))
        write(self.map / "population/manifest.json", {
            "metadata": {"title": "北北基官方村里年齡圖層"},
            "records": [{"dataset_id": "northern_village_age", "outputs": [{
                "path": self.relative, "record_count": len(self.doc["features"]),
                "bytes": path.stat().st_size, "encoding": "gzip", "uncompressed_bytes": len(encoded),
            }]}],
        })

    def review(self, **overrides):
        record = {"file": self.relative, "review_status": "pass_with_limitations", "record_count": 1,
                  "reviewed_generated_at": "same generation", "sources": deepcopy(self.doc["metadata"]["sources"])}
        record.update(overrides)
        write(self.map / "quality/village_age_geography_review.json", {"records": [record]})

    def build(self):
        with patch.object(catalog, "ROOT", self.root), patch.object(catalog, "MAP", self.map):
            return catalog.build()

    def test_optional_population_manifest_and_exact_review_are_included(self):
        self.assertEqual(self.build()["records"], [])
        self.output_and_manifest()
        self.review()
        record = self.build()["records"][0]
        self.assertEqual(record["group"], "population")
        self.assertEqual(record["title"], "北北基官方村里年齡圖層")
        self.assertEqual(record["manifest"], "population/manifest.json")
        self.assertEqual(record["independent_review_status"], "passed")
        self.assertEqual(record["files"][0]["path"], "population/age.geojson.gz")
        self.assertEqual(record["files"][0]["encoding"], "gzip")
        review = record["files"][0]["independent_review"]
        self.assertEqual(review["status"], "pass_with_limitations")
        self.assertEqual(review["report"], "quality/village_age_geography_review.json")
        self.assertEqual(review["sources"], self.doc["metadata"]["sources"])

    def test_multi_source_receipt_or_generation_change_invalidates_prior_review(self):
        self.output_and_manifest()
        self.review()
        original = deepcopy(self.doc)
        for change in ("receipt", "generation", "missing_sources"):
            with self.subTest(change=change):
                self.doc = deepcopy(original)
                if change == "receipt":
                    self.doc["metadata"]["sources"][1]["downloaded_at"] = "new download"
                elif change == "generation":
                    self.doc["metadata"]["generated_at"] = "new generation"
                else:
                    self.doc["metadata"].pop("sources")
                self.output_and_manifest()  # count and valid size do not prove the same snapshot
                record = self.build()["records"][0]
                self.assertEqual(record["files"][0]["independent_review"]["status"], "pending_source_version")
                self.assertEqual(record["independent_review_status"], "pending_or_needs_correction")

    def test_exact_snapshot_fields_do_not_override_explicit_period_checks(self):
        doc = {"metadata": {"generated_at": "fixed", "source_period": "2026-09", "sources": []}}
        self.assertFalse(catalog.review_matches_source(doc, {
            "reviewed_generated_at": "fixed", "sources": [], "source_periods": ["2026-08"],
        }))
        self.assertTrue(catalog.review_matches_source(doc, {
            "reviewed_generated_at": "fixed", "sources": [], "source_periods": ["2026-09"],
        }))
        self.assertFalse(catalog.review_matches_source({"metadata": {}}, {"reviewed_generated_at": None}))


if __name__ == "__main__":
    unittest.main()
