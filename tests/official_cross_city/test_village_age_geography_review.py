"""An independent audit must reject plausible same-count geography tampering."""
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest

try:
    from shapely.geometry import box, mapping
except ImportError:
    box = None

from scripts.review_village_age_geography import GROUP_INTERVALS, inspect_outputs, recompute_circle, registry_rows

MONTH = "2026-09"
MAIN = "VILLAGE_NLSC_1150817"
VINTAGE = "2026-08-17"


def fixture():
    """A hand-specified northern village, unassigned area and Sanhe overlap."""
    roster = {
        "63000010001": {"village_code": "63000010001", "district_code": "63000010", "village_name": "甲里", "source_row_number": 1},
        "10013280006": {"village_code": "10013280006", "district_code": "10013280", "village_name": "三和村", "source_row_number": 2},
        "10013130021": {"village_code": "10013130021", "district_code": "10013130", "village_name": "黎明村", "source_row_number": 3},
    }
    def boundary(code, county, district, name, rectangle, layer=MAIN, vintage=VINTAGE, note=""):
        return {"properties": {"VILLCODE": code, "TOWNCODE": code[:8], "COUNTYCODE": code[:5],
                               "COUNTYNAME": county, "TOWNNAME": district, "VILLNAME": name, "NOTE": note},
                "geometry": box(*rectangle), "layer": layer, "vintage": vintage}
    boundaries = {
        "63000010001": boundary("63000010001", "臺北市", "中正區", "甲里", (121.4, 25, 121.5, 25.1)),
        "63000010S01": boundary("63000010S01", "臺北市", "中正區", "", (121.5, 25, 121.6, 25.1), note="未編定村里"),
        "10013280006": boundary("10013280006", "屏東縣", "瑪家鄉", "三和村", (120.6, 22.7, 120.61, 22.71), "Village_Sanhe", None),
        "10013130021": boundary("10013130021", "屏東縣", "內埔鄉", "黎明村", (120.6, 22.7, 120.62, 22.72)),
    }
    layers = [{"layer": MAIN, "feature_count": 3, "source_crs": "EPSG:3824", "boundary_vintage": VINTAGE,
               "same_archive_distribution": True, "vintage_evidence": "dated SHP filename"},
              {"layer": "Village_Sanhe", "feature_count": 1, "source_crs": "EPSG:3824", "boundary_vintage": None,
               "same_archive_distribution": True, "vintage_evidence": "unknown; ZIP member timestamps are not geometry dates"}]
    poprows = [
        {"area_id": "63000010001", "district_id": "63000010", "county_name": "臺北市", "area_name": "臺北市中正區", "village_name": "甲里", "sex": "total", "period": MONTH, "population": 10, "age_counts": [0] * 100 + [10]},
        {"area_id": "10013280006", "district_id": "10013280", "county_name": "屏東縣", "area_name": "屏東縣瑪家鄉", "village_name": "三和村", "sex": "total", "period": MONTH, "population": None, "age_counts": [None] * 101},
        {"area_id": "10013130021", "district_id": "10013130", "county_name": "屏東縣", "area_name": "屏東縣內埔鄉", "village_name": "黎明村", "sex": "total", "period": MONTH, "population": 5, "age_counts": [5] + [0] * 100},
    ]
    def cross(code, name, status, population, layer=MAIN, vintage=VINTAGE, present=True):
        return {"area_id": code, "county_code": code[:5], "population_present": present, "roster_present": present,
                "boundary_present": True, "population_name": name if present else None,
                "roster_name": name if present else None, "boundary_name": name, "district_id": code[:8],
                "roster_match_status": "matched_code_parent_and_name" if present else "missing_population",
                "boundary_identity_status": "verified" if present else "unassigned", "boundary_join_status": status,
                "allocation_eligible": status == "verified", "source_population": population,
                "population_period": MONTH, "roster_period": MONTH, "boundary_layer": layer,
                "boundary_vintage": vintage, "same_month_geometry_verified": False}
    summary = {"population_village_count": 3, "roster_village_count": 3, "boundary_feature_count": 4,
               "boundary_layers": layers, "population_missing_from_roster": [], "roster_missing_from_population": [],
               "population_missing_boundary": [], "boundary_status_counts": {"verified": 1, "unassigned": 1, "geometry_conflict": 2},
               "same_month_geometry_verified": False,
               "supplemental_overlap_records": [{"supplement_area_id": "10013280006", "main_area_id": "10013130021", "geometry_eligible": False}]}
    meta = {"population_period": MONTH, "roster_period": MONTH, "boundary_vintage": VINTAGE,
            "same_month_geometry_verified": False, "national_audit_summary": summary}
    crosswalk = {"metadata": deepcopy(meta), "records": [
        cross("63000010001", "甲里", "verified", 10), cross("63000010S01", None, "unassigned", None, present=False),
        cross("10013280006", "三和村", "geometry_conflict", None, "Village_Sanhe", None),
        cross("10013130021", "黎明村", "geometry_conflict", 5),
    ]}
    features = []
    for code, name, status, count, ages, note in (
        ("63000010001", "甲里", "verified", 10, poprows[0]["age_counts"], None),
        ("63000010S01", None, "unassigned", None, None, "未編定村里"),
    ):
        features.append({"type": "Feature", "id": code, "properties": {
            "area_id": code, "boundary_id": code, "county_name": "臺北市", "district_name": "中正區", "area_name": "臺北市中正區",
            "village_name": name, "boundary_village_name": name, "boundary_join_status": status, "population": count,
            "age_counts": ages, "boundary_vintage": VINTAGE, "source_note": note,
            "age_groups": {"age_0_14": 0, "age_15_64": 0, "age_65_plus": 10,
                           "age_0_5": 0, "age_6_17": 0, "age_18_64": 0, "age_65_74": 0,
                           "age_75_plus": 10} if ages is not None else {
                               key: None for key in ("age_0_14", "age_15_64", "age_65_plus", "age_0_5", "age_6_17", "age_18_64", "age_65_74", "age_75_plus")},
        }, "geometry": mapping(boundaries[code]["geometry"])})
    return [roster, boundaries, layers, {"metadata": {"period": MONTH}, "records": poprows},
            {"metadata": deepcopy(meta), "records": list(roster.values())}, crosswalk,
            {"type": "FeatureCollection", "metadata": deepcopy(meta), "features": features}, MONTH]


@unittest.skipIf(box is None, "Run geometry audit tests with the project GIS dependencies")
class IndependentVillageGeographyReviewTests(unittest.TestCase):
    def test_valid_independent_fixture_preserves_null_and_supplement_exclusion(self):
        report = inspect_outputs(*fixture())
        self.assertEqual(report["failures"], [])
        self.assertEqual(report["excluded_conflicting_codes"], ["10013130021", "10013280006"])

    def test_same_count_deleted_and_added_registry_id_cannot_pass(self):
        args = fixture()
        args[4]["records"][0] = {**args[4]["records"][0], "village_code": "63000010002"}
        self.assertTrue(any("registry output differs" in error for error in inspect_outputs(*args)["failures"]))

    def test_same_count_crosswalk_wrong_id_is_detected_by_set_comparison(self):
        args = fixture()
        args[5]["records"][0]["area_id"] = "63000010002"
        self.assertTrue(any("complete ID union differs" in error for error in inspect_outputs(*args)["failures"]))

    def test_undated_sanhe_cannot_borrow_main_vintage_or_be_eligible(self):
        args = fixture()
        args[5]["records"][2].update({"boundary_vintage": VINTAGE, "allocation_eligible": True})
        errors = inspect_outputs(*args)["failures"]
        self.assertTrue(any("boundary_vintage differs" in error for error in errors))
        self.assertTrue(any("allocation_eligible differs" in error for error in errors))

    def test_literal_name_and_main_vintage_tampering_fail(self):
        args = fixture()
        args[5]["records"][0]["boundary_name"] = "乙里"
        args[6]["metadata"]["boundary_vintage"] = "2026-09-01"
        errors = inspect_outputs(*args)["failures"]
        self.assertTrue(any("boundary_name differs" in error for error in errors))
        self.assertTrue(any("main boundary vintage differs" in error for error in errors))

    def test_equal_population_total_does_not_hide_swapped_100_plus_bin(self):
        args = fixture()
        args[6]["features"][0]["properties"]["age_counts"] = [10] + [0] * 100
        self.assertTrue(any("101 source age cells differ" in error for error in inspect_outputs(*args)["failures"]))

    def test_geometry_change_and_unknown_population_zero_filling_fail(self):
        args = fixture()
        args[6]["features"][0]["geometry"] = mapping(box(121.4, 25, 121.50001, 25.1))
        args[5]["records"][2]["source_population"] = 0
        args[6]["features"][1]["properties"]["population"] = 0
        errors = inspect_outputs(*args)["failures"]
        self.assertTrue(any("full geometry coordinates differ" in error for error in errors))
        self.assertTrue(any("source_population differs" in error for error in errors))
        self.assertTrue(any("101 source age cells differ" in error for error in errors))


class IndependentRegistryRawTests(unittest.TestCase):
    def test_raw_registry_preserves_original_unicode_and_rejects_parent_or_letter_code(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "roster.txt"
            path.write_text("65000120027,65000120,濓新里\n", encoding="utf-8")
            self.assertEqual(registry_rows(path)["65000120027"]["village_name"], "濓新里")
            for text in ("65000120027,65000121,濓新里\n", "65000120S01,65000120,未編定\n"):
                path.write_text(text, encoding="utf-8")
                with self.assertRaises(ValueError):
                    registry_rows(path)


@unittest.skipIf(box is None, "Run geometry audit tests with the project GIS dependencies")
class IndependentStationRecomputeTests(unittest.TestCase):
    def test_complete_measured_zero_and_missing_population_are_distinct(self):
        row = {"geometry": box(-1000, -1000, 1000, 1000), "verified": True, "conflict": False,
               "population": 0, **{key: 0 for key in GROUP_INTERVALS}}
        zero = recompute_circle([row], (0, 0), 100)
        self.assertEqual(zero["status"], "complete")
        self.assertEqual(zero["population_estimate"], 0)
        missing = recompute_circle([{**row, "population": None}], (0, 0), 100)
        self.assertIsNone(missing["population_estimate"])
        self.assertTrue(all(value is None for value in missing["age_group_estimates"].values()))

    def test_unmatched_candidate_is_excluded_and_union_coverage_remains_explicit(self):
        left = {"geometry": box(-1000, -1000, 0, 1000), "verified": True, "conflict": False,
                "population": 100, **{key: 10 for key in GROUP_INTERVALS}}
        right = {**left, "geometry": box(0, -1000, 1000, 1000), "verified": False, "population": 9999999}
        result = recompute_circle([left, right], (0, 0), 100)
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["boundary_share"], 1)
        self.assertEqual(result["population_data_share"], .5)
        self.assertEqual(result["unmatched_village_count"], 1)
        self.assertIsNone(result["population_estimate"])
        self.assertLess(result["population_known_estimate"], 10)


if __name__ == "__main__":
    unittest.main()
