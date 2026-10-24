#!/usr/bin/env python3
"""Download the official government office calendar (行政機關辦公日曆表) and normalize it.

Used to tell working weekdays from holidays and make-up days when averaging metro flows.
"""
import argparse
import csv
from datetime import date, datetime
import io
import json
from pathlib import Path
import subprocess
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "raw_data/calendar"
OUT = ROOT / "data/calendar"
TZ = ZoneInfo("Asia/Taipei")
PAGE_URL = "https://data.gov.tw/dataset/14718"
# One file per ROC year; 114 uses the revised (2025-10-20) release, which is Big5.
SOURCES = {
    "office_calendar_114.csv": {
        "year": 2025,
        "encoding": "big5",
        "description": "114年中華民國政府行政機關辦公日曆表(1141020更新)",
        # The revision date only appears in the source title (民國 114-10-20).
        "source_updated_at": "2025-10-20",
        "url": "https://www.dgpa.gov.tw/FileConversion?filename=dgpa/files/202510/b84cb88a-803c-4621-a843-d637b2775615.csv&nfix=&name=114%e5%b9%b4%e4%b8%ad%e8%8f%af%e6%b0%91%e5%9c%8b%e6%94%bf%e5%ba%9c%e8%a1%8c%e6%94%bf%e6%a9%9f%e9%97%9c%e8%be%a6%e5%85%ac%e6%97%a5%e6%9b%86%e8%a1%a8(1141020%e6%9b%b4%e6%96%b0).csv",
    },
    "office_calendar_115.csv": {
        "year": 2026,
        "encoding": "utf-8-sig",
        "description": "115年中華民國政府行政機關辦公日曆表",
        "source_updated_at": None,
        "url": "https://www.dgpa.gov.tw/FileConversion?filename=dgpa/files/202506/a52331bd-a189-466b-b0f0-cae3062bbf74.csv&nfix=&name=115%e5%b9%b4%e4%b8%ad%e8%8f%af%e6%b0%91%e5%9c%8b%e6%94%bf%e5%ba%9c%e8%a1%8c%e6%94%bf%e6%a9%9f%e9%97%9c%e8%be%a6%e5%85%ac%e6%97%a5%e6%9b%86%e8%a1%a8.csv",
    },
}
WEEKDAYS = "一二三四五六日"
LIMITATIONS = [
    "這是行政機關辦公日曆，不含颱風、天災等臨時停班停課，也不代表學校行事曆或各業別的上班日。",
    "is_holiday 依來源「是否放假」：2 為放假、0 為上班；週末補班日 is_holiday 為 false。",
    "note 為來源備註原文；空白為 null。",
]


def now():
    return datetime.now(TZ).isoformat(timespec="seconds")


def write_json(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".part")
    with temp.open("w", encoding="utf-8", newline="\n") as f:
        json.dump(obj, f, ensure_ascii=False, allow_nan=False, indent=2)
        f.write("\n")
    temp.replace(path)


def read_json(path):
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def fetch(offline):
    manifest_path = RAW / "manifest.json"
    manifest = read_json(manifest_path) if manifest_path.exists() else {}
    for name, source in SOURCES.items():
        path = RAW / name
        if offline:
            if name not in manifest or not path.exists() or not path.stat().st_size:
                raise ValueError(f"Missing offline source: {path.relative_to(ROOT).as_posix()}")
            continue
        if urlparse(source["url"]).scheme != "https":
            raise ValueError(f"HTTPS required: {source['url']}")
        RAW.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix(".part")
        command = ["curl", "--fail", "--location", "--silent", "--show-error",
                   "--proto", "=https", "--proto-redir", "=https",
                   "--connect-timeout", "20", "--max-time", "120", "--retry", "2",
                   "--user-agent", "bus-analysis/1.0", "--output", str(temp), "--write-out", "%{json}", source["url"]]
        result = subprocess.run(command, capture_output=True, text=True, timeout=400)
        if result.returncode:
            temp.unlink(missing_ok=True)
            raise RuntimeError(result.stderr.strip())
        stat = json.loads(result.stdout)
        temp.replace(path)
        manifest[name] = {
            "page_url": PAGE_URL,
            "description": source["description"],
            "download_url": source["url"],
            "raw_file": path.relative_to(ROOT).as_posix(),
            "downloaded_at": now(),
            "bytes": path.stat().st_size,
            "content_type": stat.get("content_type"),
            "source_encoding": source["encoding"],
        }
    write_json(manifest_path, manifest)
    return manifest


def parse(name, source):
    text = (RAW / name).read_bytes().decode(source["encoding"])
    rows = list(csv.reader(io.StringIO(text)))
    header = [h.strip().lstrip("﻿") for h in rows[0]]
    if header != ["西元日期", "星期", "是否放假", "備註"]:
        raise ValueError(f"{name}: unexpected header {header}")
    records = []
    for row in rows[1:]:
        if not any(cell.strip() for cell in row):
            continue
        raw_date, weekday, code, note = (cell.strip() for cell in row)
        day = date(int(raw_date[:4]), int(raw_date[4:6]), int(raw_date[6:]))
        if day.year != source["year"]:
            raise ValueError(f"{name}: {raw_date} outside {source['year']}")
        if WEEKDAYS[day.isoweekday() - 1] != weekday:
            raise ValueError(f"{name}: weekday mismatch on {raw_date}")
        if code not in {"0", "2"}:
            raise ValueError(f"{name}: unknown holiday code {code!r} on {raw_date}")
        records.append({
            "date": day.isoformat(),
            "weekday_iso": day.isoweekday(),
            "is_holiday": code == "2",
            "source_holiday_code": code,
            "note": note or None,
            "source_file": (RAW / name).relative_to(ROOT).as_posix(),
        })
    expected = (date(source["year"], 12, 31) - date(source["year"], 1, 1)).days + 1
    if len(records) != expected:
        raise ValueError(f"{name}: {len(records)} days, expected {expected}")
    return records


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--offline", action="store_true", help="rebuild from raw_data/calendar/ without downloading")
    args = parser.parse_args()
    manifest = fetch(args.offline)
    records = [r for name, source in SOURCES.items() for r in parse(name, source)]
    period = {"start": records[0]["date"], "end": records[-1]["date"]}
    output = OUT / "office_calendar.json"
    write_json(output, {
        "schema_version": 1,
        "dataset_id": "tw_government_office_calendar",
        "metadata": {
            "title": "中華民國政府行政機關辦公日曆表",
            "sources": [{k: manifest[name][k] for k in ("page_url", "description", "download_url", "raw_file", "downloaded_at")}
                        | {"source_updated_at": source["source_updated_at"]} for name, source in SOURCES.items()],
            "source_updated_at_note": "114 年修訂版的 2025-10-20 取自來源檔名「1141020更新」；115 年檔未標更新日，為 null",
            "source_period": period,
            "grain": "one record per calendar date",
            "missing_value_policy": "Blank source notes are null.",
            "field_notes": {"source_holiday_code": "來源「是否放假」原值：0 上班、2 放假"},
            "limitations": LIMITATIONS,
        },
        "records": records,
    })
    write_json(RAW / "delivery.json", {
        "schema_version": 1,
        "prepared_at": now(),
        "datasets": [{
            "dataset_id": "tw_government_office_calendar",
            "status": "normalized",
            "output_files": [output.relative_to(ROOT).as_posix()],
            "record_count": len(records),
            "period": period,
            "limitations": LIMITATIONS,
            "bytes": output.stat().st_size,
        }],
    })
    holidays = sum(r["is_holiday"] for r in records)
    workdays = sum(not r["is_holiday"] for r in records)
    print(f"{output.relative_to(ROOT).as_posix()}: {len(records)} days, {holidays} holidays, {workdays} working days")


if __name__ == "__main__":
    main()
