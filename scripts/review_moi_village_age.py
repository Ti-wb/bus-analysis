"""Independently reconcile MOI village-age CSV values and the published gzip.

This audit uses only the standard library and never imports the collector.
It writes a failed report before returning a nonzero CLI status.
"""
from __future__ import annotations

import argparse
from collections import Counter
import csv
from datetime import datetime
import gzip
import hashlib
import json
from pathlib import Path
import re
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
PAGE = "https://data.gov.tw/dataset/77132"
HOSTS = {"opdadm.moi.gov.tw", "www.ris.gov.tw", "ris.gov.tw"}
AGE_LABELS = [str(age) for age in range(100)] + ["100_plus"]
BASE_COLUMNS = ["統計年月", "區域別代碼", "區域別", "村里", "戶數", "人口數", "人口數-男", "人口數-女"]
AGE_COLUMNS = [f"{age}歲-{sex}" for age in range(100) for sex in ("男", "女")] + ["100歲以上-男", "100歲以上-女"]
NULLS = {"", "-", "--", "…", "N/A", "NA", "null"}
COUNTIES = {
    "63000": "臺北市", "64000": "高雄市", "65000": "新北市", "66000": "臺中市",
    "67000": "臺南市", "68000": "桃園市", "10002": "宜蘭縣", "10004": "新竹縣",
    "10005": "苗栗縣", "10007": "彰化縣", "10008": "南投縣", "10009": "雲林縣",
    "10010": "嘉義縣", "10013": "屏東縣", "10014": "臺東縣", "10015": "花蓮縣",
    "10016": "澎湖縣", "10017": "基隆市", "10018": "新竹市", "10020": "嘉義市",
    "09007": "連江縣", "09020": "金門縣",
}


def month_arg(value):
    if not re.fullmatch(r"20\d{2}-(?:0[1-9]|1[0-2])", value):
        raise argparse.ArgumentTypeError("month must be YYYY-MM")
    return value


def source_month(value):
    if re.fullmatch(r"\d{5}", value):
        return month_arg(f"{int(value[:3]) + 1911:04d}-{value[3:]}")
    if re.fullmatch(r"20\d{4}", value):
        return month_arg(value[:4] + "-" + value[4:])
    return month_arg(value)


def number(value):
    value = value.strip()
    if value in NULLS:
        return None
    if not re.fullmatch(r"(?:\d+|\d{1,3}(?:,\d{3})+)", value):
        raise ValueError("invalid source count")
    return int(value.replace(",", ""))


def add_known(*values):
    return None if None in values else sum(values)


def read_json(path):
    with (gzip.open(path, "rt", encoding="utf-8") if path.suffix == ".gz" else path.open(encoding="utf-8")) as stream:
        return json.load(stream)


def timestamp(value):
    if not isinstance(value, str):
        return False
    try:
        return datetime.fromisoformat(value).utcoffset() is not None
    except ValueError:
        return False


def safe_url(value, hosts):
    if not isinstance(value, str):
        return False
    try:
        url = urlsplit(value)
        return (url.scheme == "https" and url.hostname in hosts and not url.username
                and not url.password and url.port in {None, 443} and not url.fragment)
    except ValueError:
        return False


def source_rows(path, month):
    """Parse source cells independently, preserving their exact Unicode text."""
    villages = {}
    with path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.reader(stream, strict=True)
        header = next(reader, None)
        if header is None:
            raise ValueError("source CSV is empty")
        header = [column.strip() for column in header]
        if len(header) != 210 or len(set(header)) != 210 or set(header) != set(BASE_COLUMNS + AGE_COLUMNS):
            raise ValueError("source CSV does not have the documented complete 210 columns")
        for cells in reader:
            if not cells or not any(cell.strip() for cell in cells):
                continue
            if len(cells) != 210:
                raise ValueError(f"source CSV width differs at line {reader.line_num}")
            row = dict(zip(header, (v.strip() for v in cells)))
            code = row["區域別代碼"]
            if source_month(row["統計年月"]) != month:
                raise ValueError(f"source month differs at line {reader.line_num}")
            if not re.fullmatch(r"[0-9]{11}", code) or code[:5] not in COUNTIES:
                raise ValueError(f"invalid source village code at line {reader.line_num}")
            if code in villages:
                raise ValueError("duplicate source village code")
            if not row["區域別"].replace("台", "臺").startswith(COUNTIES[code[:5]]) or not row["村里"]:
                raise ValueError("missing/conflicting source administrative names")
            male = [number(row[f"{age}歲-男"]) for age in range(100)] + [number(row["100歲以上-男"])]
            female = [number(row[f"{age}歲-女"]) for age in range(100)] + [number(row["100歲以上-女"])]
            totals = {"male": number(row["人口數-男"]), "female": number(row["人口數-女"]), "total": number(row["人口數"])}
            for sex, ages in (("male", male), ("female", female)):
                if totals[sex] is not None and None not in ages and sum(ages) != totals[sex]:
                    raise ValueError("source exact ages differ from sex total")
            if None not in totals.values() and totals["male"] + totals["female"] != totals["total"]:
                raise ValueError("source male plus female differs from total")
            all_ages = {"male": male, "female": female, "total": [add_known(m, f) for m, f in zip(male, female)]}
            if totals["total"] is not None and None not in all_ages["total"] and sum(all_ages["total"]) != totals["total"]:
                raise ValueError("source summed total ages differ from published population")
            villages[code] = {"line": reader.line_num, "area_name": row["區域別"], "village_name": row["村里"],
                              "households": number(row["戶數"]), "population": totals,
                              "ages": all_ages}
    if not villages:
        raise ValueError("source CSV contains no villages")
    return villages


def identical(expected, actual):
    """Booleans are not counts, and a JSON float is not a population integer."""
    if type(expected) is not type(actual):
        return False
    if isinstance(expected, list):
        return len(expected) == len(actual) and all(identical(a, b) for a, b in zip(expected, actual))
    if isinstance(expected, dict):
        return expected.keys() == actual.keys() and all(identical(value, actual[key]) for key, value in expected.items())
    return expected == actual


def audit_frontend(root):
    path = root / "web/src/components/StationInfo.tsx"
    text = path.read_text(encoding="utf-8")
    age_path = root / "web/src/components/StationAge.tsx"
    age_text = age_path.read_text(encoding="utf-8") if age_path.is_file() else ""
    # This is a wording audit, not proof that the component rendered or loaded
    # its data. The integrated age card owns the scoped empty state now.
    scoped_age_text = age_text if "StationAge" in text else text
    return {"file": path.relative_to(root).as_posix(),
            "registered_population_estimate_label": "戶籍估算" in text,
            "not_ridership_label": "戶籍人口估算，非搭乘量" in text,
            "age_empty_state_is_station_scope": "尚未提供此站範圍的年齡估算" in scoped_age_text,
            "no_false_source_age_absence": "現有來源未提供年齡資料" not in text + age_text}


def review(root=ROOT, month="2026-09"):
    month_arg(month)
    root = Path(root)
    relative = f"data/population/moi_village_age_{month}.json.gz"
    raw_file = f"raw_data/moi_village_age/moi_village_age_{month}.csv"
    errors, checks = [], {}
    generated_at = None
    reviewed_source = None
    output_source = None
    record_count = None

    def check(condition, message):
        if not condition:
            errors.append(message)

    try:
        raw = root / raw_file
        receipt = read_json(root / (raw_file + ".source.json"))
        check(receipt.get("raw_file") == raw_file and receipt.get("page_url") == PAGE, "source receipt path/page differs")
        check(receipt.get("http_status") == 200 and receipt.get("bytes") == raw.stat().st_size,
              "source receipt HTTP status/byte count differs")
        check(timestamp(receipt.get("downloaded_at")), "source receipt has no timezone-aware download time")
        check(safe_url(receipt.get("requested_url"), HOSTS) and safe_url(receipt.get("effective_url"), HOSTS),
              "source receipt URLs are not official HTTPS downloads")
        with raw.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        check(receipt.get("sha256") == digest, "source raw content differs from retained receipt")
        reviewed_source = {key: value for key, value in receipt.items() if key != "sha256"}
        villages = source_rows(raw, month)
        doc = read_json(root / relative)
        meta, records = doc["metadata"], doc["records"]
        generated_at = meta.get("generated_at")
        output_source = {key: value for key, value in meta.get("source", {}).items() if key != "sha256"}
        check(doc.get("schema_version") == 1 and doc.get("dataset_id") == "moi_village_population_age_monthly", "output envelope identity differs")
        check(isinstance(records, list), "output records must be an array")
        if not isinstance(records, list):
            raise ValueError("cannot inspect non-array records")
        if any(not isinstance(record, dict) for record in records):
            raise ValueError("output records must contain only objects")
        record_count = len(records)
        check(timestamp(generated_at), "output generated_at has no timezone-aware timestamp")
        check(generated_at == receipt.get("downloaded_at"), "output snapshot reference time differs from source download time")
        check(meta.get("source") == reviewed_source, "output source provenance differs from retained receipt")
        check(meta.get("period") == month, "output statistical month differs")
        check(meta.get("source_period") == {"start": month, "end": month}, "output source period differs")
        check(meta.get("source_data_time") is None, "output invents a source update time")
        check(meta.get("age_counts_order") == AGE_LABELS, "output age bin order differs")
        check(meta.get("data_kind") == "official_published_data", "output data kind differs")
        license_info = meta.get("license", {})
        check(license_info.get("url") == "https://data.gov.tw/license", "output license differs")
        source_schema = meta.get("source_schema", {})
        check(source_schema.get("column_count") == 210 and source_schema.get("required_columns") == sorted(BASE_COLUMNS + AGE_COLUMNS)
              and source_schema.get("extra_columns") == "rejected", "output declared source schema differs")
        by_key = {}
        for record in records:
            key = (record.get("area_id"), record.get("sex"))
            if key in by_key:
                errors.append("duplicate output village/sex record")
            by_key[key] = record
        expected_keys = {(code, sex) for code in villages for sex in ("male", "female", "total")}
        check(set(by_key) == expected_keys, "missing or unknown output village/sex records")
        differences = 0
        for code, source in villages.items():
            for sex in ("male", "female", "total"):
                record = by_key.get((code, sex))
                if record is None:
                    continue
                expected = {"period": month, "year": int(month[:4]), "year_roc": int(month[:4]) - 1911,
                            "month": int(month[5:]), "area_id": code, "area_name": source["area_name"],
                            "area_level": "village", "district_id": code[:8], "county_code": code[:5],
                            "county_name": COUNTIES[code[:5]], "village_name": source["village_name"],
                            "sex": sex, "population": source["population"][sex],
                            "age_counts": source["ages"][sex], "source_file": Path(raw_file).name,
                            "source_row": source["line"], "boundary_join_status": "not_verified"}
                if sex == "total":
                    expected["households"] = source["households"]
                check(set(record) == set(expected), f"output record field set differs: {code}/{sex}")
                for field, value in expected.items():
                    if field not in record or not identical(value, record[field]):
                        if differences < 20:
                            errors.append(f"source/output field differs: {code}/{sex}/{field}")
                        differences += 1
        county_counts = Counter(code[:5] for code in villages)
        county_populations = {}
        county_totals = []
        for county in sorted(county_counts):
            values = [r["population"]["total"] for code, r in villages.items() if code.startswith(county)]
            county_populations[county] = None if None in values else sum(values)
            county_totals.append({"county_code": county, "county_name": COUNTIES[county], "village_count": county_counts[county],
                                  "population": county_populations[county], "population_known_sum": sum(v for v in values if v is not None),
                                  "population_unknown_villages": sum(v is None for v in values)})
        values = [r["population"]["total"] for r in villages.values()]
        reconciliation = {"sex_population": {"verified": 0, "inconclusive": 0},
                          "age_population": {"verified": 0, "inconclusive": 0}}
        missing_cells = 0
        for village in villages.values():
            missing_cells += sum(value is None for value in [village["households"], *village["population"].values(),
                                                            *village["ages"]["male"], *village["ages"]["female"]])
            kind = "inconclusive" if None in village["population"].values() else "verified"
            reconciliation["sex_population"][kind] += 1
            for sex in ("male", "female", "total"):
                kind = "inconclusive" if village["population"][sex] is None or None in village["ages"][sex] else "verified"
                reconciliation["age_population"][kind] += 1
        expected_quality = {"source_row_count": len(villages), "village_count": len(villages), "record_count": len(expected_keys),
                            "source_schema_column_count": 210, "county_count": len(county_counts), "unknown_county_count": 0,
                            "missing_source_numeric_cells": missing_cells, "population": add_known(*values),
                            "population_known_sum": sum(v for v in values if v is not None), "population_unknown_villages": sum(v is None for v in values),
                            "county_totals": county_totals, "reconciliation": reconciliation}
        check(set(county_counts) == set(COUNTIES), "source snapshot does not contain all 22 official county prefixes")
        for field, expected in expected_quality.items():
            check(identical(expected, doc.get("quality", {}).get(field)), "output quality aggregate differs: " + field)
            check(identical(expected, meta.get("validation", {}).get(field)), "output metadata validation aggregate differs: " + field)
        check(meta.get("validation", {}).get("boundary_join_status") == "not_verified", "output claims a verified boundary join")
        manifest = read_json(root / "raw_data/moi_village_age/manifest.json")
        check(manifest.get("metadata", {}).get("period") == month, "raw source manifest month differs")
        check(reviewed_source in manifest.get("records", []), "raw source receipt is absent from source manifest")
        catalog_file = f"raw_data/moi_village_age/moi_catalog_{month}.html"
        catalog = root / catalog_file
        catalog_receipt = read_json(root / (catalog_file + ".source.json"))
        check(catalog_receipt.get("raw_file") == catalog_file and catalog_receipt.get("page_url") == PAGE
              and catalog_receipt.get("requested_url") == PAGE and catalog_receipt.get("effective_url") == PAGE,
              "catalog receipt path/page differs")
        check(catalog_receipt.get("http_status") == 200 and catalog_receipt.get("bytes") == catalog.stat().st_size
              and timestamp(catalog_receipt.get("downloaded_at")), "catalog receipt status, byte count or time differs")
        with catalog.open("rb") as stream:
            check(hashlib.file_digest(stream, "sha256").hexdigest() == catalog_receipt.get("sha256"),
                  "catalog raw content differs from retained receipt")
        check({key: value for key, value in catalog_receipt.items() if key != "sha256"} in manifest.get("records", []),
              "catalog receipt is absent from raw source manifest")
        check(receipt["requested_url"] in catalog.read_text(encoding="utf-8-sig"), "official catalog no longer retains the selected CSV URL")
        checks.update({"source_column_count": 210, "source_village_count": len(villages),
                       "expected_sex_record_count": 3 * len(villages), "source_output_cell_difference_count": differences,
                       "all_source_villages_have_exactly_three_sexes": set(by_key) == expected_keys and len(records) == len(expected_keys),
                       "all_101_age_bins_and_source_fields_match": differences == 0,
                       "source_county_village_counts": dict(sorted(county_counts.items())),
                       "source_county_population_totals": county_populations,
                       "population_total_counting_only_total_sex": add_known(*(r["population"]["total"] for r in villages.values())),
                       "source_missing_numeric_cells": missing_cells, "source_reconciliation": reconciliation,
                       "aggregation_sex": "total_only", "boundary_join_status": "not_verified"})
        checks["frontend"] = audit_frontend(root)
        check(all(value for key, value in checks["frontend"].items() if key != "file"), "frontend population/age scope labels differ")
    except (OSError, ValueError, KeyError, TypeError, AttributeError, csv.Error, argparse.ArgumentTypeError) as exc:
        errors.append(f"review could not complete: {type(exc).__name__}: {exc}")
    status = "needs_correction" if errors else "pass_with_limitations"
    limitations = ["戶籍年齡人口是居住人口參考，不是實際乘客、白天活動人口、通勤 OD 或公車搭乘需求。",
                   "只加總 sex=total；不可再加上 male/female，100_plus 是開放上限，不是精確 100 歲。",
                   "本人口月檔維持 boundary_join_status=not_verified；清冊／特定版本里界的另行核對見 national_village_age_review.json，不推定最小統計區或公車站點 ID 關聯。",
                   "站點人口仍為既有統計區面積分攤估算；本資料未接入站點年齡估算或需求權重，前端維持此範圍的年齡缺資料狀態。",
                   "此報告僅驗證所列本地官方 CSV 與輸出快照，不表示政府來源之後未修訂。"]
    checks.update({"failure_count": len(errors), "failures": errors})
    return {"schema_version": 1, "dataset_id": "independent_moi_village_age_review", "metadata": {
                "reviewer_agent": "/root/interface_audit", "independent_of_producer": True,
                "reviewed_at": datetime.now(ZoneInfo("Asia/Taipei")).isoformat(timespec="seconds"),
                "reviewed_generated_at": generated_at, "reviewed_source": reviewed_source,
                "period": month, "status": status, "record_count": 1,
                "script": "scripts/review_moi_village_age.py",
                "method": "Independent stdlib CSV/gzip field, type, Unicode, age, sex, month, null and source receipt reconciliation; no collector imports.",
                "limitations": limitations}, "records": [{"file": relative, "output_path": relative,
                    "reviewed_generated_at": generated_at, "source": output_source,
                    "record_count": record_count, "review_status": status, "checks": checks, "limitations": limitations}]}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--month", required=True, type=month_arg)
    args = parser.parse_args(argv)
    result = review(args.root, args.month)
    path = args.root / "data/quality/moi_village_age_review.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, ensure_ascii=False, allow_nan=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": result["metadata"]["status"], "report": str(path),
                      "failures": result["records"][0]["checks"]["failures"]}, ensure_ascii=False))
    return 1 if result["metadata"]["status"] == "needs_correction" else 0


if __name__ == "__main__":
    raise SystemExit(main())
