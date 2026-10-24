"""An independent audit must reject plausible same-count geography tampering."""
from copy import deepcopy
import gzip
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

try:
    from shapely.geometry import box, mapping
except ImportError:
    box = None

from scripts.review_village_age_geography import (
    GROUP_INTERVALS, fnv1a32_utf8, gzip_copy_matches, inspect_outputs, inspect_station_shards,
    population_source_snapshot, recompute_circle, registry_rows, station_points_from_layers,
)
from scripts import review_village_age_geography as audit

MONTH = "2026-09"
MAIN = "VILLAGE_NLSC_1150817"
VINTAGE = "2026-08-17"
POPULATION_SOURCE = {"raw_file": "raw_data/moi_village_age/moi_village_age_2026-09.csv",
                     "requested_url": "https://www.ris.gov.tw/fixture-population.csv",
                     "downloaded_at": "2026-10-24T10:00:00+08:00", "http_status": 200,
                     "bytes": 1234, "source_data_time": None, "http_last_modified": None}


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
            "same_month_geometry_verified": False, "national_audit_summary": summary,
            "population_source": deepcopy(POPULATION_SOURCE), "generated_at": "2026-10-24T12:00:00+08:00",
            "sources": [{"raw_file": "map-data/raw/national_village_age/villages.zip"}]}
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
    return [roster, boundaries, layers, {"metadata": {"period": MONTH, "source": deepcopy(POPULATION_SOURCE)}, "records": poprows},
            {"metadata": deepcopy(meta), "records": list(roster.values())}, crosswalk,
            {"type": "FeatureCollection", "metadata": deepcopy(meta), "features": features}, MONTH]


@unittest.skipIf(box is None, "Run geometry audit tests with the project GIS dependencies")
class IndependentVillageGeographyReviewTests(unittest.TestCase):
    def test_failed_review_still_emits_each_reviewed_document_receipt(self):
        documents = fixture()[4:7]
        with patch.object(audit, "read", side_effect=[*documents, OSError("missing source")]):
            result = audit.review(Path("/unused-fixture-root"), MONTH)
        self.assertEqual(result["metadata"]["status"], "needs_correction")
        self.assertEqual(len(result["records"]), 3)
        for document, record in zip(documents, result["records"]):
            self.assertEqual(record["population_source"], document["metadata"]["population_source"])
            self.assertIsNot(record["population_source"], document["metadata"]["population_source"])
            document["metadata"]["population_source"]["downloaded_at"] = "changed"
            self.assertEqual(record["population_source"], POPULATION_SOURCE)

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

    def test_population_receipt_change_is_detected_with_identical_geography_snapshot(self):
        for changed in (3, 4, 5, 6):  # Population input, then each delivered document.
            args = fixture()
            geography_snapshots = [(deepcopy(doc["metadata"]["sources"]), doc["metadata"]["generated_at"])
                                   for doc in args[4:7]]
            key = "source" if changed == 3 else "population_source"
            args[changed]["metadata"][key]["downloaded_at"] = "2026-10-25T10:00:00+08:00"
            with self.subTest(changed_document=changed):
                errors = inspect_outputs(*args)["failures"]
                self.assertTrue(any("population source snapshot differs" in error for error in errors))
                self.assertEqual(geography_snapshots, [(doc["metadata"]["sources"], doc["metadata"]["generated_at"])
                                                      for doc in args[4:7]])

    def test_equal_missing_or_invalid_population_receipts_do_not_pass(self):
        for value in (None, {}, []):
            args = fixture()
            args[3]["metadata"]["source"] = deepcopy(value)
            for document in args[4:7]:
                document["metadata"]["population_source"] = deepcopy(value)
            with self.subTest(value=value):
                self.assertTrue(any("receipt is missing or invalid" in error for error in inspect_outputs(*args)["failures"]))
        args = fixture()
        args[3]["metadata"].pop("source")
        for document in args[4:7]:
            document["metadata"].pop("population_source")
        self.assertTrue(any("receipt is missing or invalid" in error for error in inspect_outputs(*args)["failures"]))


class ReviewedPopulationSourceSnapshotTests(unittest.TestCase):
    def test_actual_reviewed_metadata_receipt_is_preserved_including_valid_nulls(self):
        metadata = {"population_source": deepcopy(POPULATION_SOURCE)}
        snapshot = population_source_snapshot(metadata)
        self.assertEqual(snapshot, POPULATION_SOURCE)
        self.assertIn("source_data_time", snapshot)
        self.assertIsNone(snapshot["source_data_time"])
        self.assertIsNone(snapshot["http_last_modified"])
        metadata["population_source"]["downloaded_at"] = "2026-10-25T10:00:00+08:00"
        self.assertEqual(snapshot["downloaded_at"], POPULATION_SOURCE["downloaded_at"])


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


class IndependentStationIdentityUnionTests(unittest.TestCase):
    def layers(self):
        def feature(code, coordinates, key="id"):
            return {"properties": {key: code}, "geometry": {"type": "Point", "coordinates": coordinates}}
        return {
            "bus_stops.geojson": {"metadata": {}, "features": [feature("001", [121.5, 25.0])]},
            "regional_bus_stops.geojson": {"metadata": {}, "features": [
                feature("NWT001@121.51,25.01", [121.51, 25.01]),
                feature("NWT001@121.52,25.02", [121.52, 25.02])]},
            "mrt_stations.geojson": {"metadata": {}, "features": [feature("001", [121.53, 25.03])]},
            "youbike_stations.geojson": {"metadata": {}, "features": [feature("001", [121.54, 25.04], "station_id")]},
        }

    def test_exact_local_regional_union_keeps_coordinate_variants_and_namespaces(self):
        inventory = station_points_from_layers(self.layers())
        self.assertEqual(inventory["errors"], [])
        self.assertEqual(set(inventory["points"]["bus"]), {"001", "NWT001@121.51,25.01", "NWT001@121.52,25.02"})
        self.assertEqual(inventory["points"]["bus"]["NWT001@121.52,25.02"], [121.52, 25.02])
        self.assertEqual(set(inventory["points"]["mrt"]), {"001"})
        self.assertEqual(set(inventory["points"]["youbike"]), {"001"})
        self.assertEqual(inventory["source_counts"], {"bus_stops": 1, "regional_bus_stops": 2, "mrt": 1, "youbike": 1})

    def test_cross_source_id_collision_is_not_silently_overwritten(self):
        layers = self.layers()
        layers["regional_bus_stops.geojson"]["features"][0]["properties"]["id"] = "001"
        inventory = station_points_from_layers(layers)
        self.assertTrue(any("collision within namespace bus: 001" in error for error in inventory["errors"]))
        self.assertEqual(inventory["points"]["bus"]["001"], [121.5, 25.0])

    def test_duplicate_regional_id_or_invalid_point_fails_independent_inventory(self):
        layers = self.layers()
        layers["regional_bus_stops.geojson"]["features"].append(deepcopy(layers["regional_bus_stops.geojson"]["features"][0]))
        inventory = station_points_from_layers(layers)
        self.assertTrue(any("duplicate displayed original station ID" in error for error in inventory["errors"]))
        layers = self.layers()
        layers["regional_bus_stops.geojson"]["features"][0]["geometry"]["coordinates"] = [121.5, float("nan")]
        self.assertTrue(any("invalid displayed WGS84" in error for error in station_points_from_layers(layers)["errors"]))


class IndependentStationShardTests(unittest.TestCase):
    def test_gzip_copy_must_be_complete_and_identical(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "part-00.json"
            compressed = path.with_name(path.name + ".gz")
            path.write_bytes(b'{"station": 1}')
            compressed.write_bytes(gzip.compress(path.read_bytes()))
            self.assertTrue(gzip_copy_matches(path))
            compressed.write_bytes(gzip.compress(b'{"station": 2}'))
            self.assertFalse(gzip_copy_matches(path))
            compressed.write_bytes(gzip.compress(path.read_bytes())[:-5])
            self.assertFalse(gzip_copy_matches(path))

    def parts(self):
        canonical = {"schema_version": 1, "dataset_id": "web_station_age_catchments", "radii": [200, 350, 500],
                     "metadata": {"built_at": "2026-10-24T12:00:00+00:00", "sources": [{"raw_file": "source.zip"}]},
                     "records": {"bus": {"001": [{"population_estimate": 0}], "NWT001:121.5:25.0": [{"population_estimate": None}]},
                                 "mrt": {"001": [{"population_estimate": 123.45}]}, "youbike": {"臺北站": [{"population_estimate": 5}]}}}
        parts = {}
        for index in range(32):
            parts[f"part-{index:02d}.json"] = {**deepcopy(canonical), "records": {"bus": {}, "mrt": {}, "youbike": {}}}
            parts[f"part-{index:02d}.json"]["metadata"]["shard"] = {
                "algorithm": "fnv1a32-utf8-mod32", "index": index, "count": 32, "station_count": 0}
        for kind, stations in canonical["records"].items():
            for code, entries in stations.items():
                part = parts[f"part-{fnv1a32_utf8(kind + ':' + code) % 32:02d}.json"]
                part["records"][kind][code] = deepcopy(entries)
                part["metadata"]["shard"]["station_count"] += 1
        return canonical, parts

    def test_bytewise_hash_matches_standard_and_independent_node_vectors(self):
        for value, expected in (("", 2166136261), ("a", 3826002220), ("foobar", 3214735720),
                                ("bus:100", 3602010264), ("mrt:100", 1274992703),
                                ("bus:臺北站", 1291106125), ("bus:😀", 2385802468)):
            self.assertEqual(fnv1a32_utf8(value), expected)

    def test_all_32_shards_have_exact_union_values_and_metadata(self):
        canonical, parts = self.parts()
        review = inspect_station_shards(canonical, parts.items())
        self.assertEqual(review["failures"], [])
        self.assertEqual(review["unique_shard_station_count"], 4)

    def test_wrong_routing_even_with_correct_counts_and_union_is_detected(self):
        canonical, parts = self.parts()
        source = fnv1a32_utf8("bus:001") % 32
        wrong = (source + 1) % 32
        entry = parts[f"part-{source:02d}.json"]["records"]["bus"].pop("001")
        parts[f"part-{source:02d}.json"]["metadata"]["shard"]["station_count"] -= 1
        parts[f"part-{wrong:02d}.json"]["records"]["bus"]["001"] = entry
        parts[f"part-{wrong:02d}.json"]["metadata"]["shard"]["station_count"] += 1
        self.assertTrue(any("wrong shard" in error for error in inspect_station_shards(canonical, parts.items())["failures"]))

    def test_duplicate_entry_stale_metadata_and_changed_value_fail(self):
        canonical, parts = self.parts()
        source = fnv1a32_utf8("bus:001") % 32
        wrong = (source + 1) % 32
        parts[f"part-{wrong:02d}.json"]["records"]["bus"]["001"] = [{"population_estimate": 999}]
        parts[f"part-{wrong:02d}.json"]["metadata"]["shard"]["station_count"] += 1
        parts["part-00.json"]["metadata"]["built_at"] = "stale"
        errors = inspect_station_shards(canonical, parts.items())["failures"]
        self.assertTrue(any("repeated across shards" in error for error in errors))
        self.assertTrue(any("entries differ from canonical" in error for error in errors))
        self.assertTrue(any("snapshot differs" in error for error in errors))

    def test_missing_part_or_station_cannot_pass_equal_counts_elsewhere(self):
        canonical, parts = self.parts()
        parts.pop("part-31.json")
        errors = inspect_station_shards(canonical, parts.items())["failures"]
        self.assertTrue(any("32 shard filenames" in error for error in errors))


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
