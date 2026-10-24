"""Geometry and missing-data acceptance checks for estimated station age populations."""
from copy import deepcopy
import math
from pathlib import Path
import sys
import unittest

from pyproj import Transformer
from shapely import transform
from shapely.geometry import box, mapping

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from build_age_catchments import AGE_GROUPS, AGE_GROUP_SETS, build_age_catchments


TO_TM = Transformer.from_crs(4326, 3826, always_xy=True)
TO_WGS = Transformer.from_crs(3826, 4326, always_xy=True)
X, Y = TO_TM.transform(121.5, 25.0)
CENTRE = [121.5, 25.0]


def feature(code, rectangle, *, population=10100, ages=None, status="verified", boundary_id=None):
    """Define test shapes in physical metres, then use the public WGS84 interface."""
    west, south, east, north = rectangle
    geometry = box(X + west, Y + south, X + east, Y + north)
    return {"type": "Feature", "properties": {
        "area_id": code, "boundary_id": boundary_id or code, "boundary_join_status": status,
        "population": population, "age_counts": [100] * 101 if ages is None else ages,
    }, "geometry": mapping(transform(geometry, TO_WGS.transform, interleaved=False))}


def collection(*features):
    return {"type": "FeatureCollection", "metadata": {
        "population_period": "2026-09", "roster_period": "2026-09", "boundary_vintage": "2026-08-17",
        "same_month_geometry_verified": False, "source": {"url": "https://data.gov.tw/dataset/77132"},
        "license": {"name": "政府資料開放授權條款第1版"}, "limitations": ["source limitation"],
    }, "features": list(features)}


def entry(boundaries, point=CENTRE, radius=100):
    return build_age_catchments({"bus": {"001": point}}, boundaries, (radius,))["records"]["bus"]["001"][0]


class AgeCatchmentGeometryTests(unittest.TestCase):
    def test_complete_circle_uses_square_density_and_physical_area(self):
        # A 2 km × 2 km village with 10,100 people has a known uniform density.
        result = entry(collection(feature("63000010001", (-1000, -1000, 1000, 1000))))
        expected = 10100 * math.pi * 100 ** 2 / 2000 ** 2
        self.assertEqual(result["status"], "complete")
        self.assertAlmostEqual(result["population_estimate"], expected, delta=0.02)
        self.assertEqual(result["population_estimate"], result["population_known_estimate"])
        self.assertEqual((result["boundary_share"], result["data_share"], result["population_data_share"]), (1, 1, 1))
        # Each exact-age bin has the same density: 15 bins belong to 0–14.
        self.assertAlmostEqual(result["age_group_estimates"]["age_0_14"], expected * 15 / 101, delta=0.01)

    def test_adjacent_villages_form_complete_circle_without_edge_conflict(self):
        result = entry(collection(
            feature("63000010001", (-1000, -1000, 0, 1000), population=10100),
            feature("65000010001", (0, -1000, 1000, 1000), population=20200, ages=[200] * 101),
        ))
        expected = math.pi * 100 ** 2 / 2 * (10100 / 2e6 + 20200 / 2e6)
        self.assertEqual(result["status"], "complete")
        self.assertAlmostEqual(result["population_estimate"], expected, delta=0.03)
        self.assertEqual(result["geometry_conflict_village_count"], 0)
        self.assertEqual(result["village_count"], 2)

    def test_coast_or_missing_boundary_is_partial_and_is_not_zero_filled(self):
        result = entry(collection(feature("63000010001", (-1000, -1000, 0, 1000))))
        self.assertIsNone(result["population_estimate"])
        self.assertAlmostEqual(result["population_known_estimate"], 10100 * math.pi * 100 ** 2 / 4e6, delta=0.02)
        self.assertEqual(result["status"], "partial")
        self.assertAlmostEqual(result["data_share"], 0.5, places=6)
        self.assertAlmostEqual(result["outside_boundary_share"], 0.5, places=6)
        self.assertEqual(result["unknown_village_count"], 0)  # outside geography is separately reported

    def test_unmatched_candidate_population_is_never_used(self):
        left = feature("63000010001", (-1000, -1000, 0, 1000))
        right = feature("65000010001", (0, -1000, 1000, 1000), status="name_mismatch", population=999999999)
        result = entry(collection(left, right))
        self.assertEqual(result["boundary_share"], 1)
        self.assertEqual(result["unmatched_village_count"], 1)
        self.assertEqual(result["unknown_village_count"], 1)
        self.assertAlmostEqual(result["data_share"], 0.5, places=6)
        self.assertIsNone(result["population_estimate"])
        self.assertLess(result["population_known_estimate"], 100)
        self.assertEqual(result["age_group_unknown_village_counts"]["age_0_14"], 1)

    def test_unassigned_polygon_is_present_geography_but_unknown_population(self):
        result = entry(collection(feature("63000010000", (-1000, -1000, 1000, 1000), status="unassigned")))
        self.assertEqual(result["boundary_share"], 1)
        self.assertEqual(result["data_share"], 0)
        self.assertIsNone(result["population_estimate"])
        self.assertIsNone(result["population_known_estimate"])
        self.assertTrue(all(value is None for value in result["age_group_known_estimates"].values()))

    def test_positive_area_overlap_quarantines_both_sides_and_coverage_is_union(self):
        doc = build_age_catchments({"bus": {"001": CENTRE}}, collection(
            feature("63000010001", (-1000, -1000, 50, 1000)),
            feature("65000010001", (-50, -1000, 1000, 1000)),
        ), (100,))
        result = doc["records"]["bus"]["001"][0]
        self.assertEqual(result["status"], "geometry_conflict")
        self.assertEqual(result["boundary_share"], 1)  # summing clipped areas would exceed one
        self.assertEqual(result["data_share"], 0)
        self.assertEqual(result["geometry_conflict_village_count"], 2)
        self.assertIsNone(result["population_known_estimate"])
        self.assertEqual(doc["metadata"]["quality"]["overlap_pair_count"], 1)

    def test_no_boundary_produces_null_estimates_and_explicit_outside_share(self):
        result = entry(collection())
        self.assertEqual(result["status"], "no_boundary")
        self.assertEqual(result["outside_boundary_share"], 1)
        self.assertIsNone(result["population_known_estimate"])
        self.assertEqual(result["village_count"], 0)

    def test_tiny_village_near_circle_edge_is_not_lost_by_candidate_search(self):
        angle = math.pi / 64
        dx, dy = 99.95 * math.cos(angle), 99.95 * math.sin(angle)
        # This falls between the chords of a coarse 64-sided search circle and
        # the 256-sided measurement circle. Its complete 2 cm square is inside.
        result = entry(collection(feature("63000010001", (dx - .01, dy - .01, dx + .01, dy + .01))))
        self.assertEqual(result["village_count"], 1)
        self.assertEqual(result["population_known_estimate"], 10100)
        self.assertIsNone(result["population_estimate"])

    def test_source_metadata_and_station_id_namespaces_survive(self):
        boundaries = collection(feature("63000010001", (-1000, -1000, 1000, 1000)))
        unchanged = deepcopy(boundaries)
        doc = build_age_catchments({kind: {"001": CENTRE} for kind in ("bus", "mrt", "youbike")}, boundaries)
        self.assertEqual(doc["radii"], [200, 350, 500])
        self.assertEqual(doc["metadata"]["population_period"], "2026-09")
        self.assertEqual(doc["metadata"]["boundary_vintage"], "2026-08-17")
        self.assertFalse(doc["metadata"]["same_month_geometry_verified"])
        self.assertEqual(set(doc["records"]), {"bus", "mrt", "youbike"})
        self.assertEqual(doc["metadata"]["quality"]["station_count"], 3)
        self.assertEqual(boundaries, unchanged)


class AgeCatchmentUnknownAndAgeTests(unittest.TestCase):
    def test_missing_published_population_cannot_be_complete_despite_known_ages(self):
        result = entry(collection(feature("63000010001", (-1000, -1000, 1000, 1000), population=None)))
        self.assertEqual(result["data_share"], 1)
        self.assertEqual(result["population_data_share"], 0)
        self.assertIsNone(result["population_estimate"])
        self.assertIsNone(result["population_known_estimate"])
        self.assertEqual(result["missing_population_village_count"], 1)
        self.assertTrue(all(value is None for value in result["age_group_estimates"].values()))
        self.assertGreater(result["age_group_known_estimates"]["age_0_14"], 0)

    def test_one_missing_age_propagates_only_to_groups_that_include_it(self):
        ages = [100] * 101
        ages[100] = None
        result = entry(collection(feature("63000010001", (-1000, -1000, 1000, 1000), ages=ages)))
        self.assertIsNotNone(result["population_estimate"])
        for key in ("age_65_plus", "age_75_plus"):
            self.assertIsNone(result["age_group_estimates"][key])
            self.assertIsNone(result["age_group_known_estimates"][key])
            self.assertEqual(result["age_group_data_shares"][key], 0)
            self.assertEqual(result["age_group_unknown_village_counts"][key], 1)
        for key in ("age_0_14", "age_15_64", "age_65_74"):
            self.assertIsNotNone(result["age_group_estimates"][key])
            self.assertEqual(result["age_group_data_shares"][key], 1)
        self.assertEqual(result["status"], "partial")

    def test_group_known_partial_keeps_only_the_other_villages_contribution(self):
        missing = [100] * 101
        missing[100] = None
        result = entry(collection(
            feature("63000010001", (-1000, -1000, 0, 1000), ages=missing),
            feature("65000010001", (0, -1000, 1000, 1000)),
        ))
        self.assertIsNotNone(result["population_estimate"])
        self.assertIsNone(result["age_group_estimates"]["age_75_plus"])
        self.assertAlmostEqual(result["age_group_data_shares"]["age_75_plus"], .5, places=6)
        self.assertAlmostEqual(result["age_group_known_estimates"]["age_75_plus"],
                               26 * 100 * math.pi * 100 ** 2 / 4e6, delta=.01)
        self.assertEqual(result["age_group_unknown_village_counts"]["age_75_plus"], 1)

    def test_100_plus_is_included_once_and_age_group_sets_partition_separately(self):
        ages = [0] * 101
        ages[100] = 1000
        result = entry(collection(feature("63000010001", (-1000, -1000, 1000, 1000), population=1000, ages=ages)))
        values = result["age_group_estimates"]
        self.assertEqual(values["age_65_plus"], result["population_estimate"])
        self.assertEqual(values["age_75_plus"], result["population_estimate"])
        self.assertEqual(values["age_65_74"], 0)
        for keys in AGE_GROUP_SETS.values():
            self.assertAlmostEqual(sum(values[key] for key in keys), result["population_estimate"], delta=0.02)
        self.assertAlmostEqual(sum(values.values()), result["population_estimate"] * 2, delta=0.02)
        self.assertEqual({group["key"] for group in AGE_GROUPS}, set(values))

    def test_observed_zero_population_is_preserved_in_complete_circle(self):
        result = entry(collection(feature("63000010001", (-1000, -1000, 1000, 1000), population=0, ages=[0] * 101)))
        self.assertEqual(result["status"], "complete")
        self.assertEqual(result["population_estimate"], 0)
        self.assertEqual(result["population_known_estimate"], 0)
        self.assertTrue(all(value == 0 for value in result["age_group_estimates"].values()))

    def test_explicit_verified_alias_is_used_without_guessing_names(self):
        result = entry(collection(feature("63000010001", (-1000, -1000, 1000, 1000), status="verified_alias")))
        self.assertEqual(result["status"], "complete")

    def test_verified_wrong_code_or_repeated_population_is_rejected(self):
        boundary = feature("63000010001", (-1000, -1000, 1000, 1000))
        boundary["properties"]["population_area_id"] = "65000010001"
        with self.assertRaisesRegex(ValueError, "disagrees"):
            entry(collection(boundary))
        with self.assertRaisesRegex(ValueError, "11 digits"):
            entry(collection(feature("123", (-1000, -1000, 1000, 1000))))
        with self.assertRaisesRegex(ValueError, "Repeated verified"):
            entry(collection(feature("63000010001", (-1000, -1000, 0, 1000)),
                             feature("63000010001", (0, -1000, 1000, 1000), boundary_id="part2")))

    def test_bad_age_values_and_inconsistent_population_are_rejected(self):
        for ages in ([1] * 100, [True] + [100] * 100, [float("nan")] + [100] * 100):
            with self.subTest(ages=ages[:2]):
                with self.assertRaises(ValueError):
                    entry(collection(feature("63000010001", (-1000, -1000, 1000, 1000), ages=ages)))
        with self.assertRaisesRegex(ValueError, "disagree"):
            entry(collection(feature("63000010001", (-1000, -1000, 1000, 1000), population=100)))

    def test_far_offshore_and_nonfinite_points_are_not_silently_projected(self):
        for point in ([118.3, 24.4], [119.5, 23.5], [121.5, float("nan")]):
            with self.subTest(point=point):
                with self.assertRaisesRegex(ValueError, "projection scope|Invalid WGS84"):
                    entry(collection(), point=point)
        far = feature("63000010001", (-1000, -1000, 1000, 1000))
        far["geometry"] = mapping(box(118.2, 24.3, 118.4, 24.5))
        with self.assertRaisesRegex(ValueError, "projection scope"):
            entry(collection(far))

    def test_official_nearby_keelung_islet_block_remains_in_northern_scope(self):
        island = feature("10017010S01", (-1000, -1000, 1000, 1000), status="unassigned")
        island["geometry"] = mapping(box(122.075, 25.625, 122.08, 25.63))
        result = entry(collection(island), point=[122.0775, 25.6275])
        self.assertEqual(result["village_count"], 1)
        self.assertEqual(result["unmatched_village_count"], 1)
        self.assertIsNone(result["population_estimate"])


if __name__ == "__main__":
    unittest.main()
