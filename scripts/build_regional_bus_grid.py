#!/usr/bin/env python3
"""Build north-Taipei/New-Taipei/Keelung planned bus-frequency grids from retained TDX evidence.

This is a service-supply estimate, not passenger demand. It does not attach the
municipal TDX IDs to the independent Taipei public-transport-office feed.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone
import gzip
import json
import math
from pathlib import Path

from pyproj import Geod
from shapely.geometry import box, shape
from shapely.ops import unary_union
from shapely.prepared import prep

ROOT = Path(__file__).resolve().parents[1]
INPUT = ROOT / "data/official_cross_city/service_coverage"
OUTPUT = ROOT / "web/public/data"
SCOPES = ("Taipei", "NewTaipei", "Keelung", "InterCity")
CITY_CODES = ("63000", "65000", "10017")
SIZES = (100, 150, 250, 500)
DAY_NAMES = {"weekday": "wednesday", "saturday": "saturday", "sunday": "sunday"}
WEEK = ("sunday", "monday", "tuesday", "wednesday", "thursday", "friday", "saturday")
CORRIDOR_M = 350  # finer grids omit remote unserved areas to keep the web layer usable


def read(path: Path):
    with (gzip.open(path, "rt", encoding="utf-8") if path.suffix == ".gz"
          else path.open("r", encoding="utf-8")) as handle:
        return json.load(handle)


def region_geometry(root: Path):
    """Use retained official city and Keelung-village boundaries, not a viewport bbox."""
    city_doc = read(root / "map-data/admin/cities.geojson")
    cities = {f["properties"]["admin_code"]: shape(f["geometry"])
              for f in city_doc["features"] if f["properties"].get("admin_code") in CITY_CODES[:2]}
    if set(cities) != set(CITY_CODES[:2]):
        raise ValueError("Taipei / New Taipei city boundary is missing")
    keelung = read(root / "map-data/admin/keelung_villages.geojson")
    if not keelung["features"]:
        raise ValueError("Keelung village boundary is missing")
    geometries = [*cities.values(), *(shape(f["geometry"]) for f in keelung["features"])]
    if any(g.is_empty or not g.is_valid for g in geometries):
        raise ValueError("Invalid official geometry: review instead of silently repairing")
    return unary_union(geometries)


def coordinate(stop):
    pos = stop.get("stop_position") or {}
    lon, lat = pos.get("position_lon"), pos.get("position_lat")
    if (type(lon) not in (int, float) or type(lat) not in (int, float)
            or not math.isfinite(lon) or not math.isfinite(lat)
            or not (119 < lon < 123 and 21 < lat < 27)):
        return None
    return float(lon), float(lat)


def load_services(root: Path):
    """Identity is TDX RouteUID, SubRouteUID, direction, not a route name.

    Schedule operator IDs are not always published. Anonymous models are treated
    once per route/direction and marked partial, never replicated per operator.
    """
    route_stops = defaultdict(set)
    schedules = defaultdict(list)
    source_meta = []
    for scope in SCOPES:
        for kind in ("StopOfRoute", "Schedule"):
            path = root / "data/official_cross_city/service_coverage" / f"{scope}_{kind}.json.gz"
            doc = read(path)
            meta = doc["metadata"]
            if meta.get("scope_status") != "complete_published_endpoint_by_exhausted_page":
                raise ValueError(f"Cannot build a complete regional layer from partial {scope} {kind}")
            source_meta.append({
                "scope": scope, "service": kind, "path": path.relative_to(root).as_posix(),
                "source_updated_at": meta.get("source_updated_at"),
                "source_record_update_times": meta.get("source_record_update_times"),
                "source_pages": sorted({x.get("source_page") for x in meta.get("sources", []) if x.get("source_page")}),
                "download_urls": [x.get("download_url") for x in meta.get("sources", [])],
                "raw_files": [x.get("raw_file") for x in meta.get("sources", [])],
                "records": len(doc["records"]),
            })
            for model in doc["records"]:
                key = (model["route_uid"], model["sub_route_uid"], str(model.get("direction")))
                if kind == "StopOfRoute":
                    for stop in model.get("stops") or []:
                        point = coordinate(stop)
                        if point is not None:
                            # Different coordinates of one UID are intentionally distinct.
                            route_stops[key].add((stop["stop_uid"], point[0], point[1]))
                else:
                    schedules[key].append(model)
    services = {}
    uncertain_operator = 0
    for key in route_stops:
        rows = schedules.get(key, [])
        exact = [r for r in rows if r.get("operator_id") is not None]
        anonymous = [r for r in rows if r.get("operator_id") is None]
        # Do not double count route-level schedules alongside operator-specific rows.
        chosen = exact if exact else anonymous
        uncertain = bool(anonymous)
        if uncertain:
            uncertain_operator += 1
        values = service_departures(chosen)
        services[key] = (values, uncertain)
    return route_stops, services, source_meta, uncertain_operator


def clock_minutes(value):
    if not isinstance(value, str):
        return None
    pieces = value.split(":")
    if len(pieces) < 2 or len(pieces) > 3:
        return None
    if not all(x.isdigit() for x in pieces):
        return None
    h, m = int(pieces[0]), int(pieces[1])
    if h > 47 or m > 59 or (len(pieces) == 3 and int(pieces[2]) > 59):
        return None
    return h * 60 + m


def weekly_active(calendar, day):
    if not isinstance(calendar, dict) or any(type(calendar.get(d)) is not int or calendar[d] not in (0, 1) for d in WEEK):
        return None
    return bool(calendar[DAY_NAMES[day]])


def timetable_minute(item):
    times = item.get("stop_times")
    if isinstance(times, list) and times:
        # The first stop is the route's departure reference; later stops are
        # not counted as separate scheduled vehicles.
        first = times[0]
        return clock_minutes(first.get("departure_time") or first.get("arrival_time"))
    return clock_minutes(item.get("departure_time"))


def frequency_hours(item):
    start = clock_minutes(item.get("start_time"))
    end = clock_minutes(item.get("end_time"))
    bounds = [x for x in (item.get("min_headway_mins"), item.get("max_headway_mins"))
              if type(x) in (int, float) and math.isfinite(x) and x > 0]
    if start is None or end is None or not bounds:
        return None
    if end < start:
        end += 24 * 60
    if end - start > 24 * 60:
        return None
    counts = [0.] * 24
    if start == end:
        counts[start // 60 % 24] = 1.
    else:
        interval = sum(bounds) / len(bounds)
        for hour in range(start // 60, (end + 59) // 60):
            minutes = min(end, (hour + 1) * 60) - max(start, hour * 60)
            if minutes > 0:
                counts[hour % 24] += minutes / interval
    return counts


def service_departures(models):
    """Each TDX timetable trip counts once; frequency fills its identity's empty hours.

    Missing calendars, missing clock values and absent models become null; an
    explicit complete calendar with no operation on a day yields zero.
    """
    if not models:
        return {d: None for d in DAY_NAMES}
    result = {}
    for day in DAY_NAMES:
        fixed = defaultdict(lambda: [0.] * 24)
        frequencies = defaultdict(lambda: [0.] * 24)
        any_record = False
        uncertain = False
        trips_seen = set()
        freq_seen = set()
        for model in models:
            identity = (model["route_uid"], model["sub_route_uid"], str(model.get("direction")),
                        model.get("operator_id"))
            rows = ((model.get("timetables") or [], "trip"), (model.get("frequencys") or [], "freq"))
            for entries, kind in rows:
                for entry in entries:
                    any_record = True
                    on = weekly_active(entry.get("service_day"), day)
                    if on is None:
                        uncertain = True
                        continue
                    if not on:
                        continue
                    if kind == "trip":
                        minute = timetable_minute(entry)
                        if minute is None:
                            uncertain = True
                            continue
                        # Stable source identifiers avoid duplication across publishing scopes.
                        ident = (identity, entry.get("trip_id") or json.dumps(entry.get("stop_times"), sort_keys=True))
                        if ident in trips_seen:
                            continue
                        trips_seen.add(ident)
                        fixed[identity][minute // 60 % 24] += 1.
                    else:
                        hours = frequency_hours(entry)
                        if hours is None:
                            uncertain = True
                            continue
                        ident = (identity, entry.get("start_time"), entry.get("end_time"),
                                 entry.get("min_headway_mins"), entry.get("max_headway_mins"))
                        if ident in freq_seen:
                            continue
                        freq_seen.add(ident)
                        for hour, n in enumerate(hours):
                            frequencies[identity][hour] += n
        if not any_record or uncertain:
            result[day] = None
        else:
            identities = fixed.keys() | frequencies.keys()
            # One operator's timetable must not suppress another's frequency.
            result[day] = [round(sum(fixed[identity][h] if fixed[identity][h]
                                     else frequencies[identity][h]
                                     for identity in identities), 1) for h in range(24)]
    return result


def make_grid(region, route_stops, services, cell_m):
    geod = Geod(ellps="GRS80")
    step_x = cell_m / geod.inv(121.5, 25.05, 121.51, 25.05)[2] * .01
    step_y = cell_m / geod.inv(121.5, 25.05, 121.5, 25.06)[2] * .01
    minx, miny, maxx, maxy = region.bounds
    ox, oy = math.floor(minx / step_x) * step_x, math.floor(miny / step_y) * step_y
    cols, rows = math.ceil((maxx - ox) / step_x), math.ceil((maxy - oy) / step_y)
    cell_stops = defaultdict(set)
    cell_services = defaultdict(set)
    for key, stops in route_stops.items():
        for uid, lon, lat in stops:
            ix, iy = int((lon - ox) // step_x), int((lat - oy) // step_y)
            if 0 <= ix < cols and 0 <= iy < rows:
                cell_stops[ix, iy].add((uid, lon, lat))
                cell_services[ix, iy].add(key)

    if cell_m >= 250:
        candidates = ((ix, iy) for ix in range(cols) for iy in range(rows))
        scope_note = "各市完整行政遮罩"
    else:
        # Urban/transit corridor zoom: remote unserved land is deliberately
        # omitted, NOT falsely marked as service available or as a zero score.
        reach = math.ceil(CORRIDOR_M / cell_m)
        occupied = set(cell_stops)
        nearby = {(x + dx, y + dy)
                  for x, y in occupied for dx in range(-reach, reach + 1)
                  for dy in range(-reach, reach + 1)
                  if 0 <= x + dx < cols and 0 <= y + dy < rows
                  and dx * dx + dy * dy <= reach * reach}
        candidates = iter(sorted(nearby))
        scope_note = f"僅公車站位周圍約 {CORRIDOR_M} m；其餘格不在分析範圍"
    prepared = prep(region)
    cells = []
    statuses = Counter()
    for ix, iy in candidates:
        x, y = ox + ix * step_x, oy + iy * step_y
        if not prepared.intersects(box(x, y, x + step_x, y + step_y)):
            continue
        keys = cell_services.get((ix, iy), set())
        known = {d: [0.] * 24 for d in DAY_NAMES}
        available = {d: 0 for d in DAY_NAMES}
        unknown_keys = set()
        for key in keys:
            times, uncertain = services[key]
            if uncertain:
                unknown_keys.add(key)
            for day in DAY_NAMES:
                series = times[day]
                if series is None:
                    unknown_keys.add(key)
                    continue
                available[day] += 1
                for hour, count in enumerate(series):
                    known[day][hour] += count
        values = []
        for day in DAY_NAMES:
            values.append([round(x, 1) for x in known[day]] if available[day] else None)
        state = "no_stop" if not keys else ("no_schedule" if not any(available.values())
                else "partial" if unknown_keys else "ok")
        statuses[state] += 1
        cells.append([ix, iy, len(cell_stops.get((ix, iy), ())), len(keys), len(unknown_keys), *values])

    return {
        "origin": [round(ox, 7), round(oy, 7)], "step": [step_x, step_y],
        "cell_m": cell_m,
        "fields": ["ix", "iy", "stop_count", "subroute_count", "unscheduled_subroute_count", "weekday", "saturday", "sunday"],
        "cells": cells,
    }, dict(statuses), scope_note


def build_all(root=ROOT, output=OUTPUT, built_at=None):
    if built_at is None:
        built_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    region = region_geometry(root)
    route_stops, services, sources, uncertain = load_services(root)
    output.mkdir(parents=True, exist_ok=True)
    for cell_m in SIZES:
        grid, status, scope = make_grid(region, route_stops, services, cell_m)
        metadata = {
            "source": "交通部 TDX 官方 Bus StopOfRoute / Schedule (retained snapshots)",
            "source_data_time": None,
            "statistical_period": None,
            "units": {"frequency": "estimated planned departure trips per hour", "position": "WGS84 longitude/latitude degrees"},
            "built_at": built_at,
            "grid_definition": {name: grid[name] for name in ("origin", "step", "cell_m", "fields")},
            "cell": f"約 {cell_m} m 統一經緯度網格，{scope}；北北基行政遮罩",
            "value": "格內同一 RouteUID／SubRouteUID／方向計畫起站發車數，按小時計；跨市重複站序去重",
            "scope_city_codes": list(CITY_CODES),
            "geometry_sources": ["map-data/admin/cities.geojson", "map-data/admin/keelung_villages.geojson"],
            "sources": sources,
            "operator_ambiguous_service_models": uncertain,
            "status_counts": {k: status.get(k, 0) for k in ("ok", "partial", "no_stop", "no_schedule")},
            "scheduled_subroutes": sum(any(s is not None for s in values[0].values()) for values in services.values()),
            "sequence_subroutes": len(services),
            "limitations": [
                "本圖是計畫供給，不是即時班車、車上人數或實際搭乘需求。",
                "TDX 班表以起站時刻呈現，未校正沿途站牌到達時間；特殊服務日不套入代表性週三／六／日。",
                "部分 TDX Schedule 沒有業者識別碼，匿名日曆只計一次且保留資料不確定性，不複製給不同業者。",
                "無法驗證日曆、班表或時間的附線保留缺值 null，不視為 0；部分數值是供給下限。",
                f"{cell_m} m 的空間顯示範圍：{scope}。不同尺寸與來源的色階尚未重新校準。",
                "與臺北市公共運輸處本地 Feed 屬不同識別碼空間；既有路線模擬與人口需求模型仍使用原臺北市資料。",
            ],
        }
        document = {
            "schema_version": 1,
            "dataset_id": f"regional_bus_grid_{cell_m}",
            "metadata": metadata,
            "records": grid["cells"],
        }
        (output / f"regional_bus_grid_{cell_m}.json").write_text(
            json.dumps(document, ensure_ascii=False, allow_nan=False, separators=(",", ":")) + "\n",
            encoding="utf-8")
        print(f"regional bus grid {cell_m}m: {len(grid['cells'])} cells; {status}")


if __name__ == "__main__":
    build_all()
