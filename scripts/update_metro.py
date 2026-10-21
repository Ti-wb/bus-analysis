#!/usr/bin/env python3
"""Build compact Taipei Metro station/hour entries and exits, 2025-10–2026-09.

Large OD CSVs are temporary conversion/review inputs. Final data contains station
totals, not OD pairs. Only source links, periods and retrieval times are retained;
no hashes are saved. --offline recombines the small saved monthly aggregates.
"""
from __future__ import annotations
import argparse
import calendar
from concurrent.futures import ProcessPoolExecutor, as_completed
import csv
from datetime import date, datetime
import itertools
import json
from multiprocessing import get_context
from pathlib import Path
import re
import shutil
import subprocess
import sys
from urllib.parse import urljoin, urlparse
from zoneinfo import ZoneInfo
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]
RAW, OUT = ROOT / "raw_data/metro", ROOT / "data/metro"
TZ = ZoneInfo("Asia/Taipei")
OD_PAGE = "https://data.taipei/dataset/detail?id=63f31c7e-7fc3-418b-bd82-b95158755b4d"
EXIT_PAGE = "https://data.taipei/dataset/detail?id=b1398491-07fe-40da-bb5a-ae73f4e45984"
HOSTS = {"data.taipei", "tcgmetro.blob.core.windows.net"}
START, END = "2025-10", "2026-09"


def now():
    return datetime.now(TZ).isoformat(timespec="seconds")


def write_json(path, obj, compact=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".part")
    with temp.open("w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, allow_nan=False,
                  indent=None if compact else 2, separators=(",", ":") if compact else None)
        f.write("\n")
    temp.replace(path)


def source_fields(obj):
    return {k: obj.get(k) for k in ["page_url", "download_url", "raw_file", "downloaded_at", "source_data_time"]}


def fetch(url, name, page_url, offline=False, refresh=False):
    if urlparse(url).scheme != "https" or urlparse(url).hostname not in HOSTS:
        raise ValueError(f"Unexpected source URL: {url}")
    path = RAW / name
    receipt_path = path.with_suffix(path.suffix + ".source.json")
    if path.exists() and receipt_path.exists() and not refresh:
        receipt = source_fields(json.loads(receipt_path.read_text(encoding="utf-8")))
        if receipt["download_url"] == url and path.stat().st_size:
            write_json(receipt_path, receipt)
            return path, receipt
    if offline:
        raise ValueError(f"Original source unavailable offline: {name}")
    if shutil.disk_usage(ROOT).free < 5 * 1024 ** 3:
        raise RuntimeError("Less than 5 GiB disk space remains.")
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".part")
    try:
        cmd = ["curl", "--fail", "--location", "--silent", "--show-error",
               "--proto", "=https", "--proto-redir", "=https", "--connect-timeout", "20",
               "--max-time", "900", "--retry", "3", "--retry-delay", "2",
               "--output", str(temp), "--write-out", "%{json}", url]
        result = subprocess.run(cmd, text=True, capture_output=True, timeout=1900)
        if result.returncode:
            raise RuntimeError(f"Download failed {name}: {result.stderr.strip()}")
        info = json.loads(result.stdout)
        if info["http_code"] != 200 or not temp.stat().st_size:
            raise ValueError(f"Invalid response for {name}")
        if urlparse(info["url_effective"]).hostname not in HOSTS:
            raise ValueError(f"Unexpected redirect for {name}")
        receipt = {"page_url": page_url, "download_url": info["url_effective"],
                   "raw_file": str(path.relative_to(ROOT)), "downloaded_at": now(), "source_data_time": None}
        temp.replace(path)
        write_json(receipt_path, receipt)
        return path, receipt
    finally:
        temp.unlink(missing_ok=True)


def resource(page, page_url):
    soup = BeautifulSoup(page.read_text(encoding="utf-8"), "html.parser")
    links = []
    for a in soup.select("a[href]"):
        if a.get_text(strip=True) != "下載":
            continue
        cells = a.find_parent("tr").find_all("td")
        if len(cells) >= 4 and "CSV" in cells[2].get_text():
            links.append(urljoin(page_url, a["href"]))
    if len(links) != 1:
        raise ValueError(f"Expected one CSV resource at {page_url}")
    return links[0]


def read_index(path, start, end):
    records = []
    with path.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames != ["SeqNo", "西元年", "月", "URL", "說明/備註"]:
            raise ValueError(f"Unexpected OD index header: {reader.fieldnames}")
        for row in reader:
            year, month = int(row["西元年"]), int(row["月"])
            date(year, month, 1)
            period = f"{year:04d}-{month:02d}"
            if start <= period <= end:
                records.append({"period": period, "download_url": row["URL"].strip().replace("http://", "https://", 1)})
    if len({r["period"] for r in records}) != len(records):
        raise ValueError("Duplicate source months")
    return sorted(records, key=lambda r: r["period"])


def parse_month(path, source, period):
    """Aggregate every original OD cell; incomplete station totals become null."""
    target = RAW / "monthly_flows" / f"{period}.json"
    if target.exists():
        cached = json.loads(target.read_text(encoding="utf-8"))
        if cached["metadata"]["source"]["downloaded_at"] == source["downloaded_at"]:
            return {"period": period, "intermediate_file": str(target.relative_to(ROOT)), **cached["quality"]}
    reference = {**source_fields(source), "raw_file": None, "period": period}
    seen_groups, flows = set(), {}
    entry_names, exit_names, entry_index, exit_index = [], [], {}, {}
    row_count = total = zero_count = missing_values = 0
    with path.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.reader(f)
        fields = next(reader)
        if fields != ["日期", "時段", "進站", "出站", "人次"]:
            raise ValueError(f"Unexpected OD header in {period}: {fields}")
        groups = itertools.groupby(reader, key=lambda r: (r[0], r[1], r[2]))
        for key, rows in groups:
            day_raw, hour_raw, origin = key
            day = date.fromisoformat(day_raw.replace("/", "-")).isoformat()
            hour = int(hour_raw)
            if not 0 <= hour <= 23 or not origin:
                raise ValueError(f"Invalid hour/origin in {period}: {key}")
            canonical_key = (day, hour, origin)
            if canonical_key in seen_groups:
                raise ValueError(f"Duplicate OD origin group: {canonical_key}")
            seen_groups.add(canonical_key)
            if origin not in entry_index:
                entry_index[origin] = len(entry_names)
                entry_names.append(origin)
            origin_i = entry_index[origin]
            entries, exits, entry_seen, exit_seen, entry_nulls, exit_nulls = flows.setdefault((day, hour), [[] for _ in range(6)])
            for vector in [entries, entry_seen, entry_nulls]:
                vector.extend([0] * (origin_i + 1 - len(vector)))
            destinations_seen = set()
            for row in rows:
                if len(row) != 5 or not row[3]:
                    raise ValueError(f"Unexpected OD row in {period}")
                if row[3] not in exit_index:
                    exit_index[row[3]] = len(exit_names)
                    exit_names.append(row[3])
                destination_i = exit_index[row[3]]
                if destination_i >= len(exits):
                    for vector in [exits, exit_seen, exit_nulls]:
                        vector.extend([0] * (destination_i + 1 - len(vector)))
                if destination_i in destinations_seen:
                    raise ValueError(f"Duplicate OD pair: {canonical_key}, {row[3]}")
                destinations_seen.add(destination_i)
                value = row[4].strip()
                if value in {"", "-", "--", "…", "N/A"}:
                    entry_nulls[origin_i] += 1
                    exit_nulls[destination_i] += 1
                    missing_values += 1
                else:
                    number = int(value.replace(",", ""))
                    if number < 0:
                        raise ValueError(f"Negative OD count in {period}")
                    entries[origin_i] += number
                    exits[destination_i] += number
                    total += number
                    zero_count += number == 0
                entry_seen[origin_i] += 1
                exit_seen[destination_i] += 1
                row_count += 1
    records, incomplete = [], []
    for (day, hour), (entries, exits, entry_seen, exit_seen, entry_nulls, exit_nulls) in sorted(flows.items()):
        for direction, names, expected, counts, seen, nulls in [
            ("entries", entry_names, len(exit_names), entries, entry_seen, entry_nulls),
            ("exits", exit_names, len(entry_names), exits, exit_seen, exit_nulls)]:
            for vector in [counts, seen, nulls]:
                vector.extend([0] * (len(names) - len(vector)))
            for i in range(len(names)):
                if seen[i] != expected or nulls[i]:
                    incomplete.append({"date": day, "hour": hour, "station_name": names[i], "direction": direction,
                                       "expected_od_cells": expected,
                                       "observed_od_cells": seen[i], "missing_value_cells": nulls[i], "known_passenger_trips_sum": counts[i]})
                    counts[i] = None
        records.append([day, hour, entries, exits])
    days = sorted({r[0] for r in records})
    quality = {"source_row_count": row_count, "source_passenger_trips_sum": total,
               "source_zero_cells": zero_count, "source_missing_value_cells": missing_values,
               "date_hour_count": len(records), "entry_station_count": len(entry_names), "exit_station_count": len(exit_names),
               "date_range": {"start": days[0], "end": days[-1]},
               "out_of_month_date_hours": [[r[0], r[1]] for r in records if not r[0].startswith(period)],
               "incomplete_station_totals": incomplete}
    obj = {"schema_version": 1, "dataset_id": f"metro_station_flows_{period}",
           "metadata": {"source": reference, "entry_station_names": entry_names, "exit_station_names": exit_names,
                        "record_fields": ["service_date", "hour", "entries", "exits"], "units": "人次", "timezone": "Asia/Taipei",
                        "aggregation": "entries按進站加總所有出站，exits按出站加總所有進站；必要來源列缺漏或缺值則該站總量為null。"},
           "records": records, "quality": quality}
    write_json(target, obj, compact=True)
    return {"period": period, "intermediate_file": str(target.relative_to(ROOT)), **quality}


def month_job(record, offline, refresh):
    period = record["period"]
    intermediate = RAW / "monthly_flows" / f"{period}.json"
    if intermediate.exists() and not refresh:
        cached = json.loads(intermediate.read_text(encoding="utf-8"))
        return {"period": period, "intermediate_file": str(intermediate.relative_to(ROOT)), **cached["quality"]}
    path, source = fetch(record["download_url"], f"od/{period}.csv", OD_PAGE, offline, refresh)
    return parse_month(path, source, period)


def combine_months(months, start, end):
    dictionaries, by_time, sources, source_quality = [[], []], {}, [], []
    for result in sorted(months, key=lambda r: r["period"]):
        obj = json.loads((ROOT / result["intermediate_file"]).read_text(encoding="utf-8"))
        sources.append(obj["metadata"]["source"])
        source_quality.append({"period": result["period"], **obj["quality"]})
        names = [obj["metadata"]["entry_station_names"], obj["metadata"]["exit_station_names"]]
        for direction in range(2):
            for name in names[direction]:
                if name not in dictionaries[direction]:
                    dictionaries[direction].append(name)
        for record in obj["records"]:
            if start <= record[0][:7] <= end:
                by_time.setdefault((record[0], record[1]), []).append((result["period"], names, record))
    records, conflicts, duplicates = [], [], []
    for (day, hour), candidates in sorted(by_time.items()):
        if len(candidates) > 1:
            duplicates.append({"date": day, "hour": hour, "source_months": [c[0] for c in candidates]})
        values = [{name: [] for name in dictionary} for dictionary in dictionaries]
        for period, names, record in candidates:
            for direction in range(2):
                for i, name in enumerate(names[direction]):
                    values[direction][name].append((period, record[direction + 2][i]))
        vectors = [[], []]
        for direction in range(2):
            for name in dictionaries[direction]:
                known = {v for _, v in values[direction][name] if v is not None}
                vectors[direction].append(next(iter(known)) if len(known) == 1 else None)
                if len(known) > 1:
                    conflicts.append({"date": day, "hour": hour, "station_name": name,
                                      "direction": "entries" if direction == 0 else "exits",
                                      "source_values": [{"source_month": p, "value": v} for p, v in values[direction][name]]})
        records.append([day, hour, vectors[0], vectors[1]])
    quality = {"source_month_count": len(months), "date_hour_count": len(records),
               "entry_station_name_count": len(dictionaries[0]), "exit_station_name_count": len(dictionaries[1]),
               "station_date_hour_value_count": len(records) * sum(len(d) for d in dictionaries),
               "entries_sum": sum(v for r in records for v in r[2] if v is not None),
               "exits_sum": sum(v for r in records for v in r[3] if v is not None),
               "null_entries": sum(v is None for r in records for v in r[2]),
               "null_exits": sum(v is None for r in records for v in r[3]),
               "duplicate_source_date_hours": duplicates, "conflicting_station_totals": conflicts}
    limitations = ["本檔只有每日期×小時×站的進站及出站加總，並非完整OD；無法回推進出站配對人次。",
                   "保留捷運跨臺北／新北路網以支援臺北接駁分析，未按站名猜測行政界。",
                   "進站與出站來源名稱集合不對稱，分別使用entry_station_names與exit_station_names，未猜測跨字典的站ID對應。",
                   "站名保留G/O/BL等來源前綴，未逕自併站或對應公車站ID。捷運出站人次不等於公車轉乘人次。",
                   "零是來源觀測零；缺列、缺值、不在月份站名字典或跨檔衝突均為null。未出現日期／小時不補零。",
                   "來源跨月列依實際日期納入期間；跨檔同時段相同值去重，衝突值為null並保留候選。",
                   "大型原始CSV僅暫存供轉換與獨立巡檢，最後只保留清理資料及來源連結。"]
    period = {"start": start + "-01", "end": end + f"-{calendar.monthrange(int(end[:4]), int(end[5:]))[1]:02d}"}
    obj = {"schema_version": 1, "dataset_id": "taipei_metro_station_hourly_flows",
           "metadata": {"period": period, "timezone": "Asia/Taipei", "source_data_time": None, "sources": sources,
                        "grain": "日期 × 小時 × 捷運站的進站與出站人次", "record_fields": ["service_date", "hour", "entries", "exits"],
                        "entry_station_names": dictionaries[0], "exit_station_names": dictionaries[1],
                        "vector_definition": "entries[i]依entry_station_names[i]；exits[j]依exit_station_names[j]。進出站來源名稱集合不同，不可按索引相互配對。",
                        "units": {"entries": "人次", "exits": "人次"}, "contains_full_od_pairs": False,
                        "within_taipei_filter_applied": False, "limitations": limitations}, "records": records, "quality": quality}
    output = OUT / "station_hourly_flows.json"
    write_json(output, obj, compact=True)
    write_json(OUT / "source_references.json", {"schema_version": 1, "dataset_id": "metro_od_source_references",
               "metadata": {"period": {"start": start, "end": end}, "page_url": OD_PAGE,
                            "grain": "一個OD來源月檔", "source_data_time": None,
                            "units": {"source_row_count": "列", "source_passenger_trips_sum": "人次"},
                            "note": "OD來源與轉換核對數字；交付資料僅站進出加總，非完整OD。"},
               "records": [{"source": s, "source_row_count": q["source_row_count"], "source_passenger_trips_sum": q["source_passenger_trips_sum"],
                            "date_range": q["date_range"], "out_of_month_date_hours": q["out_of_month_date_hours"],
                            "incomplete_station_totals": q["incomplete_station_totals"]} for s, q in zip(sources, source_quality)]})
    return {"status": "normalized_station_flows_only", "output_files": [str(output.relative_to(ROOT)), "data/metro/source_references.json"],
            "record_count": len(records), "station_date_hour_value_count": quality["station_date_hour_value_count"], "period": period,
            "limitations": limitations, "quality": quality}


def build_exits(path, source):
    raw = path.read_bytes()
    try:
        text, encoding = raw.decode("utf-8-sig"), "UTF-8"
    except UnicodeDecodeError:
        text, encoding = raw.decode("cp950"), "CP950"
    records, ids, dates = [], set(), set()
    ambiguous = 0
    reader = csv.DictReader(text.splitlines())
    if reader.fieldnames != ["序號", "車站", "出口", "公車名稱", "最後更新日期"]:
        raise ValueError(f"Unexpected metro exit header: {reader.fieldnames}")
    for row in reader:
        source_id = row["序號"].strip()
        if not source_id or source_id in ids:
            raise ValueError("Missing or repeated exit source sequence")
        ids.add(source_id)
        label = row["公車名稱"].strip()
        parts = label.split("-")
        if len(parts) == 2 and all(parts):
            route, destination = parts
            status = "single_hyphen_split_candidate"
        else:
            route = destination = None
            status = "ambiguous_preserved_unsplit"
            ambiguous += 1
        value = row["最後更新日期"].strip()
        updated = datetime.strptime(value, "%Y%m%d").date().isoformat() if value else None
        if updated:
            dates.add(updated)
        records.append({"source_sequence": source_id, "station_name": row["車站"].strip() or None,
                        "exit_name": row["出口"].strip() or None, "bus_label": label or None,
                        "bus_route_name_candidate": route, "direction_destination_candidate": destination,
                        "label_parse_status": status, "source_updated_date": updated,
                        "bus_route_id": None, "bus_stop_id": None, "walking_distance_m": None})
    duplicates = len(records) - len({(r["station_name"], r["exit_name"], r["bus_label"]) for r in records})
    output = OUT / "exit_bus_connections.json"
    limitations = ["來源沒有RouteID、StopID或步行距離，未直接連結公車資料。",
                   "單連字號標籤拆為名稱及方向候選，仍需比對；多連字號標籤不猜測。",
                   "保留來源重複關係列及序號，筆數不是獨立路線數。"]
    obj = {"schema_version": 1, "dataset_id": "taipei_metro_exit_bus_connections",
           "metadata": {"source": source_fields(source), "source_data_time": max(dates) if dates else None,
                        "source_encoding": encoding, "grain": "來源列：車站 × 出口 × 公車名稱／方向標籤",
                        "units": {"walking_distance_m": "公尺（來源未提供，為null）"},
                        "period": {"source_updated_dates": sorted(dates)}, "limitations": limitations},
           "records": records, "quality": {"record_count": len(records), "ambiguous_bus_labels": ambiguous,
                                          "duplicate_station_exit_bus_labels": duplicates}}
    write_json(output, obj, compact=True)
    return {"status": "normalized_with_unmatched_route_identifiers", "output_files": [str(output.relative_to(ROOT))],
            "record_count": len(records), "period": obj["metadata"]["period"], "limitations": limitations, "quality": obj["quality"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--offline", action="store_true", help="recombine saved aggregates without downloading")
    parser.add_argument("--refresh", action="store_true", help="redownload sources; ordinary runs reuse saved aggregates")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--start-month", default=START)
    parser.add_argument("--end-month", default=END)
    args = parser.parse_args()
    if (not re.fullmatch(r"\d{4}-\d{2}", args.start_month) or not re.fullmatch(r"\d{4}-\d{2}", args.end_month)
            or args.start_month > args.end_month):
        raise ValueError("Invalid requested month range")
    RAW.mkdir(parents=True, exist_ok=True)
    OUT.mkdir(parents=True, exist_ok=True)
    inputs = []
    for name, url in [("od", OD_PAGE), ("exit_bus", EXIT_PAGE)]:
        page, _ = fetch(url, name + ".html", url, args.offline, args.refresh)
        inputs.append(fetch(resource(page, url), "od_month_index.csv" if name == "od" else "exit_bus.csv", url, args.offline, args.refresh))
    selected = read_index(inputs[0][0], args.start_month, args.end_month)
    start_year, start_number = map(int, args.start_month.split("-"))
    end_year, end_number = map(int, args.end_month.split("-"))
    date(start_year, start_number, 1)
    date(end_year, end_number, 1)
    required = [f"{value // 12:04d}-{value % 12 + 1:02d}"
                for value in range(start_year * 12 + start_number - 1, end_year * 12 + end_number)]
    if [item["period"] for item in selected] != required:
        raise ValueError("Official source index does not contain every requested month")
    exits = build_exits(*inputs[1])
    results, failures = [], []
    # Avoid Python 3.14's forkserver socket requirement in restricted Linux shells.
    with ProcessPoolExecutor(max_workers=args.workers, mp_context=get_context("fork")) as pool:
        jobs = {pool.submit(month_job, r, args.offline, args.refresh): r for r in selected}
        for future in as_completed(jobs):
            period = jobs[future]["period"]
            try:
                result = future.result()
                results.append(result)
                print(f"{len(results)}/{len(selected)} {period}: {result['source_row_count']:,} OD rows -> {result['date_hour_count']:,} flow vectors", flush=True)
            except Exception as exc:
                failures.append({"period": period, "error": str(exc)})
                print(f"FAILED {period}: {exc}", file=sys.stderr, flush=True)
    if failures:
        write_json(RAW / "incomplete_months.json", {"failures": failures})
        return 1
    flows = combine_months(results, args.start_month, args.end_month)
    write_json(RAW / "delivery.json", {"schema_version": 1, "datasets": {"metro_hourly_od": flows, "metro_exit_bus_connections": exits}})
    refs = json.loads((OUT / "source_references.json").read_text(encoding="utf-8"))
    write_json(RAW / "manifest.json", {"schema_version": 1, "sources": [r["source"] for r in refs["records"]] + [source_fields(inputs[1][1])]})
    (RAW / "incomplete_months.json").unlink(missing_ok=True)
    print(json.dumps({"months": len(results), "date_hours": flows["record_count"], "station_hour_values": flows["station_date_hour_value_count"], "exit_bus_records": exits["record_count"]}), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
