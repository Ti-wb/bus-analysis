"""All fixtures below are SYNTHETIC. No live data or API credentials are required."""
from __future__ import annotations

import csv
import hashlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.request import Request

from scripts.official_cross_city import common, ntpc, population, metro_od


class FixtureCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.source = {"downloaded_at": "2026-10-22T12:00:00+08:00", "source_data_time": None,
                       "fixture": "synthetic_not_government_observations"}

    def csv(self, name, rows, fieldnames=None):
        path = self.root / name
        with path.open("w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames or list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
        return path


class CommonTests(FixtureCase):
    def test_url_accepts_exact_https_host(self):
        self.assertEqual(common.check_url("https://data.ntpc.gov.tw/api", {"data.ntpc.gov.tw"}), "https://data.ntpc.gov.tw/api")

    def test_url_rejects_scheme_credentials_subdomain_and_port(self):
        for url in ["http://data.ntpc.gov.tw/a", "https://data.ntpc.gov.tw.attacker.example/a",
                    "https://u:p@data.ntpc.gov.tw/a", "https://data.ntpc.gov.tw:8443/a",
                    "file:///tmp/x", "https://127.0.0.1/x", "https://data.ntpc.gov.tw/x#fragment"]:
            with self.subTest(url=url), self.assertRaises(ValueError):
                common.check_url(url, {"data.ntpc.gov.tw"})

    def test_redirect_checked_before_network(self):
        handler = common.SafeRedirect({"data.ntpc.gov.tw"})
        with self.assertRaises(ValueError):
            handler.redirect_request(Request("https://data.ntpc.gov.tw/a"), None, 302, "", {}, "https://evil.example/a")

    def test_numeric_zero_and_null_differ(self):
        self.assertEqual(common.integer("0"), 0)
        self.assertIsNone(common.integer("-"))
        self.assertEqual(common.integer("1,234"), 1234)
        for bad in ["1,2", "-1", "1.0", "NaN", "false"]:
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                common.integer(bad)

    def test_csv_rejects_duplicate_headers(self):
        p = self.root / "bad.csv"
        p.write_text("id,ID\na,b\n")
        with self.assertRaises(ValueError):
            list(common.csv_rows(p, {"id"}, lower_headers=True))

    def test_csv_rejects_html(self):
        p = self.root / "bad.csv"
        p.write_text("<html>Access denied</html>")
        with self.assertRaises(ValueError):
            list(common.csv_rows(p, {"id"}))

    def test_csv_rejects_truncated_width(self):
        p = self.root / "bad.csv"
        p.write_text("id,name\n1\n")
        with self.assertRaises(ValueError):
            list(common.csv_rows(p, {"id", "name"}))

    def test_empty_csv_is_not_zero_service(self):
        p = self.root / "empty.csv"
        p.write_text("id,name\n")
        with self.assertRaises(ValueError):
            list(common.csv_rows(p, {"id"}))

    def test_atomic_json_preserves_previous_file(self):
        p = self.root / "out.json"
        common.write_json(p, {"valid": 1})
        with self.assertRaises(ValueError):
            common.write_json(p, {"bad": float("nan")})
        self.assertEqual(json.loads(p.read_text()), {"valid": 1})
        self.assertEqual(list(self.root.glob("*.part")), [])

    def test_month_validation(self):
        for m in ["2026-00", "2026-13", "26-09", "../../x"]:
            with self.subTest(month=m), self.assertRaises(ValueError):
                common.validate_month(m)

    def test_month_link_exact_not_latest_guess(self):
        html = '<a href="https://opdadm.moi.gov.tw/old">11508人口</a><a href="https://opdadm.moi.gov.tw/right"><span>11509</span>人口</a>'
        self.assertEqual(common.monthly_link(html, population.PAGE, "2026-09", population.HOSTS), "https://opdadm.moi.gov.tw/right")
        with self.assertRaises(ValueError):
            common.monthly_link(html, population.PAGE, "2026-10", population.HOSTS)

    def test_month_ambiguous_links_fail_closed(self):
        html = '<a href="https://opdadm.moi.gov.tw/a">11509</a><a href="https://opdadm.moi.gov.tw/b">11509</a>'
        with self.assertRaises(ValueError):
            common.monthly_link(html, population.PAGE, "2026-09", population.HOSTS)

    def test_offline_requires_matching_receipt(self):
        raw = self.root / "raw_data/official_cross_city"
        raw.mkdir(parents=True)
        (raw / "x.csv").write_text("a,b\n1,2\n")
        with self.assertRaises(ValueError):
            common.fetch(self.root, "x.csv", "https://data.ntpc.gov.tw/x", "p", {"data.ntpc.gov.tw"}, offline=True)
        receipt = {"requested_url": "https://data.ntpc.gov.tw/x", "effective_url": "https://data.ntpc.gov.tw/x", "page_url": "p", "bytes": 8, "http_status": 200, "downloaded_at": "known", "sha256": hashlib.sha256(b"a,b\n1,2\n").hexdigest()}
        common.write_json(raw / "x.csv.source.json", receipt)
        path, _ = common.fetch(self.root, "x.csv", receipt["requested_url"], "p", {"data.ntpc.gov.tw"}, offline=True)
        self.assertEqual(path.stat().st_size, 8)
        (raw / "x.csv").write_text("truncated")
        with self.assertRaises(ValueError):
            common.fetch(self.root, "x.csv", receipt["requested_url"], "p", {"data.ntpc.gov.tw"}, offline=True)

    def test_raw_path_traversal_rejected(self):
        with self.assertRaises(ValueError):
            common.fetch(self.root, "../x.csv", "https://data.ntpc.gov.tw/x", "p", {"data.ntpc.gov.tw"}, offline=True)

    def test_ntpc_default_is_plan_without_network(self):
        with patch.object(ntpc, "fetch", side_effect=AssertionError("no network")), patch("sys.stdout", new=io.StringIO()) as out:
            ntpc.main(["--root", str(self.root)])
        self.assertEqual(len(out.getvalue().strip().splitlines()), 3)
        self.assertFalse((self.root / "data").exists())


class NtpcTests(FixtureCase):
    def route(self):
        return {"id": "001", "providerid": "02", "namezh": "測試線", "pathattributeid": "003", "distance": "12.5", "peakheadway": "10-15", "realsequence": "50"}

    def stop(self):
        return {"id": "0004", "routeid": "001", "namezh": "測試站", "seqno": "1", "pgp": "9", "goback": "0", "longitude": "121.5", "latitude": "25.0", "stoplocationid": "0005"}

    def test_routes_preserve_ids_and_descriptions(self):
        result = ntpc.normalize(self.csv("routes.csv", [self.route()]), "routes", self.source)
        record = result["records"][0]
        self.assertEqual(record["route_id"], "001")
        self.assertEqual(record["subroute_id"], "003")
        self.assertEqual(record["peak_headway_raw"], "10-15")
        self.assertEqual(record["round_trip_distance_raw"], "12.5")
        self.assertEqual(result["metadata"]["independent_review_status"], "pending")

    def test_exact_duplicates_fold_with_provenance(self):
        result = ntpc.normalize(self.csv("routes.csv", [self.route(), self.route()]), "routes", self.source)
        self.assertEqual(len(result["records"]), 1)
        self.assertEqual(result["records"][0]["source_row_numbers"], [2, 3])
        self.assertEqual(result["quality"]["exact_duplicate_rows_collapsed"], 1)

    def test_conflicting_duplicate_routes_rejected(self):
        with self.assertRaises(ValueError):
            ntpc.normalize(self.csv("routes.csv", [self.route(), {**self.route(), "namezh": "衝突"}]), "routes", self.source)

    def test_different_operators_remain_separate(self):
        result = ntpc.normalize(self.csv("r.csv", [self.route(), {**self.route(), "providerid": "03"}]), "routes", self.source)
        self.assertEqual(len(result["records"]), 2)

    def test_missing_key_rejected(self):
        with self.assertRaises(ValueError):
            ntpc.normalize(self.csv("r.csv", [{**self.route(), "id": ""}]), "routes", self.source)

    def test_stop_codes_are_not_guessed(self):
        result = ntpc.normalize(self.csv("s.csv", [self.stop()]), "stops", self.source)
        r = result["records"][0]
        self.assertEqual(r["boarding_alighting_code_raw"], "9")
        self.assertNotIn("can_board", r)
        self.assertEqual(r["position"], {"longitude": 121.5, "latitude": 25.0})

    def test_invalid_coordinates_preserved_as_null(self):
        result = ntpc.normalize(self.csv("s.csv", [{**self.stop(), "longitude": "0"}]), "stops", self.source)
        self.assertIsNone(result["records"][0]["position"])
        self.assertEqual(result["records"][0]["longitude_raw"], "0")
        self.assertEqual(result["quality"]["invalid_coordinate_rows"], 1)

    def test_opposite_direction_and_loop_stops_not_collapsed(self):
        rows = [self.stop(), {**self.stop(), "goback": "1"}, {**self.stop(), "seqno": "8"}]
        result = ntpc.normalize(self.csv("s.csv", rows), "stops", self.source)
        self.assertEqual(len(result["records"]), 3)

    def test_new_source_columns_are_not_silently_dropped(self):
        result = ntpc.normalize(self.csv("r.csv", [{**self.route(), "futurefield": "x"}]), "routes", self.source)
        self.assertEqual(result["records"][0]["source_extra_fields"], {"futurefield": "x"})

    def test_leapfrog_not_joined_by_name(self):
        row = {"type": "跳蛙", "seqno": "01", "agency_codes": "65000", "routename": "測試跳蛙", "start_and_end": "甲－乙", "weekday_operation_hours": "07:00", "holiday_operation_hours": "停駛"}
        result = ntpc.normalize(self.csv("frog.csv", [row]), "leapfrog", self.source)
        r = result["records"][0]
        self.assertIsNone(r["route_id"])
        self.assertEqual(r["linkage_status"], "unverified")
        self.assertEqual(r["holiday_operation_hours_raw"], "停駛")


class PopulationTests(FixtureCase):
    def row(self, code):
        row = {"統計年月": "11509", "區域別代碼": code, "區域別": "合成測試區", "村里": "合成測試里", "戶數": "3", "人口數": "6", "人口數-男": "3", "人口數-女": "3"}
        for age in range(100):
            for sex in ["男", "女"]:
                row[f"{age}歲-{sex}"] = "1" if age in {0, 20, 65} else "0"
        row.update({"100歲以上-男": "0", "100歲以上-女": "0"})
        return row

    def rows(self):
        return [self.row(c + "01001") for c in population.COUNTIES]

    def test_population_aggregates_all_three_cities(self):
        result = population.normalize(self.csv("p.csv", self.rows()), "2026-09", self.source)
        self.assertEqual(len(result["records"]), 3)
        self.assertTrue(all(r["population_age_65_plus"] == 2 for r in result["records"]))
        self.assertTrue(all(r["population_age_0_14"] == 2 for r in result["records"]))
        self.assertTrue(all(r["boundary_join_status"] == "not_verified" for r in result["records"]))

    def test_missing_county_is_not_zero_population(self):
        with self.assertRaises(ValueError):
            population.normalize(self.csv("p.csv", self.rows()[:2]), "2026-09", self.source)

    def test_month_mismatch_rejected(self):
        with self.assertRaises(ValueError):
            population.normalize(self.csv("p.csv", self.rows()), "2026-08", self.source)

    def test_duplicate_village_rejected(self):
        with self.assertRaises(ValueError):
            population.normalize(self.csv("p.csv", self.rows() + [self.rows()[0]]), "2026-09", self.source)

    def test_population_sex_totals_checked(self):
        rows = self.rows()
        rows[0]["人口數"] = "100"
        with self.assertRaises(ValueError):
            population.normalize(self.csv("p.csv", rows), "2026-09", self.source)

    def test_age_totals_checked(self):
        rows = self.rows()
        rows[0]["20歲-男"] = "2"
        with self.assertRaises(ValueError):
            population.normalize(self.csv("p.csv", rows), "2026-09", self.source)

    def test_missing_age_cell_propagates_null(self):
        rows = self.rows()
        rows[0]["65歲-男"] = ""
        result = population.normalize(self.csv("p.csv", rows), "2026-09", self.source)
        r = next(r for r in result["records"] if r["county_code"] == "63000")
        self.assertIsNone(r["population_age_65_plus"])
        self.assertEqual(result["quality"]["missing_numeric_cells"], 1)

    def test_unsupported_schema_not_published(self):
        with self.assertRaises(ValueError):
            population.normalize(self.csv("p.csv", [{"statistic_yyy": "11509", "foo": "1"}]), "2026-09", self.source)


class MetroTests(FixtureCase):
    def rows(self):
        return [{"日期": "2026-09-01", "時段": "7", "進站": o, "出站": d, "人次": "2" if o != d else "0"}
                for o in ["甲", "乙", "丙"] for d in ["甲", "乙", "丙"]]

    def result(self, rows=None, **kwargs):
        return metro_od.normalize(self.csv("m.csv", rows if rows is not None else self.rows()), "2026-09", self.source, {"甲"}, {"乙"}, **kwargs)

    def test_exact_directional_od_and_conservation(self):
        result = self.result()
        self.assertEqual(result["quality"]["source_known_passenger_trips_sum"], 12)
        self.assertEqual(result["quality"]["selected_known_passenger_trips_sum"], 2)
        self.assertEqual(len(result["records"]), 1)
        self.assertEqual(result["records"][0]["origin_station_name"], "甲")

    def test_bidirectional_selection_does_not_double_count(self):
        result = self.result(bidirectional=True)
        self.assertEqual(len(result["records"]), 2)
        self.assertEqual(result["quality"]["selected_known_passenger_trips_sum"], 4)

    def test_duplicate_cell_rejected(self):
        rows = self.rows()
        rows.insert(1, rows[0].copy())
        with self.assertRaises(ValueError):
            self.result(rows)

    def test_noncontiguous_group_rejected(self):
        with self.assertRaises(ValueError):
            self.result(self.rows() + [self.rows()[0]])

    def test_missing_counts_preserved(self):
        rows = self.rows()
        rows[1]["人次"] = "--"
        result = self.result(rows)
        self.assertIsNone(result["records"][0]["passenger_trips"])
        self.assertEqual(result["quality"]["selected_missing_count_cells"], 1)
        self.assertEqual(result["quality"]["selected_known_passenger_trips_sum"], 0)

    def test_missing_pair_not_zero_filled(self):
        rows = [r for r in self.rows() if not (r["進站"] == "乙" and r["出站"] == "甲")]
        result = self.result(rows, bidirectional=True)
        self.assertEqual(result["quality"]["selected_pairs_not_observed"], [["乙", "甲"]])
        self.assertEqual(result["quality"]["selected_cells_not_observed"], 1)
        self.assertEqual(len(result["records"]), 1)

    def test_typo_station_rejected(self):
        with self.assertRaises(ValueError):
            metro_od.normalize(self.csv("m.csv", self.rows()), "2026-09", self.source, {"甲錯字"}, {"乙"})

    def test_invalid_hour_rejected(self):
        with self.assertRaises(ValueError):
            self.result([{**self.rows()[0], "時段": "24"}])

    def test_negative_count_rejected(self):
        with self.assertRaises(ValueError):
            self.result([{**self.rows()[0], "人次": "-1"}])

    def test_cross_month_rows_retained_and_labelled(self):
        rows = [{**r, "日期": "2026/10/1"} for r in self.rows()]
        result = self.result(rows)
        self.assertFalse(result["records"][0]["within_source_month"])
        self.assertEqual(result["quality"]["source_out_of_month_rows"], 9)

    def test_selected_row_budget(self):
        with patch.object(metro_od, "MAX_SELECTED_ROWS", 1), self.assertRaises(ValueError):
            self.result(bidirectional=True)

    def test_source_ref_exact_month_and_official_host(self):
        p = self.root / "ref.json"
        obj = {"dataset_id": "metro_od_source_references", "records": [{"source": {"period": "2026-09", "page_url": metro_od.PAGE,
                 "download_url": "https://tcgmetro.blob.core.windows.net/stationod/synthetic.csv"}}]}
        common.write_json(p, obj)
        url, _ = metro_od.source_for_month(p, "2026-09")
        self.assertIn("/stationod/", url)
        with self.assertRaises(ValueError):
            metro_od.source_for_month(p, "2026-08")
        obj["records"][0]["source"]["download_url"] = "https://evil.example/test.csv"
        common.write_json(p, obj)
        with self.assertRaises(ValueError):
            metro_od.source_for_month(p, "2026-09")


if __name__ == "__main__":
    unittest.main()
