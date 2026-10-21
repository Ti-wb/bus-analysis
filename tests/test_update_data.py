import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts.update_data import (
    Downloads, arrivals_json, number, numeric_rows, period_from_text,
    reconcile_totals, resolve_stop_crs, run, sha256, typed_pdf_rows,
)


class DataSemanticsTests(unittest.TestCase):
    def test_arrival_status_is_not_a_negative_duration(self):
        feed = {"EssentialInfo": {"UpdateTime": "2026/10/21 23:14:20"}, "BusInfo": [
            {"RouteID": "001", "StopID": str(i), "EstimateTime": str(eta), "GoBack": "2"}
            for i, eta in enumerate([-1, -2, -3, -4, -99, 0, 45])
        ]}
        rows, updated = arrivals_json(feed)
        self.assertEqual([r["estimate_seconds"] for r in rows], [None] * 5 + [0, 45])
        self.assertEqual(rows[4]["status"], "unknown")
        self.assertEqual(rows[6]["go_back_status"], "not_departed")
        self.assertEqual(rows[0]["route_id"], "001")
        self.assertEqual(updated, "2026-10-21T23:14:20+08:00")

    def test_crs_conflict_does_not_double_project_coordinates(self):
        crs, basis, warnings = resolve_stop_crs([121.46, 24.96, 121.67, 25.19])
        self.assertEqual(crs.to_epsg(), 4326)
        self.assertEqual(basis, "inferred_from_coordinate_range")
        self.assertTrue(warnings)
        projected, _, _ = resolve_stop_crs([300000, 2760000, 310000, 2780000])
        self.assertEqual(projected.to_epsg(), 3826)
        with self.assertRaises(ValueError):
            resolve_stop_crs([0, 0, 1, 1])

    def test_revised_pdf_values_missing_values_and_reporting_period(self):
        self.assertEqual(number("337,355.2"), 337355.2)
        self.assertIsNone(number("…"))
        rows = numeric_rows("總 計 ④ 10 ⓡ 1,234 … 0", count=4)
        self.assertEqual(rows[0]["values"], [10, 1234, None, 0])
        period = period_from_text("114.01.07 修訂\n中華民國 114 年 10 月\n中華民國 115 年 4 月 8 日編製")
        self.assertEqual(period["period"], "2025-10")
        doc = {"id": "revision", "title": "修正表", "period": period}
        result = typed_pdf_rows(doc, ["總 計 ④ 10 ⓡ 1,234 … 0\n備註：範例 1 2 3 4"],
                                ["a", "b", "c", "d"], "operator_name")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["revised_fields"], ["b"])
        self.assertEqual(result[0]["operator_name"], "總計")

    def test_totals_detect_dropped_pdf_rows(self):
        rows = [{"is_total": True, "passenger_trips": 100},
                {"is_total": False, "passenger_trips": 90}]
        with self.assertRaisesRegex(ValueError, "do not reconcile"):
            reconcile_totals(rows, ["passenger_trips"])


class UpdateSafetyTests(unittest.TestCase):
    def test_offline_replay_rejects_modified_source(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "sample.json").write_text("[]")
            url = "https://data.taipei/sample.json"
            (root / "taipei_bus_manifest.json").write_text(json.dumps({
                "sample.json": {"url": url, "sha256": sha256(root / "sample.json")}
            }))
            downloads = Downloads(root, offline=True)
            self.assertEqual(downloads.fetch(url, "sample.json").read_text(), "[]")
            (root / "sample.json").write_text("[1]")
            with self.assertRaisesRegex(ValueError, "changed offline source"):
                downloads.fetch(url, "sample.json")

    def test_failed_refresh_preserves_previous_data(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "data").mkdir()
            target = root / "data/bus_arrivals.json"
            target.write_text('"previous"')

            def fail(stage, offline):
                (stage / "data").mkdir()
                (stage / "data/bus_arrivals.json").write_text('"incomplete"')
                raise ValueError("bad PDF")

            with patch("scripts.update_data.build", side_effect=fail):
                with self.assertRaises(ValueError):
                    run(root)
            self.assertEqual(target.read_text(), '"previous"')

    def test_refresh_does_not_overwrite_another_agents_source(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "raw_data/population").mkdir(parents=True)
            other = root / "raw_data/population/manifest.json"
            other.write_text('"old"')
            (root / "data").mkdir()
            (root / "data/population.json").write_text('"population"')

            def build_owned(stage, offline):
                self.assertFalse((stage / "raw_data/population").exists())
                other.write_text('"concurrent update"')
                (stage / "data").mkdir()
                (stage / "data/bus_arrivals.json").write_text('"bus"')

            with patch("scripts.update_data.build", side_effect=build_owned):
                run(root)
            self.assertEqual(other.read_text(), '"concurrent update"')
            self.assertEqual((root / "data/population.json").read_text(), '"population"')


if __name__ == "__main__":
    unittest.main()
