import copy
import unittest
from unittest.mock import patch

from scripts.build_web_supplemental import coordinate, hourly_windows, traffic_snapshot_join, villages


class SourceWindowTests(unittest.TestCase):
    def test_source_windows_preserve_missing_zero_and_2400(self):
        values, conflicts = hourly_windows([
            {"start_time": "7:30", "end_time": "9:00", "run_time": 45},
            {"start_time": "10:00", "end_time": "11:00", "run_time": 0},
            {"start_time": "23:00", "end_time": "24:00", "run_time": 90},
        ])
        self.assertIsNone(values[7])
        self.assertEqual(values[8], 45)
        self.assertIsNone(values[9])
        self.assertEqual(values[10], 0)
        self.assertEqual(values[23], 90)
        self.assertEqual(conflicts, 0)

    def test_overlapping_source_conflicts_stay_unknown(self):
        values, conflicts = hourly_windows([
            {"start_time": "8:00", "end_time": "9:00", "run_time": 45},
            {"start_time": "8:00", "end_time": "9:00", "run_time": 45},
            {"start_time": "8:00", "end_time": "10:00", "run_time": 70},
        ])
        self.assertIsNone(values[8])
        self.assertEqual(values[9], 70)
        self.assertEqual(conflicts, 1)

    def test_invalid_or_placeholder_runtime_never_enters_hourly_cost(self):
        values, _ = hourly_windows([
            {"start_time": "8:00", "end_time": "9:00", "run_time": value}
            for value in (None, -99, True, float("nan"), float("inf"))
        ] + [{"start_time": "25:00", "end_time": "26:00", "run_time": 12}])
        self.assertEqual(values, [None] * 24)


class TrafficSnapshotIdentityTests(unittest.TestCase):
    def setUp(self):
        self.definition = {
            "metadata": {"response_metadata": {
                "authority_code": "TPE", "update_time": "2026-10-24T02:40:46+08:00",
                "src_update_time": "2026-10-24T00:00:00+08:00"}},
            "records": [{"section_id": "A", "link_ids": [{"link_id": "L1"}]}],
        }
        self.observation = {
            "metadata": {"response_metadata": {
                "authority_code": "TPE", "update_time": "2026-10-24T10:51:52+08:00",
                "src_update_time": "2026-10-24T10:50:30+08:00"}},
            "records": [{"section_id": "A", "link_ids": [{"link_id": "L1"}],
                         "data_collect_time": "2026-10-24T10:50:00+08:00", "travel_speed": 42}],
        }

    def test_exact_bounded_snapshot_is_joined(self):
        index, checks = traffic_snapshot_join(self.definition, self.observation)
        self.assertEqual(index["A"]["travel_speed"], 42)
        self.assertTrue(all(checks.values()))

    def test_each_identity_failure_blocks_every_join(self):
        mutations = [
            lambda d: d["metadata"]["response_metadata"].update(authority_code="NWT"),
            lambda d: d["metadata"]["response_metadata"].update(update_time="2026-10-23T10:51:52+08:00"),
            lambda d: d["metadata"]["response_metadata"].update(update_time="2026-10-24T10:51:52"),
            lambda d: d["records"].append(copy.deepcopy(d["records"][0])),
            lambda d: d["records"][0].update(section_id="B"),
            lambda d: d["records"][0].update(link_ids=[]),
            lambda d: d["records"][0].update(link_ids=[{"link_id": "L2"}]),
            lambda d: d["records"][0].update(data_collect_time="2026-10-23T10:50:00+08:00"),
        ]
        for mutate in mutations:
            with self.subTest(mutation=mutate):
                observation = copy.deepcopy(self.observation)
                mutate(observation)
                index, checks = traffic_snapshot_join(self.definition, observation)
                self.assertEqual(index, {})
                self.assertFalse(all(checks.values()))

    def test_coordinate_validation_does_not_invent_missing_endpoints(self):
        self.assertIsNone(coordinate({"stop_position": {"position_lon": 121.5}}))
        self.assertIsNone(coordinate({"stop_position": {"position_lon": 121.5, "position_lat": float("nan")}}))
        self.assertEqual(coordinate({"stop_position": {"position_lon": 121.5, "position_lat": 25}}), [121.5, 25])


class VillageIdentityTests(unittest.TestCase):
    def documents(self):
        people = {"source_area_code": "A", "population": 100, "households": 50,
                  "population_age_0_14": 10, "population_age_15_64": 60,
                  "population_age_65_plus": 30, "period": "2026-09"}
        link = {"population_area_code": "A", "boundary_village_code": "A", "population": 100,
                "status": "matched_code_and_names", "population_period": "2026-09"}
        boundaries = [{"type": "Feature", "properties": {"admin_code": code, "name": code,
                       "city_code": "63000", "city_name": "臺北市", "district_name": "測試區"},
                       "geometry": {"type": "Polygon", "coordinates": [[[121, 25], [121.1, 25], [121.1, 25.1], [121, 25]]]}}
                      for code in ("A", "B", "C")]
        return {
            "population_2026-09.json": {"metadata": {"period": "2026-09", "license": {}}, "records": [people]},
            "population_boundary_crosswalk.json": {
                "metadata": {"boundary_source_period": "2026-08-17"},
                "records": [link, {**link, "boundary_village_code": "B", "status": "matched_code_name_variant_requires_review"}],
            },
            "villages.geojson": {"metadata": {}, "features": boundaries},
            "keelung_villages.geojson": {"metadata": {}, "features": []},
        }

    def test_variant_name_and_boundary_only_do_not_borrow_population(self):
        docs = self.documents()
        with patch("scripts.build_web_supplemental.read", side_effect=lambda root, path: docs[path.split("/")[-1]]):
            result = villages(None)
        props = [f["properties"] for f in result["features"]]
        self.assertEqual(props[0]["population"], 100)
        self.assertEqual(props[0]["elderly_share"], 0.3)
        for entry in props[1:]:
            self.assertIsNone(entry["population"])
            self.assertIsNone(entry["population_age_65_plus"])
            self.assertIsNone(entry["elderly_share"])

    def test_changed_population_does_not_reuse_stale_crosswalk(self):
        docs = self.documents()
        docs["population_2026-09.json"]["records"][0]["population"] = 101
        with patch("scripts.build_web_supplemental.read", side_effect=lambda root, path: docs[path.split("/")[-1]]):
            with self.assertRaisesRegex(ValueError, "crosswalk drifted"):
                villages(None)


if __name__ == "__main__":
    unittest.main()
