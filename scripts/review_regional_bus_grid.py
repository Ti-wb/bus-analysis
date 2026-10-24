#!/usr/bin/env python3
"""Independently reconcile every regional grid cell/hour to retained TDX data.

No generator helpers are imported. A report is replaced even after failure, so
CI cannot accidentally publish a previously successful audit.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime
import gzip
import json
import math
from pathlib import Path
import re
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from pyproj import Geod
from shapely.geometry import box, shape
from shapely.ops import unary_union
from shapely.prepared import prep

ROOT = Path(__file__).resolve().parents[1]
REPORT = Path("data/quality/regional_bus_grid_review.json")
EXPECTED = (100, 150, 250, 500)
FIELDS = ["ix", "iy", "stop_count", "subroute_count", "unscheduled_subroute_count", "weekday", "saturday", "sunday"]
DAYS = {"weekday": "wednesday", "saturday": "saturday", "sunday": "sunday"}
WEEK = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
SCOPES = ("Taipei", "NewTaipei", "Keelung", "InterCity")
CITY_CODES = ["63000", "65000", "10017"]
GEOMETRIES = ["map-data/admin/cities.geojson", "map-data/admin/keelung_villages.geojson"]
UNITS = {"frequency": "estimated planned departure trips per hour", "position": "WGS84 longitude/latitude degrees"}


def load(path):
    with (gzip.open(path, "rt", encoding="utf-8") if path.suffix == ".gz"
          else path.open("r", encoding="utf-8")) as stream:
        return json.load(stream)


def require(condition, message):
    if not condition:
        raise ValueError(message)


def number(value):
    return type(value) in (int, float) and math.isfinite(value)


def timestamp(value):
    if not isinstance(value, str) or "T" not in value:
        return False
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).utcoffset() is not None
    except ValueError:
        return False


def service_key(row):
    require(all(isinstance(row.get(k), str) and row[k] for k in ("route_uid", "sub_route_uid")),
            "TDX service IDs must be nonempty strings")
    return row["route_uid"], row["sub_route_uid"], str(row.get("direction"))


def minute(value):
    if not isinstance(value, str) or not re.fullmatch(r"\d+:\d+(?::\d+)?", value):
        return None
    parts = [int(x) for x in value.split(":")]
    if parts[0] > 47 or parts[1] > 59 or len(parts) == 3 and parts[2] > 59:
        return None
    return parts[0] * 60 + parts[1]


def calendar_valid(calendar):
    return isinstance(calendar, dict) and all(type(calendar.get(d)) is int and calendar[d] in (0, 1) for d in WEEK)


def origin_minute(entry):
    stops = entry.get("stop_times")
    if isinstance(stops, list) and stops:
        return minute(stops[0].get("departure_time") or stops[0].get("arrival_time"))
    return minute(entry.get("departure_time"))


def frequency_series(entry):
    start, end = minute(entry.get("start_time")), minute(entry.get("end_time"))
    bounds = [entry.get(k) for k in ("min_headway_mins", "max_headway_mins")
              if number(entry.get(k)) and entry[k] > 0]
    if start is None or end is None or not bounds:
        return None
    end += 1440 if end < start else 0
    if end - start > 1440:
        return None
    totals = [0.] * 24
    if end == start:
        totals[(start // 60) % 24] = 1.
    else:
        headway = sum(bounds) / len(bounds)
        # Intersect each clock-hour interval, including the following day.
        for hour in range(start // 60, (end + 59) // 60):
            overlap = max(0, min(end, (hour + 1) * 60) - max(start, hour * 60))
            totals[hour % 24] += overlap / headway
    return totals


def recompute_service(models):
    """Apply timetable/frequency precedence inside each operator, then sum.

    A missing calendar/time makes the representative service/day unknown. A
    complete calendar explicitly not running that day produces a known zero.
    """
    answer = {}
    for output_day, weekday in DAYS.items():
        by_operator = defaultdict(lambda: {"trips": [0.] * 24, "frequency": [0.] * 24})
        seen = {"trips": set(), "frequency": set()}
        valid, entries_found = True, False
        for model in models:
            identity = (*service_key(model), model.get("operator_id"))
            for kind, field in (("trips", "timetables"), ("frequency", "frequencys")):
                for entry in model.get(field) or []:
                    entries_found = True
                    calendar = entry.get("service_day")
                    if not calendar_valid(calendar):
                        valid = False
                        continue
                    if not calendar[weekday]:
                        continue
                    if kind == "trips":
                        departure = origin_minute(entry)
                        if departure is None:
                            valid = False
                            continue
                        identifier = (identity, entry.get("trip_id") or json.dumps(entry.get("stop_times"), sort_keys=True))
                        values = [0.] * 24
                        values[(departure // 60) % 24] = 1.
                    else:
                        values = frequency_series(entry)
                        if values is None:
                            valid = False
                            continue
                        identifier = (identity, *(entry.get(k) for k in
                                      ("start_time", "end_time", "min_headway_mins", "max_headway_mins")))
                    if identifier in seen[kind]:
                        continue
                    seen[kind].add(identifier)
                    for hour, count in enumerate(values):
                        by_operator[identity][kind][hour] += count
        answer[output_day] = ([round(sum(b["trips"][h] or b["frequency"][h]
                                         for b in by_operator.values()), 1) for h in range(24)]
                              if valid and entries_found else None)
    return answer


def read_inputs(root):
    stops, schedules = defaultdict(set), defaultdict(list)
    sources, diagnostics = [], []
    for scope in SCOPES:
        for endpoint in ("StopOfRoute", "Schedule"):
            relative = f"data/official_cross_city/service_coverage/{scope}_{endpoint}.json.gz"
            document = load(root / relative)
            require(document.get("schema_version") == 1 and isinstance(document.get("records"), list),
                    relative + ": invalid source envelope")
            meta, records = document["metadata"], document["records"]
            require(isinstance(meta, dict) and all(isinstance(row, dict) for row in records),
                    relative + ": invalid source metadata/model type")
            require({"source_updated_at", "source_record_update_times", "statistical_period"}.issubset(meta),
                    relative + ": required source time/period fields missing")
            require(meta.get("scope_status") == "complete_published_endpoint_by_exhausted_page",
                    relative + ": incomplete source endpoint")
            require(meta.get("scope") == scope and meta.get("service") == endpoint,
                    relative + ": source scope/endpoint drift")
            require(not meta.get("next_pages_or_source_gaps"), relative + ": unresolved source pages")
            updates = sorted({r["update_time"] for r in records if r.get("update_time")})
            require(meta.get("source_record_update_times") == updates and
                    meta.get("source_updated_at") == (updates[0] if len(updates) == 1 else None),
                    relative + ": source update time differs from retained rows")
            require(meta.get("statistical_period") is None, relative + ": snapshot statistical period invented")
            require(meta.get("record_count") == len(records), relative + ": source record count drift")
            receipts = meta.get("sources")
            require(isinstance(receipts, list) and bool(receipts), relative + ": source receipts missing")
            for receipt in receipts:
                url = urlsplit(receipt.get("download_url", ""))
                suffix = "/InterCity" if scope == "InterCity" else "/City/" + scope
                require(url.scheme == "https" and url.netloc == "tdx.transportdata.tw" and
                        url.path == "/api/basic/v2/Bus/" + endpoint + suffix,
                        relative + ": unrecognized official source URL")
                require(bool(receipt.get("source_page")) and receipt.get("scope") == scope and
                        receipt.get("service") == endpoint, relative + ": receipt source scope/fields missing")
            sources.append({"scope": scope, "service": endpoint, "path": relative,
                            "source_updated_at": meta.get("source_updated_at"),
                            "source_record_update_times": meta.get("source_record_update_times"),
                            "source_pages": sorted({r["source_page"] for r in receipts}),
                            "download_urls": [r.get("download_url") for r in receipts],
                            "raw_files": [r.get("raw_file") for r in receipts], "records": len(records)})
            metrics = Counter()
            for row in records:
                key = service_key(row)
                if endpoint == "Schedule":
                    require(row.get("operator_id") is None or isinstance(row["operator_id"], str),
                            relative + ": operator ID must be string or null")
                    schedules[key].append(row)
                    for field in ("timetables", "frequencys"):
                        entries = row.get(field) or []
                        require(isinstance(entries, list), relative + ": invalid schedule rows")
                        metrics[field] += len(entries)
                        for entry in entries:
                            metrics["valid_weekly_calendars" if calendar_valid(entry.get("service_day"))
                                    else "unknown_weekly_calendars"] += 1
                            metrics["special_day_exceptions_not_applied"] += len(entry.get("special_days") or [])
                else:
                    for stop in row.get("stops") or []:
                        metrics["nested_stops"] += 1
                        position = stop.get("stop_position") or {}
                        lon, lat = position.get("position_lon"), position.get("position_lat")
                        if not number(lon) or not number(lat) or not (119 < lon < 123 and 21 < lat < 27):
                            metrics["invalid_coordinates_excluded"] += 1
                            continue
                        require(isinstance(stop.get("stop_uid"), str), relative + ": stop UID must be string")
                        stops[key].add((stop["stop_uid"], float(lon), float(lat)))
            diagnostics.append({"path": relative, "record_count": len(records),
                                "statistical_period": meta.get("statistical_period"),
                                "source_downloaded_at": [r.get("downloaded_at") for r in receipts],
                                "grain": meta.get("grain"), "counts": dict(metrics)})
    services = {}
    for key in stops:
        rows = schedules.get(key, [])
        exact = [r for r in rows if r.get("operator_id") is not None]
        ambiguous = any(r.get("operator_id") is None for r in rows)
        services[key] = (recompute_service(exact or rows), ambiguous)
    return stops, services, sources, diagnostics


def read_geometry(root):
    geometries, references = [], []
    for index, name in enumerate(GEOMETRIES):
        document = load(root / name)
        meta = document["metadata"]
        require(meta.get("crs") == "EPSG:4326" and meta.get("coordinate_order") == ["longitude", "latitude"],
                name + ": CRS/coordinate-order drift")
        features = document["features"]
        if index == 0:
            selected = [f for f in features if f["properties"].get("admin_code") in CITY_CODES[:2]]
            require(Counter(f["properties"]["admin_code"] for f in selected) == Counter(CITY_CODES[:2]),
                    "Taipei/New Taipei boundary is missing or duplicated")
        else:
            require(bool(features) and all(f["properties"].get("county_code") == "10017" for f in features),
                    "Keelung boundary contains missing/foreign official codes")
            selected = features
        polygons = [shape(f["geometry"]) for f in selected]
        require(all(not g.is_empty and g.is_valid for g in polygons), "Invalid retained official boundary")
        geometries.extend(polygons)
        references.append({"path": name, "feature_count": len(selected), "source_period": meta.get("source_period"),
                           "version_evidence": meta.get("version_evidence"), "sources": meta.get("sources"),
                           "crs": meta.get("crs")})
    return unary_union(geometries), references


def grid_geometry(region, stops, size):
    geod = Geod(ellps="GRS80")
    dx = size / geod.inv(121.5, 25.05, 121.51, 25.05)[2] * .01
    dy = size / geod.inv(121.5, 25.05, 121.5, 25.06)[2] * .01
    left, bottom, right, top = region.bounds
    ox, oy = math.floor(left / dx) * dx, math.floor(bottom / dy) * dy
    columns, rows = math.ceil((right - ox) / dx), math.ceil((top - oy) / dy)
    cell_stops, memberships = defaultdict(set), defaultdict(set)
    for key, locations in stops.items():
        for uid, lon, lat in locations:
            ix, iy = int((lon - ox) // dx), int((lat - oy) // dy)
            if 0 <= ix < columns and 0 <= iy < rows:
                cell_stops[ix, iy].add((uid, lon, lat))
                memberships[ix, iy].add(key)
    if size >= 250:
        candidates = ((x, y) for x in range(columns) for y in range(rows))
    else:
        radius = math.ceil(350 / size)
        offsets = [(x, y) for x in range(-radius, radius + 1) for y in range(-radius, radius + 1)
                   if math.hypot(x, y) <= radius]
        candidates = {(x + a, y + b) for x, y in cell_stops for a, b in offsets
                      if 0 <= x + a < columns and 0 <= y + b < rows}
    mask = prep(region)
    coordinates = {(x, y) for x, y in candidates if mask.intersects(
        box(ox + x * dx, oy + y * dy, ox + x * dx + dx, oy + y * dy + dy))}
    return [round(ox, 7), round(oy, 7)], [dx, dy], coordinates, cell_stops, memberships


def cell_expected(location, cell_stops, memberships, services):
    keys = memberships.get(location, set())
    unknown = {k for k in keys if services[k][1] or any(v is None for v in services[k][0].values())}
    values = []
    for day in DAYS:
        known = [services[k][0][day] for k in keys if services[k][0][day] is not None]
        values.append([round(sum(series[h] for series in known), 1) for h in range(24)] if known else None)
    status = ("no_stop" if not keys else "no_schedule" if all(v is None for v in values)
              else "partial" if unknown else "ok")
    return [*location, len(cell_stops.get(location, ())), len(keys), len(unknown), *values], status


def review_grid(root, size, region, stops, services, sources):
    relative = f"web/public/data/regional_bus_grid_{size}.json"
    doc = load(root / relative)
    require(isinstance(doc, dict) and set(doc) == {"schema_version", "dataset_id", "metadata", "records"} and
            type(doc.get("schema_version")) is int and doc["schema_version"] == 1 and
            doc.get("dataset_id") == f"regional_bus_grid_{size}" and isinstance(doc.get("metadata"), dict) and
            isinstance(doc.get("records"), list), relative + ": required grid envelope missing or invalid")
    meta = doc["metadata"]
    definition = meta.get("grid_definition")
    require(isinstance(definition, dict) and set(definition) == {"origin", "step", "cell_m", "fields"} and
            type(definition.get("cell_m")) is int and definition["cell_m"] == size and
            definition.get("fields") == FIELDS, relative + ": grid definition/schema drift")
    require(timestamp(meta.get("built_at")), relative + ": built_at must be an ISO datetime with timezone")
    origin, step, coordinates, cell_stops, memberships = grid_geometry(region, stops, size)
    require(definition.get("origin") == origin and isinstance(definition.get("step"), list) and len(definition["step"]) == 2 and
            all(number(v) and math.isclose(v, expected, rel_tol=0, abs_tol=1e-12) for v, expected in zip(definition["step"], step)),
            relative + ": source-derived grid origin/step mismatch")
    require({"source_data_time", "statistical_period", "units"}.issubset(meta),
            relative + ": required source period/units fields missing")
    require(meta.get("scope_city_codes") == CITY_CODES and meta.get("geometry_sources") == GEOMETRIES,
            relative + ": geographic scope/source drift")
    require(meta.get("source_data_time") is None and meta.get("statistical_period") is None and meta.get("units") == UNITS,
            relative + ": source period/units drift")
    require(meta.get("sources") == sources, relative + ": retained source metadata drift")
    require(meta.get("sequence_subroutes") == len(services) and
            meta.get("scheduled_subroutes") == sum(any(v is not None for v in s[0].values()) for s in services.values()) and
            meta.get("operator_ambiguous_service_models") == sum(s[1] for s in services.values()),
            relative + ": service population metadata drift")
    require(isinstance(meta.get("limitations"), list) and meta["limitations"], relative + ": limitations missing")
    if size < 250:
        require("350" in meta.get("cell", ""), relative + ": fine-grid corridor caveat missing")
    observed, statuses, known_cells = set(), Counter(), Counter()
    totals = {day: [0.] * 24 for day in DAYS}
    stop_total = service_total = uncertain_total = 0
    for row in doc["records"]:
        require(isinstance(row, list) and len(row) == len(FIELDS), relative + ": cell column contract drift")
        require(all(type(n) is int and n >= 0 for n in row[:5]), relative + ": invalid cell index/count type")
        location = tuple(row[:2])
        require(location not in observed, relative + f": duplicate cell {location}")
        observed.add(location)
        require(location in coordinates, relative + f": cell outside independent geographic mask {location}")
        expected, status = cell_expected(location, cell_stops, memberships, services)
        require(row[:5] == expected[:5], relative + f": source-derived stop/service/unknown count mismatch {location}")
        for day, values, want in zip(DAYS, row[5:], expected[5:]):
            require((values is None) == (want is None), relative + f": unknown/null changed to numeric or missing known {day} {location}")
            if values is not None:
                require(isinstance(values, list) and len(values) == 24 and
                        all(number(value) and value >= 0 for value in values), relative + f": invalid {day} hourly series {location}")
                require(all(math.isclose(value, ref, rel_tol=0, abs_tol=1e-7) for value, ref in zip(values, want)),
                        relative + f": source-derived hourly departures mismatch {day} {location}")
                known_cells[day] += 1
                for hour, value in enumerate(values):
                    totals[day][hour] += value
        stop_total += row[2]
        service_total += row[3]
        uncertain_total += row[4]
        statuses[status] += 1
    require(observed == coordinates, relative + f": geographic coverage incomplete ({len(observed)}/{len(coordinates)} cells)")
    require(bool(observed) and any(known_cells.values()), relative + ": no published service available")
    require(meta.get("status_counts") == {k: statuses[k] for k in ("ok", "partial", "no_stop", "no_schedule")},
            relative + ": source-derived status counts mismatch")
    return {"path": relative, "cell_m": size, "status": "passed", "record_count": len(observed),
            "checks": {"required_envelope_valid": True, "built_at_timestamp_valid": True,
                       "all_cells_recomputed": True, "all_24_hour_values_reconciled": True,
                       "source_metadata_reconciled": True, "full_expected_geographic_mask_reconciled": True},
            "counts": {"status_counts": dict(statuses), "cells_with_published_hours": dict(known_cells),
                       "stop_cell_memberships": stop_total, "service_cell_memberships": service_total,
                       "uncertain_service_cell_memberships": uncertain_total},
            "aggregates": {day: {"hourly_planned_departures_summed_over_cells": [round(x, 1) for x in totals[day]],
                                  "daily_planned_departures_summed_over_cells": round(sum(totals[day]), 1)} for day in DAYS}}


def audit(root=ROOT):
    result = {"schema_version": 1, "dataset_id": "independent_regional_bus_grid_review", "metadata": {
        "review_implementation_author_agent": "/root/independent_audit", "independent_implementation": True,
        "reviewed_at": datetime.now(ZoneInfo("Asia/Taipei")).isoformat(timespec="seconds"),
        "status": "failed", "record_count": 0, "check_script": "scripts/review_regional_bus_grid.py",
        "method": "Independent retained-source calendar, operator-specific timetable/frequency precedence, duplicate identity, hourly origin-departure, stop/service cell mapping, full geometry mask, metadata and aggregate reconciliation; no generator imports.",
        "representative_days": DAYS, "source_data_time": None, "statistical_period": None,
        "checked_document_fields": ["schema_version", "dataset_id", "metadata.grid_definition", "metadata.built_at", "records"],
        "checked_grid_fields": FIELDS,
        "comparison_tolerances": {"hourly_departures_absolute": 1e-7, "grid_step_degrees_absolute": 1e-12,
                                  "origin_decimal_places": 7, "cell_locations_and_counts": "exact"},
        "units": UNITS, "grain": "one review record per delivered resolution; all cells and hours checked",
        "limitations": [
            "依保留的 TDX StopOfRoute／Schedule 快照重算；不重新下載來源，也不以下載時間代替來源更新時間。",
            "本巡檢驗證網格推導；原始 HTTP 分頁、欄位逐值轉換及端點完整性另見 data/quality/service_coverage_review.json。",
            "代表性週三／六／日只用完整七日日曆，不套用特殊服務日，也不是任一實際日期的班次或載客需求。",
            "先在每個業者識別的小時內選定時班表，該業者該小時無定時班表才用班距估計，再跨業者加總。",
            "匿名班表只計一次且標示不確定性；同附線已有具名業者時優先具名模型。缺日曆、班表或有效時間保留 null。",
            "每個附線起站供給呈現在沿途所有有站格；格間加總是展示值稽核，不能解讀為全區不同車次總數。",
            "100／150 m 僅檢查站位周邊約 350 m 候選格；250／500 m 檢查完整北北基官方行政遮罩。",
            "行政遮罩按網格與界線交集保留；邊界格可包含行政界外站位。起站時刻未換算沿途站牌到達時間。",
            "只依相同 TDX UID 連接站序與班表，不跨接地方 feed；約略公尺網格採固定參考緯度，不是等面積投影。",
        ]}, "records": []}
    try:
        stops, services, sources, diagnostics = read_inputs(root)
        region, geometry_sources = read_geometry(root)
        result["metadata"].update({"source_inputs": sources, "source_diagnostics": diagnostics,
                                   "geometry_inputs": geometry_sources, "retained_service_population": len(services)})
        result["metadata"]["retained_service_aggregates"] = {
            "scope": "All retained StopOfRoute services before the regional geographic mask; InterCity includes national services.",
            "days": {day: {"known_services": sum(s[0][day] is not None for s in services.values()),
                            "unknown_services": sum(s[0][day] is None for s in services.values()),
                            "planned_origin_departures_summed_once_per_service": round(sum(
                                sum(s[0][day]) for s in services.values() if s[0][day] is not None), 1)} for day in DAYS}}
    except Exception as error:
        # A malformed nested value must still replace a prior successful report.
        result["metadata"]["failures"] = [str(error)]
        return result
    for size in EXPECTED:
        try:
            row = review_grid(root, size, region, stops, services, sources)
        except Exception as error:
            row = {"path": f"web/public/data/regional_bus_grid_{size}.json", "cell_m": size,
                   "status": "failed", "failures": [str(error)]}
        result["records"].append(row)
    result["metadata"]["record_count"] = len(result["records"])
    result["metadata"]["status"] = "passed" if all(r["status"] == "passed" for r in result["records"]) else "failed"
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--output", type=Path, help="Default: ROOT/data/quality/regional_bus_grid_review.json")
    args = parser.parse_args(argv)
    report = audit(args.root)
    output = args.output or args.root / REPORT
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, allow_nan=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": report["metadata"]["status"], "report": str(output),
                      "grids": [{k: row[k] for k in ("cell_m", "status", "failures") if k in row}
                                for row in report["records"]], "failures": report["metadata"].get("failures", [])},
                     ensure_ascii=False, indent=2))
    return 0 if report["metadata"]["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
