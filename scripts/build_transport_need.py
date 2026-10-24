#!/usr/bin/env python3
"""Build the first-version transport-need (needs-gap) grid described in docs/12.

For every 250 m grid cell whose centre lies in Taipei City and each time band, computes
demand sub-scores (resident population, weighted places, metro activity), a bus + metro
supply score, their percentile ranks, the needs gap and a weight-robustness share.
Output: data/analysis/transport_need_250.json (+ raw_data/analysis/delivery.json).
"""

import argparse
import json
import math
import re
from collections import Counter, defaultdict
from datetime import date, datetime
from pathlib import Path
from statistics import median
from zoneinfo import ZoneInfo

import numpy as np
import shapely
from pyproj import Geod, Transformer
from shapely import STRtree, make_valid
from shapely.geometry import box, shape
from shapely.ops import unary_union

from build_web_data import GRID_LAT0, MAP, METRO_CLASSES, METRO_EXCLUDE, METRO_NAME, ROOT, osm_layer, read, subroute_departures

DATA = ROOT / "data"
OUT = DATA / "analysis"
RAW = ROOT / "raw_data/analysis"
TZ = ZoneInfo("Asia/Taipei")
CELL_M = 250
SEED = 20261024
SAMPLES = 1000
TOP_SHARE = 0.05

# Time bands: (label, bus day type, hours [start, end), metro day set).
BANDS = {
    "weekday_am": ("平日早峰 07–09", "weekday", (7, 9), "workday"),
    "weekday_midday": ("平日離峰 10–16", "weekday", (10, 16), "workday"),
    "weekday_pm": ("平日晚峰 17–19", "weekday", (17, 19), "workday"),
    "weekday_evening": ("平日晚間 20–23", "weekday", (20, 23), "workday"),
    "saturday": ("週六 10–16", "saturday", (10, 16), "saturday"),
    "sunday": ("週日 10–16", "sunday", (10, 16), "sunday"),
}
# Place time factors τ_k(t) from docs/12 §3.2 (expert assumptions); the holiday column is used
# for both Saturday and Sunday.
TAU = {
    "school": (1.0, 0.3, 0.6, 0.0, 0.1, 0.1),
    "government": (0.6, 1.0, 0.4, 0.0, 0.0, 0.0),
    "market": (1.0, 0.5, 0.2, 0.0, 1.0, 1.0),
    "hospital": (0.8, 1.0, 0.4, 0.2, 0.3, 0.3),
    "mall": (0.1, 0.6, 0.8, 1.0, 1.0, 1.0),
    "business_park": (1.0, 0.3, 1.0, 0.2, 0.1, 0.1),
    "social_housing": (1.0, 0.4, 0.8, 0.5, 0.6, 0.6),
}
PLACE_LABELS = {"school": "學校", "government": "市政機關", "market": "菜市場", "hospital": "醫院", "mall": "商場百貨", "business_park": "科技產業園區", "social_housing": "社會住宅（已完工）"}
BUSINESS_PARK = re.compile(r"(科技|軟體|科學|生技研究|企業總部)園區")
# Gaussian distance decay w(d) = exp(-d² / 2σ²), cut at `cutoff` metres.
DECAY = {"bus": (250, 600), "place": (250, 600), "metro": (500, 1000)}
F0 = 12  # departures per hour at which bus service is ~63 % saturated
WEIGHTS = {"population": 0.5, "places": 0.3, "metro": 0.2}
# A weekday whose metro total is below this share of the median for its day set is treated as
# an unannounced closure (typhoon day) and left out of the averages.
ANOMALY_SHARE = 0.6
# Analysis scope: cells with at least this many residents, or a place score of at least
# MIN_PLACE in some band (about one full-weight place within 300 m). Other cells are mostly
# mountain, river or park; they keep raw values but are not ranked.
MIN_POPULATION = 100
MIN_PLACE = 0.5
# Order of the per-band value arrays in each record's `bands`.
BAND_FIELDS = [
    "place_score", "metro_activity", "bus_weighted_departures_per_hour", "bus_supply_score", "supply_score",
    "r_places", "r_metro", "r_supply", "demand_score", "r_demand", "gap", "quadrant", "robust_top5_share",
]


def now():
    return datetime.now(TZ).isoformat(timespec="seconds")


def decay(distance, kind):
    sigma, cutoff = DECAY[kind]
    return np.where(distance <= cutoff, np.exp(-(distance ** 2) / (2 * sigma ** 2)), 0.0)


def rank01(values):
    """Percentile rank in [0, 1] with ties sharing the mean rank; exact zeros stay 0."""
    values = np.asarray(values, dtype=float)
    order = np.argsort(values, kind="mergesort")
    ranks = np.empty(len(values))
    ranks[order] = np.arange(len(values))
    _, inverse, counts = np.unique(values, return_inverse=True, return_counts=True)
    sums = np.bincount(inverse, weights=ranks)
    ranks = (sums / counts)[inverse] / max(len(values) - 1, 1)
    ranks[values == 0] = 0.0
    return ranks


def norm_station(name):
    name = re.sub(r"^(BL|BR|G|O|R|Y|LG|V|K|A)(?=[一-鿿])", "", name or "")
    return name.replace("臺", "台").removesuffix("站")


def grid_cells(city):
    geod = Geod(ellps="GRS80")
    lon_step = CELL_M / geod.inv(121.5, GRID_LAT0, 121.51, GRID_LAT0)[2] * 0.01
    lat_step = CELL_M / geod.inv(121.5, GRID_LAT0, 121.5, GRID_LAT0 + 0.01)[2] * 0.01
    minx, miny, maxx, maxy = city.bounds
    origin = (math.floor(minx / lon_step) * lon_step, math.floor(miny / lat_step) * lat_step)
    cols, rows = math.ceil((maxx - origin[0]) / lon_step), math.ceil((maxy - origin[1]) / lat_step)
    ix, iy = np.meshgrid(np.arange(cols), np.arange(rows), indexing="ij")
    ix, iy = ix.ravel(), iy.ravel()
    lon = origin[0] + (ix + 0.5) * lon_step
    lat = origin[1] + (iy + 0.5) * lat_step
    inside = shapely.contains_xy(city, lon, lat)
    return {"origin": origin, "step": (lon_step, lat_step), "ix": ix[inside], "iy": iy[inside], "lon": lon[inside], "lat": lat[inside]}


def cell_population(grid, to_tm):
    boundaries = read(DATA / "population/taipei_statistical_area_boundaries.geojson")
    population = read(DATA / "population/taipei_statistical_population_base.json")
    counts = {r["base_area_id"]: r["population"] for r in population["records"]}
    lon_step, lat_step = grid["step"]
    x0 = grid["origin"][0] + grid["ix"] * lon_step
    y0 = grid["origin"][1] + grid["iy"] * lat_step
    cells = shapely.transform(shapely.box(x0, y0, x0 + lon_step, y0 + lat_step), lambda xy: np.column_stack(to_tm.transform(xy[:, 0], xy[:, 1])))
    tree = STRtree(cells)
    result = np.zeros(len(cells))
    total = 0
    for feature in boundaries["features"]:
        value = counts.get(feature["properties"]["base_area_id"])
        if not value:
            continue
        total += value
        area = shapely.transform(make_valid(shape(feature["geometry"])), lambda xy: np.column_stack(to_tm.transform(xy[:, 0], xy[:, 1])))
        hits = tree.query(area, predicate="intersects")
        if len(hits):
            result[hits] += value * shapely.area(shapely.intersection(cells[hits], area)) / area.area
    meta = {
        "population_source": "data/population/taipei_statistical_population_base.json",
        "population_period": population["metadata"]["source_period"],
        "boundary_source": "data/population/taipei_statistical_area_boundaries.geojson",
        "boundary_vintage_year": boundaries["metadata"]["boundary_vintage_year_in_filename"],
        "source_population_total": total,
        "allocated_population_total": round(float(result.sum())),
    }
    return result, meta


def metro_stations():
    """OSM metro / light-rail stations near a metro line, same selection as the web map."""
    geoms = []
    for feature in osm_layer("railways"):
        props = feature["properties"]
        name = props.get("name") or ""
        if props["feature_class"] in METRO_CLASSES and METRO_NAME.search(name) and not METRO_EXCLUDE.search(name):
            geoms.append(shape(feature["geometry"]))
    lines = unary_union(geoms).buffer(0.0008)
    stations = defaultdict(list)
    for feature in osm_layer("transport"):
        props = feature["properties"]
        if props["feature_class"] == "railway_station" and props.get("name") and lines.contains(shape(feature["geometry"])):
            stations[norm_station(props["name"])].append(feature["geometry"]["coordinates"])
    return stations


def metro_activity(stations, calendar, to_tm):
    """Mean hourly entries + exits per station for each band, on the band's day set."""
    flows = read(DATA / "metro/station_hourly_flows.json")
    meta = flows["metadata"]
    entry_names = [norm_station(n) for n in meta["entry_station_names"]]
    exit_names = [norm_station(n) for n in meta["exit_station_names"]]
    holiday = {r["date"]: r["is_holiday"] for r in calendar["records"]}

    def day_set(day):
        d = date.fromisoformat(day)
        if day not in holiday:
            return None
        if not holiday[day]:
            return "workday" if d.isoweekday() <= 5 else None  # make-up Saturdays are left out
        return {6: "saturday", 7: "sunday"}.get(d.isoweekday())  # weekday holidays are left out

    daily = defaultdict(float)
    for service_date, _, entries, _ in flows["records"]:
        daily[service_date] += sum(v for v in entries if v is not None)
    by_set = defaultdict(list)
    for day, total in daily.items():
        if day_set(day):
            by_set[day_set(day)].append(total)
    medians = {k: median(v) for k, v in by_set.items()}
    excluded = sorted(day for day, total in daily.items() if day_set(day) and total < ANOMALY_SHARE * medians[day_set(day)])
    days = defaultdict(set)
    # Per band and station: entry sum, exit sum, and the station-hours each was observed (non-null),
    # so stations that opened mid-period are averaged over their own service hours.
    sums = defaultdict(lambda: defaultdict(lambda: np.zeros(4)))
    for service_date, hour, entries, exits in flows["records"]:
        group = day_set(service_date)
        if not group or service_date in excluded:
            continue
        days[group].add(service_date)
        for band, (_, _, (start, end), band_set) in BANDS.items():
            if band_set != group or not start <= hour < end:
                continue
            for name, value in zip(entry_names, entries):
                if value is not None:
                    sums[band][name][[0, 2]] += (value, 1)
            for name, value in zip(exit_names, exits):
                if value is not None:
                    sums[band][name][[1, 3]] += (value, 1)

    exit_set = set(exit_names)
    matches, coords, activity = [], [], {band: [] for band in BANDS}
    for name in sorted(set(entry_names) | exit_set):
        points = stations.get(name, [])
        if not points:
            matches.append({"station_name": name, "status": "unmatched", "osm_points": 0})
            continue
        xy = np.array([to_tm.transform(*p) for p in points])
        spread = float(np.max(np.linalg.norm(xy - xy.mean(axis=0), axis=1)))
        if spread > 300:
            matches.append({"station_name": name, "status": "ambiguous", "osm_points": len(points), "spread_m": round(spread)})
            continue
        lon, lat = np.mean(points, axis=0)
        entry_only = name not in exit_set
        matches.append({
            "station_name": name,
            "status": "matched" if len(points) == 1 else "matched_merged",
            "osm_points": len(points),
            "spread_m": round(spread),
            "longitude": round(float(lon), 6),
            "latitude": round(float(lat), 6),
            "exits_imputed": entry_only,
        })
        coords.append(xy.mean(axis=0))
        for band in BANDS:
            entries, exits, entry_hours, exit_hours = sums[band][name]
            entry_rate = entries / entry_hours if entry_hours else 0.0
            # Stations missing from the exit dictionary use entries twice (daily entries ≈ exits).
            exit_rate = entry_rate if entry_only else (exits / exit_hours if exit_hours else 0.0)
            activity[band].append(entry_rate + exit_rate)
        matches[-1]["observed_entry_hours_weekday_am"] = int(sums["weekday_am"][name][2])
    meta = {
        "source": "data/metro/station_hourly_flows.json",
        "source_period": meta["period"],
        "day_counts": {k: len(v) for k, v in days.items()},
        "excluded_anomaly_dates": excluded,
        "anomaly_rule": f"平日、週六、週日各自計算日總進站量中位數，低於 {ANOMALY_SHARE:.0%} 的日子視為臨時停班停課（如颱風），不列入平均；介於 60%–100% 的偏低日（如連假週末）保留",
        "averaging": "每站每時段的平均以該站有觀測值（非 null）的日×時數為分母，期間中途啟用的車站不會被全年日數稀釋",
        "exit_imputation": "出站字典沒有的車站（環狀線 10 站），出站量以進站量代替（metro_activity = 進站 × 2），station_matches 的 exits_imputed 為 true",
        "station_matches": matches,
    }
    return np.array(coords), {b: np.array(v) for b, v in activity.items()}, meta


def places(to_tm):
    """Demand places with their category key and size m_i; same-name OSM features are merged."""
    groups = defaultdict(list)
    for feature in read(MAP / "traffic-focus/osm/candidates.geojson")["features"]:
        p = feature["properties"]
        cls = p["feature_class"]
        if p["category"] == "education":
            key = "school"
        elif p["category"] == "government":
            key = "government"
        elif p["category"] == "hospital":
            key = "hospital"
        elif p["category"] == "shopping":
            key = "market" if cls == "marketplace" else "mall"
        elif p["category"] == "employment" and p["name"] and BUSINESS_PARK.search(p["name"]):
            key = "business_park"
        else:
            continue
        group = p["same_name_group_id"] or p["candidate_id"]
        groups[(key, group)].append(feature["geometry"]["coordinates"])
    items = [(key, np.mean(points, axis=0), 1.0) for (key, _), points in groups.items()]
    housing = [f for f in read(MAP / "traffic-focus/housing/candidates.geojson")["features"]
               if f["properties"]["category"] == "social_housing" and f["properties"]["is_completed"] and f["properties"]["planned_households"]]
    scale = median(f["properties"]["planned_households"] for f in housing)
    items += [("social_housing", f["geometry"]["coordinates"], f["properties"]["planned_households"] / scale) for f in housing]
    keys = np.array([k for k, _, _ in items])
    xy = np.array([to_tm.transform(*c) for _, c, _ in items])
    size = np.array([m for _, _, m in items])
    meta = {
        "sources": ["map-data/traffic-focus/osm/candidates.geojson", "map-data/traffic-focus/housing/candidates.geojson"],
        "counts": dict(Counter(keys.tolist())),
        "categories": PLACE_LABELS,
        "size_rule": f"OSM 地點 m=1，同名群組（same_name_group_id）合併為一點；社會住宅只取已完工，m=規劃戶數 / 中位數 {scale:g} 戶",
        "time_factors": {k: dict(zip(BANDS, v)) for k, v in TAU.items()},
    }
    return keys, xy, size, meta


def bus_supply(points, to_tm):
    """Distance-weighted planned departures per hour (f) per cell and band, plus data-gap flags."""
    departures = subroute_departures()
    scheduled = {s for s, _ in departures}
    seq = read(DATA / "taipei_feeds/route_stop_sequences.json")
    stop_subroutes, subroute_route = defaultdict(set), {}
    for r in seq["records"]:
        stop_subroutes[r["stop_location_id"]].add(r["subroute_id"])
        subroute_route[r["subroute_id"]] = r["route_id"]
    stops = [(f["properties"]["stop_location_id"], f["geometry"]["coordinates"]) for f in read(DATA / "taipei_feeds/stop_locations.geojson")["features"]
             if f["geometry"]]
    tree = STRtree(shapely.points(np.array([to_tm.transform(*c) for _, c in stops])))
    cell_idx, stop_idx = tree.query(points, predicate="dwithin", distance=DECAY["bus"][1])
    dist = shapely.distance(points[cell_idx], tree.geometries[stop_idx])
    weight = decay(dist, "bus")
    best = defaultdict(dict)  # cell -> subroute -> max weight
    for c, s, w in zip(cell_idx, stop_idx, weight):
        for sub in stop_subroutes[stops[s][0]]:
            if w > best[c].get(sub, 0.0):
                best[c][sub] = w
    rate = {}
    for band, (_, day, (start, end), _) in BANDS.items():
        rate[band] = {sub: sum(series[start:end]) / (end - start) for (sub, d), series in departures.items() if d == day}
    f = {band: np.zeros(len(points)) for band in BANDS}
    partial = np.zeros(len(points), dtype=bool)
    unscheduled_near = np.zeros(len(points), dtype=int)
    for c, subs in best.items():
        for band in BANDS:
            f[band][c] = sum(w * rate[band].get(sub, 0.0) for sub, w in subs.items())
        served_routes = {subroute_route[s] for s in subs if s in scheduled}
        # Within about 300 m (w ≥ 0.5) and no scheduled subroute of the same route serves the cell.
        gaps = [s for s, w in subs.items() if s not in scheduled and w >= 0.5 and subroute_route[s] not in served_routes]
        unscheduled_near[c] = len(gaps)
        partial[c] = bool(gaps)
    meta = {
        "source": "data/taipei_feeds（stop_locations、route_stop_sequences、fixed_schedules、headway_schedules）",
        "sequence_subroutes": len(subroute_route),
        "scheduled_subroutes": len(scheduled),
        "rule": "每格對每條附線取最近站位的衰減權重 w，乘上該時段每小時計畫發車數後加總；固定班表優先，否則以班距換算",
        "partial_rule": "約 300 m 內（w ≥ 0.5）有缺班表附線，且同一路線沒有其他有班表的附線服務該格時標為 partial，供給為下限",
    }
    return f, partial, unscheduled_near, meta


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.parse_args()
    to_tm = Transformer.from_crs(4326, 3826, always_xy=True)
    city = make_valid(shape(next(f for f in read(MAP / "admin/cities.geojson")["features"] if f["properties"]["admin_code"] == "63000")["geometry"]))
    grid = grid_cells(city)
    points = shapely.points(*to_tm.transform(grid["lon"], grid["lat"]))
    n = len(points)

    population, population_meta = cell_population(grid, to_tm)
    stations = metro_stations()
    rail_xy, rail_activity, rail_meta = metro_activity(stations, read(DATA / "calendar/office_calendar.json"), to_tm)
    place_keys, place_xy, place_size, place_meta = places(to_tm)
    f, partial, unscheduled_near, bus_meta = bus_supply(points, to_tm)

    # Metro supply: best decay weight to any OSM metro / light-rail station.
    all_station_xy = np.array([to_tm.transform(*np.mean(p, axis=0)) for p in stations.values()])
    cx, cy = shapely.get_x(points), shapely.get_y(points)

    def distances(xy):
        return np.hypot(cx[:, None] - xy[None, :, 0], cy[:, None] - xy[None, :, 1])

    s_metro = decay(distances(all_station_xy), "metro").max(axis=1)
    rail_w = decay(distances(rail_xy), "metro")
    place_w = decay(distances(place_xy), "place") * place_size[None, :]
    district_of = district_lookup(grid)

    rng = np.random.default_rng(SEED)
    jitter = rng.uniform(0.8, 1.2, size=(SAMPLES, 3)) * np.array(list(WEIGHTS.values()))
    jitter /= jitter.sum(axis=1, keepdims=True)
    poi_by_band = {band: (place_w * np.array([TAU[k][i] for k in place_keys])[None, :]).sum(axis=1) for i, band in enumerate(BANDS)}
    active = (population >= MIN_POPULATION) | (np.max(list(poi_by_band.values()), axis=0) >= MIN_PLACE)

    def ranked(values):
        """Ranks within the analysis scope; NaN outside it."""
        result = np.full(n, np.nan)
        result[active] = rank01(values[active])
        return result

    top_n = math.ceil(active.sum() * TOP_SHARE)
    r_pop = ranked(population)
    bands = {}
    for band in BANDS:
        poi = poi_by_band[band]
        rail = rail_w @ rail_activity[band]
        s_bus = 1 - np.exp(-f[band] / F0)
        supply = 1 - (1 - s_bus) * (1 - s_metro)
        r_poi, r_rail, r_supply = ranked(poi), ranked(rail), ranked(supply)
        demand = WEIGHTS["population"] * r_pop + WEIGHTS["places"] * r_poi + WEIGHTS["metro"] * r_rail
        r_demand = ranked(demand)
        gap = r_demand - r_supply
        stack = np.stack([r_pop, r_poi, r_rail])
        hits = np.zeros(n)
        for w in jitter:
            r_d = ranked(w @ stack)
            # Only cells in the upper half of demand (the priority side of the quadrants) qualify.
            g = np.where(active & (r_d >= 0.5), r_d - r_supply, -np.inf)
            hits[np.argpartition(-g, top_n)[:top_n]] += 1
        hits[~active] = np.nan
        bands[band] = {
            "poi": poi, "rail": rail, "f": f[band], "s_bus": s_bus, "supply": supply,
            "r_poi": r_poi, "r_rail": r_rail, "r_supply": r_supply, "demand": demand, "r_demand": r_demand, "gap": gap,
            "robust": hits / SAMPLES,
        }

    def num(value, digits):
        return None if np.isnan(value) else round(float(value), digits)

    def quadrant(d, s):
        if np.isnan(d):
            return None
        if d >= 0.5:
            return "priority" if s < 0.5 else "saturated"
        return "oversupplied" if s >= 0.5 else "low_low"

    records = []
    for c in range(n):
        record = {
            "cell_id": f"{CELL_M}-{grid['ix'][c]}-{grid['iy'][c]}",
            "ix": int(grid["ix"][c]),
            "iy": int(grid["iy"][c]),
            "centre_longitude": round(float(grid["lon"][c]), 6),
            "centre_latitude": round(float(grid["lat"][c]), 6),
            "district_name": district_of[c],
            "population": round(float(population[c]), 1),
            "in_scope": bool(active[c]),
            "r_population": num(r_pop[c], 3),
            "metro_supply_score": round(float(s_metro[c]), 3),
            "data_status": "partial" if partial[c] else "ok",
            "unscheduled_subroutes_nearby": int(unscheduled_near[c]),
            "bands": {},
        }
        for band, v in bands.items():
            record["bands"][band] = [
                round(float(v["poi"][c]), 3),
                round(float(v["rail"][c]), 1),
                round(float(v["f"][c]), 2),
                round(float(v["s_bus"][c]), 3),
                round(float(v["supply"][c]), 3),
                num(v["r_poi"][c], 3),
                num(v["r_rail"][c], 3),
                num(v["r_supply"][c], 3),
                num(v["demand"][c], 3),
                num(v["r_demand"][c], 3),
                num(v["gap"][c], 3),
                quadrant(v["r_demand"][c], v["r_supply"][c]),
                num(v["robust"][c], 3),
            ]
        records.append(record)

    limitations = [
        "研究草案：權重、時間係數、距離衰減參數都是 docs/12 的專家假設，尚未以實測運量校準。",
        "人口是 2024-12 戶籍人口，以面積比例從最小統計區分到格子（假設區內均勻）；統計區界為 2015 年版。不是日間人口或搭乘量。",
        "只含臺北市人口與臺北市公共運輸處公車；新北市人口不計，市界附近需求與供給都可能低估。",
        "公車供給是計畫班次，以起站發車時刻歸入時段，未加行駛時間；partial 格的供給為下限。",
        "捷運供給只看與車站的距離，未計班距；捷運活動是各站進出站人次，不是 OD，也不分方向。",
        "地點只有類型與位置，學校、醫院等規模一律 m=1；OSM 同名群組已合併，其他重複可能仍在。",
        "直線距離，未計路網與河川、高架阻隔。",
        "排名在同一時段內計算，不同時段的分數不可直接比較；gap 是排名差，不是缺多少班次。",
        "供給排名是相對值：市區供給分數普遍接近飽和，priority 格可能仍有每小時二十班以上的加權班次，代表「比同時段其他格少」，不是絕對不足。",
    ]
    doc = {
        "schema_version": 1,
        "dataset_id": "taipei_transport_need_250",
        "metadata": {
            "title": "臺北市 250 m 網格運輸需求供需落差（第一版研究草案）",
            "method": "docs/12_運輸需求權重方法.md",
            "built_at": now(),
            "grain": "one record per 250 m grid cell (centre inside Taipei City); per-band values under bands",
            "band_fields": BAND_FIELDS,
            "crs": "EPSG:4326",
            "coordinate_order": ["longitude", "latitude"],
            "grid": {
                "cell_m": CELL_M,
                "origin": [round(grid["origin"][0], 7), round(grid["origin"][1], 7)],
                "step_deg": [grid["step"][0], grid["step"][1]],
                "note": f"與 web 班次網格相同的經緯度方格（以北緯 {GRID_LAT0}° 換算）；ix、iy 由 origin 起算",
                "cells": n,
            },
            "bands": {k: {"label": v[0], "bus_day_type": v[1], "hours": list(v[2]), "metro_day_set": v[3]} for k, v in BANDS.items()},
            "weights": WEIGHTS,
            "decay": {k: {"sigma_m": s, "cutoff_m": c} for k, (s, c) in DECAY.items()},
            "bus_saturation_f0_per_hour": F0,
            "scope": {
                "min_population": MIN_POPULATION,
                "min_place_score": MIN_PLACE,
                "rule": f"人口 ≥ {MIN_POPULATION} 人，或任一時段 place_score ≥ {MIN_PLACE} 的格子才排名；其餘多為山區、河川、公園，保留原始值，排名、gap、quadrant 為 null",
                "cells": int(active.sum()),
            },
            "robustness": {"samples": SAMPLES, "weight_jitter": "各權重乘以 U(0.8, 1.2) 後重新正規化", "top_share": TOP_SHARE, "seed": SEED},
            "formulas": {
                "rank": "R(x)：同一時段、分析範圍內格子的百分位排名（同值取平均名次），x = 0 時為 0",
                "demand_score": "α·R(人口) + β·R(地點) + γ·R(捷運活動)",
                "place_score": "Σ m_i × w(d) × τ_k(t)",
                "metro_activity": "Σ 站的每小時進站＋出站人次 × w(d)",
                "bus_supply_score": "1 − exp(−f / f0)，f = Σ 附線每小時發車數 × 最近站位 w(d)",
                "supply_score": "1 − (1 − bus_supply_score)(1 − metro_supply_score)，metro_supply_score = 最近捷運站 w(d)",
                "gap": "R(demand_score) − R(supply_score)",
                "quadrant": "以 0.5 為界：priority 高需求低供給、saturated 高需求高供給、oversupplied 低需求高供給、low_low 低需求低供給",
                "metro_activity_imputation": "環狀線 10 站缺出站字典，出站量以進站量代替",
                "robust_top5_share": "權重抽樣中，於需求排名前半（R(demand) ≥ 0.5）的格子裡 gap 排在前 N 名的比例；N = 分析範圍格數 × 5%（無條件進位）。名單可能含 saturated 格（需求極高、供給排名略高於 0.5）",
            },
            "inputs": {"population": population_meta, "metro": rail_meta, "places": place_meta, "bus": bus_meta,
                       "metro_supply": "map-data/osm 軌道車站，選取方式與 web 捷運車站圖層相同", "calendar": "data/calendar/office_calendar.json"},
            "missing_value_policy": "缺值為 null；沒有站點或地點的格子分數為 0（觀測值），缺班表以 data_status 標示，不補值。",
            "limitations": limitations,
        },
        "records": records,
    }
    OUT.mkdir(parents=True, exist_ok=True)
    output = OUT / f"transport_need_{CELL_M}.json"
    output.write_text(json.dumps(doc, ensure_ascii=False, allow_nan=False, separators=(",", ":")) + "\n", encoding="utf-8", newline="\n")
    RAW.mkdir(parents=True, exist_ok=True)
    (RAW / "delivery.json").write_text(json.dumps({
        "schema_version": 1,
        "prepared_at": now(),
        "datasets": [{
            "dataset_id": doc["dataset_id"],
            "status": "derived_research_draft",
            "output_files": [output.relative_to(ROOT).as_posix()],
            "record_count": n,
            "period": {"population": population_meta["population_period"], "metro": rail_meta["source_period"], "bus": "現行班表快照"},
            "limitations": limitations,
            "bytes": output.stat().st_size,
        }],
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")

    summary = {
        "cells": n,
        "population": population_meta,
        "metro_days": rail_meta["day_counts"],
        "excluded_dates": rail_meta["excluded_anomaly_dates"],
        "station_status": dict(Counter(m["status"] for m in rail_meta["station_matches"])),
        "places": place_meta["counts"],
        "partial_cells": int(partial.sum()),
        "quadrants": {b: dict(Counter(r["bands"][b][BAND_FIELDS.index("quadrant")] for r in records)) for b in BANDS},
        "in_scope": int(active.sum()),
        "in_scope_population_share": round(float(population[active].sum() / population.sum()), 4),
        "robust_cells_ge_0.8": {b: int((v["robust"] >= 0.8).sum()) for b, v in bands.items()},
        "size_mb": round(output.stat().st_size / 1e6, 2),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=1))


def district_lookup(grid):
    districts = [(f["properties"]["district_name"], make_valid(shape(f["geometry"]))) for f in read(MAP / "admin/districts.geojson")["features"]
                 if f["properties"]["city_code"] == "63000"]
    names = [None] * len(grid["lon"])
    for name, geometry in districts:
        for i in np.flatnonzero(shapely.contains_xy(geometry, grid["lon"], grid["lat"])):
            names[i] = name
    return names


if __name__ == "__main__":
    main()
