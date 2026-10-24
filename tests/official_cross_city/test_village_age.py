"""Synthetic 22-county fixtures; these are not government population observations."""
from __future__ import annotations

import csv
import gzip
import hashlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.official_cross_city import common, village_age


class VillageAgeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = {"downloaded_at": "2026-10-24T12:00:00+08:00", "source_data_time": None,
                       "page_url": village_age.PAGE, "fixture": "synthetic_not_government_observations"}

    def rows(self):
        rows = []
        for code, name in village_age.COUNTIES.items():
            row = {"統計年月": "11509", "區域別代碼": code + "001001", "區域別": name + "測試區",
                   "村里": "測試里", "戶數": "90", "人口數": "303", "人口數-男": "101", "人口數-女": "202"}
            row.update({field: "1" for field in village_age.AGE_FIELDS["male"]})
            row.update({field: "2" for field in village_age.AGE_FIELDS["female"]})
            rows.append(row)
        return rows

    def csv(self, rows=None, headers=None, encoding="utf-8-sig"):
        rows = self.rows() if rows is None else rows
        path = self.root / "moi_village_age_2026-09.csv"
        with path.open("w", encoding=encoding, newline="") as out:
            writer = csv.DictWriter(out, fieldnames=headers or list(rows[0]), extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)
        return path

    def normalize(self, rows=None):
        return village_age.normalize(self.csv(rows), "2026-09", self.source)

    def result_path(self, rows=None):
        path = self.root / "ages.json.gz"
        village_age.write_gzip(path, self.normalize(rows))
        return path

    def cache(self, name, data, url):
        directory = self.root / "raw_data/moi_village_age"
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / name
        path.write_bytes(data)
        receipt = {"requested_url": url, "effective_url": url, "page_url": village_age.PAGE,
                   "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest(),
                   "http_status": 200, "downloaded_at": self.source["downloaded_at"],
                   "source_data_time": None, "raw_file": str(path.relative_to(self.root))}
        common.write_json(path.with_name(path.name + ".source.json"), receipt)
        return path, receipt

    def offline_cache(self):
        url = "https://opdadm.moi.gov.tw/published.csv"
        html = f'<li class="resource-item"><a href="{url}"><span>CSV</span></a><span>11509-村里戶數、單一年齡人口</span></li>'
        self.cache("moi_catalog_2026-09.html", html.encode(), village_age.PAGE)
        self.cache("moi_village_age_2026-09.csv", self.csv().read_bytes(), url)

    def test_complete_schema_22_counties_and_existing_population_contract(self):
        result = self.normalize()
        self.assertEqual(len(village_age.REQUIRED), 210)
        self.assertEqual(result["dataset_id"], "moi_village_population_age_monthly")
        self.assertEqual(len(result["records"]), 66)
        record = result["records"][0]
        self.assertEqual(record["area_id"], "09007001001")
        self.assertEqual(record["district_id"], "09007001")
        self.assertEqual((record["year"], record["year_roc"], record["month"]), (2026, 115, 9))
        self.assertEqual(record["boundary_join_status"], "not_verified")
        self.assertEqual(record["source_row"], 2)
        self.assertEqual(record["source_file"], "moi_village_age_2026-09.csv")
        self.assertEqual(record["age_counts"], [1] * 101)
        total = result["records"][2]
        self.assertEqual(total["sex"], "total")
        self.assertEqual(total["age_counts"], [3] * 101)
        self.assertEqual(total["households"], 90)
        self.assertNotIn("households", record)
        self.assertEqual(result["quality"]["population"], 22 * 303)
        self.assertEqual(result["quality"]["unknown_county_count"], 0)
        self.assertEqual(result["quality"]["reconciliation"]["age_population"], {"verified": 66, "inconclusive": 0})

    def test_metadata_preserves_receipt_time_license_fields_and_limits(self):
        result = village_age.normalize(self.csv(), "2026-09", {**self.source, "sha256": "not-published"})
        meta = result["metadata"]
        self.assertEqual(meta["generated_at"], self.source["downloaded_at"])
        self.assertIsNone(meta["source_data_time"])
        self.assertIsNone(meta["source"]["source_data_time"])
        self.assertNotIn("sha256", meta["source"])
        self.assertEqual(meta["source_dataset_id"], "77132")
        self.assertEqual(meta["license"], common.OGDL)
        self.assertEqual(meta["age_counts_order"], [str(age) for age in range(100)] + ["100_plus"])
        self.assertIn("not the current conversion time", meta["generated_at_definition"])
        self.assertEqual(len(meta["source_field_mapping"]["age_counts_male"]), 101)
        self.assertIn("not an independent", meta["coverage"])
        self.assertIn("真實乘車需求", meta["transport_interpretation"])
        self.assertEqual(meta["independent_review_status"], "pending")

    def test_source_period_formats_and_raw_name_preservation(self):
        for period in ["11509", "202609", "2026-09"]:
            rows = self.rows()
            for row in rows:
                row["統計年月"] = period
                row["區域別"] = row["區域別"].replace("臺", "台")
            result = self.normalize(rows)
            taipei = next(row for row in result["records"] if row["county_code"] == "63000")
            self.assertEqual(taipei["area_name"], "台北市測試區")
            self.assertEqual(taipei["county_name"], "臺北市")

    def test_cp950_explicit_encoding(self):
        path = self.csv(encoding="cp950")
        self.assertEqual(len(village_age.normalize(path, "2026-09", self.source, encoding="cp950")["records"]), 66)

    def test_missing_cells_remain_null_and_reconciliation_is_inconclusive(self):
        rows = self.rows()
        rows[0]["0歲-男"] = "-"
        rows[0]["人口數"] = ""
        rows[0]["戶數"] = "NA"
        result = self.normalize(rows)
        male, female, total = result["records"][:3]
        self.assertIsNone(male["age_counts"][0])
        self.assertEqual(female["age_counts"][0], 2)
        self.assertIsNone(total["age_counts"][0])
        self.assertIsNone(total["population"])
        self.assertIsNone(total["households"])
        self.assertEqual(result["quality"]["missing_source_numeric_cells"], 3)
        self.assertIsNone(result["quality"]["population"])
        self.assertEqual(result["quality"]["population_known_sum"], 21 * 303)
        self.assertEqual(result["quality"]["population_unknown_villages"], 1)
        self.assertIsNone(result["quality"]["county_totals"][0]["population"])
        self.assertEqual(result["quality"]["reconciliation"]["sex_population"]["inconclusive"], 1)
        self.assertEqual(result["quality"]["reconciliation"]["age_population"]["inconclusive"], 2)

    def test_real_zero_remains_zero(self):
        rows = self.rows()
        for field in ["戶數", "人口數", "人口數-男", "人口數-女", *village_age.AGE_FIELDS["male"], *village_age.AGE_FIELDS["female"]]:
            rows[0][field] = "0"
        total = self.normalize(rows)["records"][2]
        self.assertEqual(total["population"], 0)
        self.assertEqual(total["age_counts"], [0] * 101)

    def test_requires_download_receipt(self):
        with self.assertRaisesRegex(ValueError, "receipt"):
            village_age.normalize(self.csv(), "2026-09", {})

    def test_rejects_bad_identity_name_month_and_values(self):
        mutations = [
            ("區域別代碼", "0900700100"), ("區域別代碼", "090070010011"),
            ("區域別代碼", "０９００７００１００１"), ("區域別代碼", "99999001001"),
            ("區域別", ""), ("村里", "-"), ("區域別", "臺北市測試區"),
            ("統計年月", "11508"), ("統計年月", "11513"), ("0歲-男", "-1"),
            ("0歲-男", "1.5"), ("0歲-男", "1,2"), ("0歲-男", "NaN"),
        ]
        for field, value in mutations:
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                rows = self.rows()
                rows[0][field] = value
                self.normalize(rows)

    def test_rejects_each_population_reconciliation_failure(self):
        for changes in [{"人口數": "304"}, {"0歲-男": "0"}, {"0歲-女": "0"},
                        {"人口數-男": "", "人口數": "304"}]:
            with self.subTest(changes=changes), self.assertRaisesRegex(ValueError, "mismatch"):
                rows = self.rows()
                rows[0].update(changes)
                self.normalize(rows)

    def test_duplicate_code_is_error_even_with_different_village_or_area_name(self):
        for changes in [{}, {"村里": "另一里"}, {"區域別": "連江縣另一鄉"}]:
            with self.subTest(changes=changes), self.assertRaisesRegex(ValueError, "Duplicate village code"):
                rows = self.rows()
                rows.append({**rows[0], **changes})
                self.normalize(rows)

    def test_multiple_villages_in_one_county_supported_without_fixed_national_count(self):
        rows = self.rows()
        rows.append({**rows[0], "區域別代碼": "09007001002", "村里": "另一里"})
        result = self.normalize(rows)
        self.assertEqual(result["quality"]["village_count"], 23)
        self.assertEqual(result["quality"]["county_totals"][0]["village_count"], 2)

    def test_missing_county_is_error(self):
        with self.assertRaisesRegex(ValueError, "Required counties missing"):
            self.normalize(self.rows()[:-1])

    def test_missing_or_extra_source_column_fails_closed(self):
        for fieldnames in [list(self.rows()[0])[:-1], [*self.rows()[0], "新增欄"]]:
            with self.subTest(columns=len(fieldnames)), self.assertRaises(ValueError):
                village_age.normalize(self.csv(headers=fieldnames), "2026-09", self.source)

    def test_duplicate_header_malformed_width_empty_and_html_are_rejected(self):
        for contents in ["<html>error</html>", "a,a\n1,2\n", ",\n1,2\n", "", ",".join(self.rows()[0]) + "\n", ",".join(self.rows()[0]) + "\n11509\n"]:
            with self.subTest(contents=contents[:30]), self.assertRaises(ValueError):
                path = self.root / "bad.csv"
                path.write_text(contents)
                village_age.normalize(path, "2026-09", self.source)

    def test_gzip_is_compact_deterministic_and_valid_utf8_json(self):
        result = self.normalize()
        first, second = self.root / "first.gz", self.root / "second.gz"
        village_age.write_gzip(first, result)
        village_age.write_gzip(second, result)
        self.assertEqual(first.read_bytes(), second.read_bytes())
        self.assertEqual(first.read_bytes()[4:8], b"\0\0\0\0")
        content = gzip.decompress(first.read_bytes()).decode()
        self.assertEqual(json.loads(content), result)
        self.assertNotIn('\n  "', content)

    def test_atomic_gzip_failure_preserves_previous_output_and_removes_temporary(self):
        path = self.result_path()
        previous = path.read_bytes()
        with self.assertRaises(ValueError):
            village_age.write_gzip(path, {"invalid": float("nan")})
        self.assertEqual(path.read_bytes(), previous)
        self.assertEqual(list(self.root.glob("*.part")), [])

    def test_cli_default_plan_does_not_network_or_create_outputs(self):
        with patch.object(village_age, "fetch", side_effect=AssertionError("network forbidden")), patch("sys.stdout", new=io.StringIO()) as stdout:
            village_age.main(["--month", "2026-09", "--root", str(self.root)])
        self.assertEqual(json.loads(stdout.getvalue())["status"], "plan_only_select_exact_published_month")
        self.assertFalse((self.root / "data").exists())
        self.assertFalse((self.root / "raw_data").exists())

    def test_offline_rebuild_uses_receipts_and_is_byte_identical_including_manifests(self):
        self.offline_cache()
        argv = ["--month", "2026-09", "--root", str(self.root), "--offline"]
        with patch.object(common, "build_opener", side_effect=AssertionError("network forbidden")), patch("sys.stdout", new=io.StringIO()):
            village_age.main(argv)
            paths = [self.root / "data/population/moi_village_age_2026-09.json.gz",
                     self.root / "raw_data/moi_village_age/manifest.json", self.root / "raw_data/moi_village_age/delivery.json"]
            previous = [path.read_bytes() for path in paths]
            village_age.main(argv)
        self.assertEqual(previous, [path.read_bytes() for path in paths])
        manifest = json.loads(paths[1].read_text())
        self.assertEqual(len(manifest["records"]), 2)
        self.assertTrue(all("sha256" not in source for source in manifest["records"]))
        delivery = json.loads(paths[2].read_text())
        self.assertEqual(delivery["datasets"][0]["record_count"], 66)
        self.assertEqual(delivery["datasets"][0]["output_files"], ["data/population/moi_village_age_2026-09.json.gz"])

    def test_download_failure_preserves_last_successful_output(self):
        self.offline_cache()
        output = self.root / "data/population/moi_village_age_2026-09.json.gz"
        village_age.write_gzip(output, {"previous": "successful output"})
        previous = output.read_bytes()
        catalog = self.root / "raw_data/moi_village_age/moi_catalog_2026-09.html"
        with patch.object(village_age, "fetch", side_effect=[(catalog, self.source), OSError("download failed")]), patch("sys.stderr", new=io.StringIO()):
            with self.assertRaises(SystemExit) as error:
                village_age.main(["--month", "2026-09", "--root", str(self.root), "--fetch"])
        self.assertEqual(error.exception.code, 2)
        self.assertEqual(output.read_bytes(), previous)

    def test_unpublished_month_does_not_guess_a_url_or_replace_output(self):
        self.offline_cache()
        catalog = self.root / "raw_data/moi_village_age/moi_catalog_2026-09.html"
        with patch.object(village_age, "fetch", return_value=(catalog, self.source)) as fetch, patch("sys.stderr", new=io.StringIO()):
            with self.assertRaises(SystemExit):
                village_age.main(["--month", "2026-10", "--root", str(self.root), "--fetch"])
        self.assertEqual(fetch.call_count, 1)
        self.assertFalse((self.root / "data").exists())

    def test_cli_invalid_arguments_are_nonzero(self):
        for args in [[], ["--month", "2026-13"], ["--month", "2026-09", "--fetch", "--offline"],
                     ["--month", "2026-09", "--area-id", "09007001001"], ["--query", "x.gz"],
                     ["--query", "x.gz", "--area-id", "09007001001", "--offline"]]:
            with self.subTest(args=args), patch("sys.stderr", new=io.StringIO()):
                with self.assertRaises(SystemExit) as error:
                    village_age.main(args)
                self.assertNotEqual(error.exception.code, 0)

    def test_query_one_sex_one_village_and_inclusive_range(self):
        path = self.result_path()
        result = village_age.query(path, "09007001001", 65, 100)
        self.assertEqual(result["dataset_id"], "moi_village_population_age_query")
        record = result["records"][0]
        self.assertEqual(record["population"], 36 * 3)
        self.assertEqual(record["population_all_ages"], 303)
        self.assertTrue(record["max_age_is_open_ended"])
        self.assertEqual(record["missing_age_cells"], 0)
        self.assertEqual(village_age.query(path, "09007001001", 100, 100, "male")["records"][0]["population"], 1)
        self.assertEqual(village_age.query(path, "09007001001", 0, 0, "female")["records"][0]["population"], 2)
        self.assertEqual(village_age.query(path, "09007001001")["records"][0]["population"], 303)

    def test_query_null_range_is_not_partial_population_or_zero(self):
        rows = self.rows()
        rows[0]["65歲-男"] = "-"
        path = self.result_path(rows)
        query = village_age.query(path, "09007001001", 65, 100)["records"][0]
        self.assertIsNone(query["population"])
        self.assertEqual(query["missing_age_cells"], 1)
        self.assertEqual(village_age.query(path, "09007001001", 0, 64)["records"][0]["population"], 65 * 3)

    def test_query_unknown_village_month_sex_and_age_bounds_fail(self):
        path = self.result_path()
        for kwargs in [{"area_id": "09007001002"}, {"area_id": "0900700100"}, {"month": "2026-08"},
                       {"sex": "all"}, {"min_age": -1}, {"max_age": 101}, {"min_age": 100, "max_age": 99},
                       {"min_age": 1.5}, {"min_age": True}]:
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                village_age.query(path, **{"area_id": "09007001001", **kwargs})

    def test_query_rejects_duplicate_records_bad_envelope_and_malformed_ages(self):
        for alteration in ["duplicate", "id", "age_count", "negative", "boolean", "period", "order", "envelope", "record"]:
            with self.subTest(alteration=alteration), self.assertRaises(ValueError):
                obj = self.normalize()
                if alteration == "duplicate":
                    obj["records"].append(obj["records"][2])
                elif alteration == "id":
                    obj["dataset_id"] = "some_other_dataset"
                elif alteration == "age_count":
                    obj["records"][2]["age_counts"].pop()
                elif alteration == "negative":
                    obj["records"][2]["age_counts"][0] = -1
                elif alteration == "boolean":
                    obj["records"][2]["age_counts"][0] = True
                elif alteration == "order":
                    obj["metadata"]["age_counts_order"].reverse()
                elif alteration == "envelope":
                    obj = []
                elif alteration == "record":
                    obj["records"].append([])
                else:
                    obj["records"][2]["period"] = "2026-08"
                path = self.root / "invalid.json.gz"
                village_age.write_gzip(path, obj)
                village_age.query(path, "09007001001")

    def test_query_cli_does_not_require_month_and_prints_json_envelope(self):
        path = self.result_path()
        with patch.object(village_age, "fetch", side_effect=AssertionError("network forbidden")), patch("sys.stdout", new=io.StringIO()) as stdout:
            village_age.main(["--query", str(path), "--area-id", "09007001001", "--min-age", "100", "--max-age", "100"])
        self.assertEqual(json.loads(stdout.getvalue())["records"][0]["population"], 3)
        with patch("sys.stderr", new=io.StringIO()), self.assertRaises(SystemExit):
            village_age.main(["--query", str(path), "--area-id", "09007001001", "--max-age", "101"])


if __name__ == "__main__":
    unittest.main()
