"""Synthetic north-region frequency tests; no TDX requests and no fake ridership."""
import gzip
import json
from pathlib import Path
import tempfile
import unittest

from shapely.geometry import box, mapping

from scripts import build_regional_bus_grid as grid

CAL = {d: 1 for d in grid.WEEK}
WEEKDAYS = {d: int(d in {"monday", "tuesday", "wednesday", "thursday", "friday"}) for d in grid.WEEK}


def model(operator=None, *, trip="X1", departure="08:00"):
    return {
        "route_uid": "NWT0123", "sub_route_uid": "NWT012301", "direction": 0,
        "operator_id": operator,
        "timetables": [{
            "trip_id": trip, "service_day": dict(WEEKDAYS),
            "stop_times": [{"stop_id": "001", "departure_time": departure}],
        }],
        "frequencys": [],
    }


def frequency_model(operator=None, *, end="09:00"):
    row = model(operator)
    row["timetables"] = []
    row["frequencys"] = [{
        "service_day": dict(WEEKDAYS), "start_time": "08:00", "end_time": end,
        "min_headway_mins": 10, "max_headway_mins": 10,
    }]
    return row


class RegionalGridTests(unittest.TestCase):
    def test_timetables_do_not_double_count_duplicate_source_records(self):
        trips = grid.service_departures([model("OP1"), model("OP1")])
        self.assertEqual(trips["weekday"][8], 1)
        self.assertEqual(sum(trips["weekday"]), 1)
        self.assertEqual(sum(trips["saturday"]), 0)

    def test_operator_specific_trips_are_distinct(self):
        trips = grid.service_departures([model("OP1"), model("OP2")])
        self.assertEqual(trips["weekday"][8], 2)

    def test_no_or_invalid_calendar_is_unknown_not_zero(self):
        self.assertEqual(grid.service_departures([]), {d: None for d in grid.DAY_NAMES})
        bad = model()
        del bad["timetables"][0]["service_day"]["tuesday"]
        self.assertIsNone(grid.service_departures([bad])["weekday"])
        self.assertIsNone(grid.service_departures([bad])["saturday"])

    def test_missing_reference_departure_is_not_zero(self):
        bad = model(departure="")
        self.assertIsNone(grid.service_departures([bad])["weekday"])

    def test_fixed_departures_win_over_frequency_same_hour(self):
        row = model()
        row["frequencys"] = [{
            "service_day": WEEKDAYS, "start_time": "08:00", "end_time": "09:00",
            "min_headway_mins": 5, "max_headway_mins": 5,
        }]
        self.assertEqual(grid.service_departures([row])["weekday"][8], 1)

    def test_fixed_departures_do_not_override_other_operator_frequency(self):
        trips = grid.service_departures([model("OP1"), frequency_model("OP2")])
        self.assertEqual(trips["weekday"][8], 7)
        self.assertEqual(sum(trips["weekday"]), 7)
        self.assertEqual(sum(trips["saturday"]), 0)

    def test_fixed_departures_override_same_identity_frequency_across_models(self):
        for operator in ("OP1", None):
            with self.subTest(operator=operator):
                trips = grid.service_departures([model(operator), frequency_model(operator, end="10:00")])
                self.assertEqual(trips["weekday"][8], 1)
                self.assertEqual(trips["weekday"][9], 6)

    def test_mixed_operator_models_do_not_double_count_duplicate_sources(self):
        fixed = model("OP1")
        frequency = frequency_model("OP2")
        trips = grid.service_departures([fixed, frequency, fixed, frequency])
        self.assertEqual(trips["weekday"][8], 7)
        self.assertEqual(sum(trips["weekday"]), 7)

    def test_grid_deduplicates_stops_and_services_and_retains_unknown(self):
        region = box(121.4, 25.0, 121.42, 25.02)
        route_stops = {
            ("NWT01", "NWT011", "0"): {("S01", 121.410, 25.010), ("S01", 121.410, 25.010)},
            ("KEE01", "KEE011", "1"): {("S02", 121.410, 25.010)},
        }
        known = {day: [1.0] * 24 for day in grid.DAY_NAMES}
        services = {
            ("NWT01", "NWT011", "0"): (known, False),
            ("KEE01", "KEE011", "1"): ({day: None for day in grid.DAY_NAMES}, True),
        }
        output, statuses, _ = grid.make_grid(region, route_stops, services, 500)
        marked = [r for r in output["cells"] if r[3] == 2]
        self.assertEqual(len(marked), 1)
        self.assertEqual(marked[0][2:5], [2, 2, 1])
        self.assertEqual(marked[0][5][0], 1.0)
        self.assertGreater(statuses.get("no_stop", 0), 0)

    def test_complete_publication_is_required_and_anonymous_only_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dest = root / "data/official_cross_city/service_coverage"
            dest.mkdir(parents=True)
            key = ("NWT0123", "NWT012301", 0)
            stops = [{
                "route_uid": key[0], "sub_route_uid": key[1], "direction": key[2],
                "operators": [{"operator_id": "1"}, {"operator_id": "2"}],
                "stops": [{"stop_uid": "NT001", "stop_position": {"position_lon": 121.5, "position_lat": 25.05}}],
            }]
            for scope in grid.SCOPES:
                for kind in ("StopOfRoute", "Schedule"):
                    rows = stops if kind == "StopOfRoute" and scope == "NewTaipei" else (
                        [model()] if kind == "Schedule" and scope == "NewTaipei" else [])
                    doc = {
                        "metadata": {"scope_status": "complete_published_endpoint_by_exhausted_page",
                                     "source_updated_at": None, "sources": []},
                        "records": rows,
                    }
                    (dest / f"{scope}_{kind}.json.gz").write_bytes(
                        gzip.compress(json.dumps(doc).encode("utf-8"), mtime=0))
            stopsets, values, sources, uncertain = grid.load_services(root)
            self.assertEqual(len(stopsets), 1)
            self.assertEqual(len(sources), 8)
            self.assertEqual(uncertain, 1)
            self.assertEqual(values[(key[0], key[1], "0")][0]["weekday"][8], 1)
            (dest / "Keelung_Schedule.json.gz").write_bytes(gzip.compress(json.dumps({
                "metadata": {"scope_status": "partial_contiguous_pages"}, "records": [],
            }).encode()))
            with self.assertRaisesRegex(ValueError, "partial Keelung Schedule"):
                grid.load_services(root)

    def test_official_geometry_union_keeps_keelung(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            admin = root / "map-data/admin"
            admin.mkdir(parents=True)
            (admin / "cities.geojson").write_text(json.dumps({"features": [
                {"properties": {"admin_code": "63000"}, "geometry": mapping(box(121.4, 25.0, 121.41, 25.01))},
                {"properties": {"admin_code": "65000"}, "geometry": mapping(box(121.45, 25.03, 121.46, 25.04))},
            ]}))
            (admin / "keelung_villages.geojson").write_text(json.dumps({"features": [
                {"properties": {"county_code": "10017"}, "geometry": mapping(box(121.70, 25.1, 121.71, 25.11))},
            ]}))
            region = grid.region_geometry(root)
            self.assertTrue(region.covers(box(121.7, 25.1, 121.71, 25.11)))
            self.assertFalse(region.contains(box(121.6, 25.08, 121.61, 25.09)))


if __name__ == "__main__":
    unittest.main()
