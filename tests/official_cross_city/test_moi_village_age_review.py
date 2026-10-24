"""The independent audit must catch altered cells even when totals still agree."""
import copy
import csv
import gzip
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from scripts import review_moi_village_age as audit


class VillageAgeReviewTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.raw_dir = self.root / "raw_data/moi_village_age"
        self.raw_dir.mkdir(parents=True)
        self.output = self.root / "data/population/moi_village_age_2026-09.json.gz"
        self.output.parent.mkdir(parents=True)
        self.fixture()

    def write_json(self, path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        with (gzip.open(path, "wt", encoding="utf-8") if path.suffix == ".gz" else path.open("w", encoding="utf-8")) as stream:
            json.dump(value, stream, ensure_ascii=False)

    def receipt(self, path, url):
        return {"page_url": audit.PAGE, "requested_url": url, "effective_url": url,
                "downloaded_at": "2026-10-24T14:40:01+08:00", "source_data_time": None,
                "http_status": 200, "http_last_modified": None, "content_type": "text/csv;charset=UTF-8",
                "bytes": path.stat().st_size, "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "raw_file": path.relative_to(self.root).as_posix()}

    def fixture(self, missing=False):
        """A hand-authored national-shape fixture, with no producer imports."""
        raw = self.raw_dir / "moi_village_age_2026-09.csv"
        url = "https://opdadm.moi.gov.tw/fixture.csv"
        catalog = self.raw_dir / "moi_catalog_2026-09.html"
        catalog.write_text(f'<a href="{url}">11509 CSV</a>', encoding="utf-8")
        records, county_totals = [], []
        columns = audit.BASE_COLUMNS + audit.AGE_COLUMNS
        with raw.open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=columns)
            writer.writeheader()
            for index, (county, county_name) in enumerate(sorted(audit.COUNTIES.items())):
                code = county + "010001"
                male, female = [0] * 101, [0] * 101
                male[5], male[70] = 2, 3
                female[6], female[100] = 4, 1
                if missing and index == 0:
                    male[70] = None
                village_name = "瓦󿾨里" if index == 0 else "測試里"
                row = {"統計年月": "11509", "區域別代碼": code, "區域別": county_name + "測試區", "村里": village_name,
                       "戶數": "3", "人口數": "10", "人口數-男": "5", "人口數-女": "5"}
                for age in range(101):
                    label = f"{age}歲" if age < 100 else "100歲以上"
                    row[label + "-男"] = "" if male[age] is None else str(male[age])
                    row[label + "-女"] = str(female[age])
                writer.writerow(row)
                for sex, ages in (("male", male), ("female", female), ("total", [None if a is None else a + b for a, b in zip(male, female)])):
                    record = {"period": "2026-09", "year": 2026, "year_roc": 115, "month": 9,
                              "area_id": code, "area_level": "village", "district_id": code[:8], "county_code": county,
                              "county_name": county_name, "area_name": row["區域別"], "village_name": village_name,
                              "sex": sex, "population": 10 if sex == "total" else 5, "age_counts": ages,
                              "source_row": index + 2, "source_file": raw.name, "boundary_join_status": "not_verified"}
                    if sex == "total":
                        record["households"] = 3
                    records.append(record)
                county_totals.append({"county_code": county, "county_name": county_name, "village_count": 1,
                                      "population": 10, "population_known_sum": 10, "population_unknown_villages": 0})
        receipt = self.receipt(raw, url)
        catalog_receipt = self.receipt(catalog, audit.PAGE)
        self.write_json(Path(str(raw) + ".source.json"), receipt)
        self.write_json(Path(str(catalog) + ".source.json"), catalog_receipt)
        source = {key: value for key, value in receipt.items() if key != "sha256"}
        self.write_json(self.raw_dir / "manifest.json", {"metadata": {"period": "2026-09"},
                         "records": [source, {key: value for key, value in catalog_receipt.items() if key != "sha256"}]})
        n = len(county_totals)
        quality = {"source_row_count": n, "village_count": n, "record_count": 3 * n, "source_schema_column_count": 210,
                   "county_count": n, "unknown_county_count": 0, "missing_source_numeric_cells": int(missing),
                   "population": 10 * n, "population_known_sum": 10 * n, "population_unknown_villages": 0,
                   "county_totals": county_totals,
                   "reconciliation": {"sex_population": {"verified": n, "inconclusive": 0},
                                      "age_population": {"verified": 3 * n - 2 * int(missing), "inconclusive": 2 * int(missing)}}}
        self.doc = {"schema_version": 1, "dataset_id": "moi_village_population_age_monthly",
                    "metadata": {"source": source, "generated_at": receipt["downloaded_at"], "period": "2026-09",
                                 "source_period": {"start": "2026-09", "end": "2026-09"}, "source_data_time": None,
                                 "age_counts_order": audit.AGE_LABELS, "data_kind": "official_published_data",
                                 "license": {"url": "https://data.gov.tw/license"},
                                 "source_schema": {"column_count": 210, "required_columns": sorted(columns), "extra_columns": "rejected"},
                                 "validation": {**copy.deepcopy(quality), "boundary_join_status": "not_verified"}},
                    "records": records, "quality": quality}
        frontend = self.root / "web/src/components/StationInfo.tsx"
        frontend.parent.mkdir(parents=True, exist_ok=True)
        frontend.write_text("戶籍估算 戶籍人口估算，非搭乘量 尚未提供此站範圍的年齡估算", encoding="utf-8")
        self.write_json(self.output, self.doc)

    def result(self):
        self.write_json(self.output, self.doc)
        return audit.review(self.root, "2026-09")

    def assertRejected(self):
        result = self.result()
        self.assertEqual(result["metadata"]["status"], "needs_correction")
        self.assertGreater(result["records"][0]["checks"]["failure_count"], 0)

    def test_complete_fixture_and_unknown_age_cells_are_reconciled(self):
        self.assertEqual(self.result()["metadata"]["status"], "pass_with_limitations")
        self.fixture(missing=True)
        result = self.result()
        self.assertEqual(result["metadata"]["status"], "pass_with_limitations")
        self.assertEqual(result["records"][0]["checks"]["source_missing_numeric_cells"], 1)
        self.assertEqual(result["records"][0]["source"], self.doc["metadata"]["source"])
        self.assertEqual(result["records"][0]["reviewed_generated_at"], self.doc["metadata"]["generated_at"])
        self.assertNotIn("sha256", json.dumps(result))

    def test_middle_age_tamper_with_preserved_population_and_age_sum_is_rejected(self):
        male = self.doc["records"][0]["age_counts"]
        male[5] -= 1
        male[6] += 1
        self.assertEqual(sum(male), 5)
        self.assertRejected()

    def test_column_reordering_preserves_the_complete_source_contract(self):
        raw = self.raw_dir / "moi_village_age_2026-09.csv"
        expected = audit.source_rows(raw, "2026-09")
        with raw.open(encoding="utf-8-sig", newline="") as stream:
            rows = list(csv.reader(stream))
        with raw.open("w", encoding="utf-8-sig", newline="") as stream:
            csv.writer(stream).writerows(list(reversed(row)) for row in rows)
        self.assertEqual(audit.source_rows(raw, "2026-09"), expected)

    def test_extra_or_duplicate_village_sex_record_is_rejected(self):
        for code in ("99999010001", self.doc["records"][0]["area_id"]):
            with self.subTest(code=code):
                original = copy.deepcopy(self.doc)
                extra = copy.deepcopy(self.doc["records"][0])
                extra["area_id"] = code
                self.doc["records"].append(extra)
                self.assertRejected()
                self.doc = original

    def test_source_name_month_period_and_unicode_are_exact(self):
        for field, value in (("village_name", "瓦磘里"), ("period", "2026-08"), ("source_row", 9),
                             ("source_file", "other.csv"), ("area_id", 9007010001)):
            with self.subTest(field=field):
                original = copy.deepcopy(self.doc)
                self.doc["records"][0][field] = value
                self.assertRejected()
                self.doc = original

    def test_unknown_age_cannot_be_filled_with_zero(self):
        self.fixture(missing=True)
        self.doc["records"][0]["age_counts"][70] = 0
        self.assertRejected()

    def test_reconciled_quality_cannot_double_count_total_and_sexes(self):
        self.doc["quality"]["population"] *= 2
        self.assertRejected()

    def test_changed_source_provenance_or_download_time_is_rejected(self):
        self.doc["metadata"]["source"]["downloaded_at"] = "2026-10-24T15:40:01+08:00"
        self.assertRejected()

    def test_changed_snapshot_reference_time_is_rejected(self):
        self.doc["metadata"]["generated_at"] = "2026-10-24T15:40:01+08:00"
        self.assertRejected()

    def test_csv_receipt_content_tamper_is_rejected(self):
        raw = self.raw_dir / "moi_village_age_2026-09.csv"
        raw.write_bytes(raw.read_bytes() + b"\n")
        self.assertRejected()

    def test_frontend_cannot_claim_source_has_no_ages(self):
        frontend = self.root / "web/src/components/StationInfo.tsx"
        frontend.write_text("戶籍估算 戶籍人口估算，非搭乘量 現有來源未提供年齡資料", encoding="utf-8")
        self.assertRejected()

    def test_failed_cli_retains_diagnostic_report_and_nonzero_status(self):
        self.doc["records"][0]["age_counts"][50] = 1
        self.write_json(self.output, self.doc)
        self.assertEqual(audit.main(["--root", str(self.root), "--month", "2026-09"]), 1)
        report = json.loads((self.root / "data/quality/moi_village_age_review.json").read_text(encoding="utf-8"))
        self.assertEqual(report["metadata"]["status"], "needs_correction")


if __name__ == "__main__":
    unittest.main()
