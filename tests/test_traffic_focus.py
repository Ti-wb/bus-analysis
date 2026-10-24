"""Focused checks for scope, unknown values, aggregate rows and review freshness."""

from pathlib import Path
import sys
import unittest
from unittest.mock import patch

from shapely.geometry import box

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from build_map_catalog import review_matches_source
from build_traffic_focus_osm import select_category
from update_traffic_housing import coordinates, count, normalize


class TrafficFocusTests(unittest.TestCase):
    def test_landuse_and_park_selection(self):
        residential = {"feature_class": "residential", "name": None}
        self.assertIsNone(select_category("landuse", residential, 999999, 10000))
        residential["name"] = "候選地名"
        self.assertEqual(select_category("landuse", residential, 20, 10000), "residential")
        park = {"feature_class": "park", "name": "公園"}
        self.assertIsNone(select_category("poi_areas", park, 9999, 10000))
        self.assertEqual(select_category("poi_areas", park, 10000, 10000), "recreation")

    def test_counts_do_not_fabricate_missing_capacity(self):
        self.assertIsNone(count(" "))
        self.assertIsNone(count("0", zero_as_unknown=True))
        self.assertEqual(count("0"), 0)
        self.assertEqual(count("300"), 300)
        with self.assertRaises(ValueError):
            count("300戶")

    def test_zero_coordinates_do_not_become_a_marker(self):
        self.assertIsNone(coordinates({"lat": "0", "lng": "0"}))
        self.assertIsNone(coordinates({"lat": None, "lng": "121.5"}))
        with self.assertRaises(ValueError):
            coordinates({"lat": "nan", "lng": "121.5"})

    def test_aggregate_outside_missing_and_ancillary_stay_distinct(self):
        row = {"name": "某社宅", "distict": "甲區", "address": "", "lat": "25", "lng": "121.5",
               "houseHolds": "300", "persons": "", "floors": "", "progress": "規劃中"}
        rows = [row, {**row, "name": "無座標社宅", "lat": "0", "lng": "0"},
                {**row, "name": "民辦都更(56處)", "houseHolds": "3600"},
                {**row, "name": "行政中心", "houseHolds": "0"},
                {**row, "name": "市外", "lng": "122"},
                {**row, "name": "區名衝突", "distict": "乙區"}]
        area = box(121.4, 24.9, 121.6, 25.1)
        districts = [({"admin_code": "63000010", "name": "甲區"}, area)]
        with patch("update_traffic_housing.boundaries", return_value=(area, districts)):
            mapped, unmapped, excluded = normalize(rows)
        self.assertEqual((len(mapped), len(unmapped), len(excluded)), (3, 2, 1))
        self.assertTrue(mapped[0]["properties"]["meets_household_threshold"])
        self.assertFalse(mapped[0]["properties"]["is_completed"])
        self.assertIsNone(mapped[0]["properties"]["daily_trips"])
        self.assertEqual(unmapped[1]["record_kind"], "multi_project_aggregate")
        self.assertIsNone(unmapped[1]["meets_household_threshold"])
        self.assertEqual(mapped[1]["properties"]["category"], "ancillary_facility")
        self.assertTrue(mapped[2]["properties"]["location_review_required"])
        self.assertEqual(mapped[2]["properties"]["source_district_name"], "乙區")

    def test_review_does_not_accept_a_new_download_with_unchanged_count(self):
        doc = {"metadata": {"source": {"source_updated_at": None, "downloaded_at": "2026-10-24T10:00:00+08:00"}}}
        self.assertFalse(review_matches_source(doc, {"source_periods": []}))
        self.assertFalse(review_matches_source(doc, {"source_downloaded_at": "2026-10-23T10:00:00+08:00"}))
        self.assertTrue(review_matches_source(doc, {"source_downloaded_at": "2026-10-24T10:00:00+08:00"}))
        self.assertIsNone(doc["metadata"]["source"]["source_updated_at"])

    def test_known_source_period_takes_precedence_over_receipt(self):
        doc = {"metadata": {"source": {"source_updated_at": "2026-10-20", "downloaded_at": "today"}}}
        self.assertFalse(review_matches_source(doc, {"source_periods": ["2026-10-19"], "source_downloaded_at": "today"}))
        self.assertTrue(review_matches_source(doc, {"source_periods": ["2026-10-20"]}))


if __name__ == "__main__":
    unittest.main()
