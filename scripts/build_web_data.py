#!/usr/bin/env python3
"""Build the small GeoJSON/JSON layers used by the web map in web/public/data/."""

import argparse
import gzip
import json
import math
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import shapely
from pyproj import Geod, Transformer
from shapely import STRtree, make_valid
from shapely.geometry import box, mapping, shape
from shapely.ops import unary_union
from shapely.prepared import prep

ROOT = Path(__file__).resolve().parents[1]
FEEDS = ROOT / "data/taipei_feeds"
MAP = ROOT / "map-data"
OUT = ROOT / "web/public/data"

# Taipei City plus a margin of the surrounding New Taipei area.
VIEW_BBOX = (121.40, 24.93, 121.70, 25.23)
CELL_SIZES = (100, 150, 250, 500)
# Station-coverage grid: 50 m cells, distances in 10 m steps up to 2.5 km.
COVERAGE_CELL_M = 50
COVERAGE_UNIT_M = 10
COVERAGE_CAP = 250
COVERAGE_OUTSIDE = 255
# Walking catchment radii; station information always uses the 350 m entry.
CATCHMENT_RADII = (200, 350, 500)
POPULATION = ROOT / "data/population"
GRID_LAT0 = 25.05
# ISO weekday used as the representative service day for each day type.
DAY_TYPES = {"weekday": 3, "saturday": 6, "sunday": 7}

ROAD_CLASSES = {"motorway", "trunk", "primary", "secondary"}
METRO_CLASSES = {"subway", "light_rail", "monorail"}
METRO_NAME = re.compile(r"捷運|輕軌|信義線東延段|^板南線$|^中和新蘆線$")
METRO_EXCLUDE = re.compile(r"機廠|測試軌|袋|洗車|儲車|卸載|扇形|預定地|Crossover")
METRO_LINES = [
    ("文湖線", "BR", "#c48c31"),
    ("淡水信義線", "R", "#e3002c"),
    ("信義線東延段", "R", "#e3002c"),
    ("新北投支線", "R", "#fd92a3"),
    ("松山新店線", "G", "#008659"),
    ("小碧潭支線", "G", "#cfdb00"),
    ("中和新蘆線", "O", "#f8b61c"),
    ("板南線", "BL", "#0070bd"),
    ("環狀線", "Y", "#ffdb00"),
    ("機場捷運", "A", "#8246af"),
    ("三鶯線", "LB", "#77bce6"),
    ("安坑輕軌", "K", "#c3b091"),
    ("淡海輕軌", "V", "#e3002c"),
]


def read(path):
    if path.suffix == ".gz":
        with gzip.open(path, "rt", encoding="utf-8") as f:
            return json.load(f)
    return json.loads(path.read_text(encoding="utf-8"))


def write(name, doc):
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / name).write_text(json.dumps(doc, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"{name}: {(OUT / name).stat().st_size / 1e6:.2f} MB")


def collection(features, metadata):
    return {"type": "FeatureCollection", "metadata": metadata, "features": features}


def rounded(geometry, digits=5):
    def walk(value):
        if isinstance(value, (list, tuple)):
            if value and isinstance(value[0], (int, float)):
                return [round(value[0], digits), round(value[1], digits)]
            return [walk(v) for v in value]
        return value

    geo = mapping(geometry)
    return {"type": geo["type"], "coordinates": walk(geo["coordinates"])}


def simplified(geometry, tolerance):
    geometry = geometry.simplify(tolerance, preserve_topology=True)
    return None if geometry.is_empty else geometry


def osm_layer(name):
    manifest = read(MAP / "osm/manifest.json")
    entry = next(r for r in manifest["records"] if r["dataset_id"] == f"osm_taipei_new_taipei_{name}")
    for output in entry["outputs"]:
        yield from read(ROOT / output["path"])["features"]


def osm_source():
    return read(MAP / "osm/manifest.json")["metadata"]["source"]


def hour_minutes(text):
    hours, minutes = text.split(":")
    return int(hours) * 60 + int(minutes)


def subroute_departures():
    """Planned departures per subroute × day type × hour, from fixed times and headway windows."""
    fixed = defaultdict(lambda: [0.0] * 24)
    for record in read(FEEDS / "fixed_schedules.json")["records"]:
        for day, iso in DAY_TYPES.items():
            if record["service_weekday_iso"] == iso:
                fixed[(record["subroute_id"], day)][hour_minutes(record["departure_time"]) // 60 % 24] += 1
    headway = defaultdict(lambda: [0.0] * 24)
    for record in read(FEEDS / "headway_schedules.json")["records"]:
        values = [v for v in (record["long_headway_minutes"], record["low_headway_minutes"]) if v]
        if not values:
            continue
        minutes = sum(values) / len(values)
        start, end = hour_minutes(record["start_time"]), hour_minutes(record["end_time"])
        # A window ending before it starts runs past midnight; equal times are a single departure.
        if end < start:
            end += 24 * 60
        for day, iso in DAY_TYPES.items():
            if record["service_weekday_iso"] != iso:
                continue
            counts = headway[(record["subroute_id"], day)]
            if end == start:
                counts[start // 60 % 24] += 1
                continue
            for hour in range(start // 60, math.ceil(end / 60)):
                overlap = min(end, (hour + 1) * 60) - max(start, hour * 60)
                if overlap > 0:
                    counts[hour % 24] += overlap / minutes
    result = {}
    for key in set(fixed) | set(headway):
        f, h = fixed.get(key, [0.0] * 24), headway.get(key, [0.0] * 24)
        # Fixed departure times win for any hour they cover; headway windows fill the rest.
        result[key] = [f[i] if f[i] else h[i] for i in range(24)]
    return result


def build_grid(city, stops, stop_subroutes, departures, scheduled, cell_m):
    geod = Geod(ellps="GRS80")
    lon_step = cell_m / geod.inv(121.5, GRID_LAT0, 121.51, GRID_LAT0)[2] * 0.01
    lat_step = cell_m / geod.inv(121.5, GRID_LAT0, 121.5, GRID_LAT0 + 0.01)[2] * 0.01
    minx, miny, maxx, maxy = city.bounds
    origin = (math.floor(minx / lon_step) * lon_step, math.floor(miny / lat_step) * lat_step)
    cols = math.ceil((maxx - origin[0]) / lon_step)
    rows = math.ceil((maxy - origin[1]) / lat_step)
    city_prepared = prep(city)

    cell_stops = defaultdict(list)
    for stop_id, (lon, lat) in stops.items():
        cell_stops[(int((lon - origin[0]) // lon_step), int((lat - origin[1]) // lat_step))].append(stop_id)

    cells, status = [], Counter()
    for ix in range(cols):
        for iy in range(rows):
            x0, y0 = origin[0] + ix * lon_step, origin[1] + iy * lat_step
            if not city_prepared.intersects(box(x0, y0, x0 + lon_step, y0 + lat_step)):
                continue
            subroutes = {s for stop in cell_stops.get((ix, iy), []) for s in stop_subroutes[stop]}
            known = sorted(s for s in subroutes if s in scheduled)
            unknown = len(subroutes) - len(known)
            values = {}
            for day in DAY_TYPES:
                if not known:
                    values[day] = None
                    continue
                hourly = [0.0] * 24
                for subroute in known:
                    for hour, count in enumerate(departures.get((subroute, day), ())):
                        hourly[hour] += count
                values[day] = [round(v, 1) for v in hourly]
            cell_status = "no_stop" if not subroutes else ("no_schedule" if not known else ("partial" if unknown else "ok"))
            status[cell_status] += 1
            cells.append([ix, iy, len(cell_stops.get((ix, iy), [])), len(subroutes), unknown, values["weekday"], values["saturday"], values["sunday"]])
    return {
        "origin": [round(origin[0], 7), round(origin[1], 7)],
        "step": [lon_step, lat_step],
        "cell_m": cell_m,
        "fields": ["ix", "iy", "stop_count", "subroute_count", "unscheduled_subroute_count", "weekday", "saturday", "sunday"],
        "cells": cells,
    }, status


def build_coverage(city, station_types):
    """Straight-line distance from each 50 m cell centre in Taipei to the nearest station of each type.

    Rows run north to south and columns west to east; each value is the distance in
    COVERAGE_UNIT_M steps, capped at COVERAGE_CAP, with COVERAGE_OUTSIDE for cells whose
    centre lies outside Taipei City.
    """
    geod = Geod(ellps="GRS80")
    lon_step = COVERAGE_CELL_M / geod.inv(121.5, GRID_LAT0, 121.51, GRID_LAT0)[2] * 0.01
    lat_step = COVERAGE_CELL_M / geod.inv(121.5, GRID_LAT0, 121.5, GRID_LAT0 + 0.01)[2] * 0.01
    minx, miny, maxx, maxy = city.bounds
    west, north = math.floor(minx / lon_step) * lon_step, math.ceil(maxy / lat_step) * lat_step
    cols, rows = math.ceil((maxx - west) / lon_step), math.ceil((north - miny) / lat_step)
    lon, lat = np.meshgrid(west + (np.arange(cols) + 0.5) * lon_step, north - (np.arange(rows) + 0.5) * lat_step)
    lon, lat = lon.ravel(), lat.ravel()
    inside = shapely.contains_xy(city, lon, lat)
    # Measure in TWD97 / TM2 (EPSG:3826) metres.
    to_tm = Transformer.from_crs(4326, 3826, always_xy=True)
    centres = shapely.points(*to_tm.transform(lon[inside], lat[inside]))
    fields = {}
    for name, coords in station_types.items():
        tree = STRtree(shapely.points(*to_tm.transform(*np.array(coords).T)))
        (index, _), distance = tree.query_nearest(centres, return_distance=True, all_matches=False)
        nearest = np.empty(len(centres))
        nearest[index] = distance
        values = np.full(rows * cols, COVERAGE_OUTSIDE, dtype=np.uint8)
        values[inside] = np.minimum(np.rint(nearest / COVERAGE_UNIT_M), COVERAGE_CAP)
        fields[name] = values.tolist()
    return {
        "west": round(west, 7),
        "north": round(north, 7),
        "step": [lon_step, lat_step],
        "size": [cols, rows],
        "cell_m": COVERAGE_CELL_M,
        "unit_m": COVERAGE_UNIT_M,
        "cap": COVERAGE_CAP,
        "outside": COVERAGE_OUTSIDE,
        "fields": fields,
    }, int(inside.sum())


def build_catchments(stops):
    """Registered population within each walking radius of the supplied station points.

    Each statistical area's population is split by the share of its area inside the circle
    (uniform density within the area). data_share is the share of the circle covered by
    Taipei statistical areas; the rest (New Taipei, mountains outside the data) adds nothing.
    """
    boundaries = read(POPULATION / "taipei_statistical_area_boundaries.geojson")
    population = read(POPULATION / "taipei_statistical_population_base.json")
    counts = {r["base_area_id"]: r for r in population["records"]}
    to_tm = Transformer.from_crs(4326, 3826, always_xy=True)
    areas, people, households = [], [], []
    for feature in boundaries["features"]:
        record = counts.get(feature["properties"]["base_area_id"])
        if record is None:
            continue
        areas.append(shapely.transform(shape(feature["geometry"]), lambda xy: np.column_stack(to_tm.transform(xy[:, 0], xy[:, 1]))))
        people.append(record["population"])
        households.append(record["households"])
    areas = np.array(areas)
    area_m2 = shapely.area(areas)
    people, households = np.array(people, dtype=float), np.array(households, dtype=float)
    tree = STRtree(areas)
    result = {}
    for stop_id, (lon, lat) in stops.items():
        centre = shapely.Point(to_tm.transform(lon, lat))
        candidates = tree.query(centre.buffer(max(CATCHMENT_RADII)), predicate="intersects")
        values = []
        for radius in CATCHMENT_RADII:
            circle = centre.buffer(radius, quad_segs=16)
            share = shapely.area(shapely.intersection(areas[candidates], circle)) / area_m2[candidates] if len(candidates) else np.zeros(0)
            data_share = float((share * area_m2[candidates]).sum() / circle.area) if len(candidates) else 0.0
            if data_share < 0.005:
                values.append([None, None, 0])
            else:
                values.append([round(float(share @ people[candidates])), round(float(share @ households[candidates])), round(min(data_share, 1.0), 2)])
        result[stop_id] = values
    meta = {
        "population_source": "data/population/taipei_statistical_population_base.json（內政部最小統計區戶籍人口）",
        "population_period": population["metadata"]["source_period"],
        "boundary_source": "data/population/taipei_statistical_area_boundaries.geojson",
        "boundary_vintage": boundaries["metadata"]["boundary_vintage_year_in_filename"],
    }
    return result, meta


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()
    built_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    view = box(*VIEW_BBOX)
    osm = osm_source()
    osm_meta = {"source": osm["source_page"], "source_updated_at": osm["source_updated_at"], "attribution": osm["attribution"], "license": osm["license"]}

    # Administrative boundaries and labels.
    cities = read(MAP / "admin/cities.geojson")["features"]
    city = make_valid(shape(next(f for f in cities if f["properties"]["admin_code"] == "63000")["geometry"]))
    index = {r["admin_code"]: r for r in read(MAP / "admin/admin_index.json")["records"]}
    districts, labels = [], []
    for feature in read(MAP / "admin/districts.geojson")["features"]:
        geometry = shape(feature["geometry"])
        if not geometry.intersects(view):
            continue
        props = feature["properties"]
        districts.append({"type": "Feature", "properties": {"name": props["district_name"], "city_code": props["city_code"]}, "geometry": rounded(simplified(geometry, 0.0002))})
        lon, lat = index[props["admin_code"]]["label_point"]
        labels.append({"type": "Feature", "properties": {"name": props["district_name"].removesuffix("區"), "city_code": props["city_code"]}, "geometry": {"type": "Point", "coordinates": [round(lon, 5), round(lat, 5)]}})
    admin_meta = {"source": "map-data/admin（內政部國土測繪中心）", "simplify_tolerance_deg": 0.0002}
    write("districts.geojson", collection(districts, admin_meta))
    write("district_labels.geojson", collection(labels, admin_meta))
    write("taipei_boundary.geojson", collection([{"type": "Feature", "properties": {"name": "臺北市"}, "geometry": rounded(simplified(city, 0.0001))}], admin_meta))

    # OSM base layers.
    def clipped(layer, classes, tolerance):
        features = []
        for feature in osm_layer(layer):
            cls = feature["properties"]["feature_class"]
            if cls not in classes:
                continue
            geometry = shape(feature["geometry"])
            if not geometry.intersects(view):
                continue
            geometry = simplified(geometry.intersection(view), tolerance)
            if geometry is None:
                continue
            features.append({"type": "Feature", "properties": {"class": cls}, "geometry": rounded(geometry)})
        return features

    write("roads.geojson", collection(clipped("roads", ROAD_CLASSES, 0.00005), osm_meta))

    # MRT and light-rail lines with nearby stations.
    lines, metro_geoms = [], []
    for feature in osm_layer("railways"):
        props = feature["properties"]
        name = props.get("name") or ""
        if props["feature_class"] not in METRO_CLASSES or not METRO_NAME.search(name) or METRO_EXCLUDE.search(name):
            continue
        geometry = shape(feature["geometry"])
        if not geometry.intersects(view):
            continue
        line = next(((label, code, color) for label, code, color in METRO_LINES if label in name or name == label), ("其他軌道", "", "#8a6fb5"))
        metro_geoms.append(geometry)
        lines.append({"type": "Feature", "properties": {"name": name, "line": line[0], "code": line[1], "color": line[2]}, "geometry": rounded(simplified(geometry, 0.00003))})
    write("mrt_lines.geojson", collection(lines, {**osm_meta, "selection": "OSM railways subway/light_rail/monorail named as MRT or light-rail lines; depot and test tracks excluded"}))
    metro_buffer = prep(unary_union(metro_geoms).buffer(0.0008))
    stations, seen = [], set()
    for feature in osm_layer("transport"):
        props = feature["properties"]
        geometry = shape(feature["geometry"])
        if props["feature_class"] != "railway_station" or not props.get("name") or not metro_buffer.contains(geometry):
            continue
        if props["name"] in seen:
            continue
        seen.add(props["name"])
        stations.append({"type": "Feature", "properties": {"id": str(props["osm_id"]), "name": props["name"]}, "geometry": rounded(geometry)})
    write("mrt_stations.geojson", collection(stations, {**osm_meta, "selection": "OSM railway_station within about 80 m of a selected metro line; deduplicated by name"}))

    # Important places: OSM demand candidates and official social-housing projects.
    places = []
    osm_places = read(MAP / "traffic-focus/osm/candidates.geojson")
    for feature in osm_places["features"]:
        p = feature["properties"]
        places.append({
            "type": "Feature",
            "properties": {"id": p["candidate_id"], "name": p["name"], "category": p["category"], "district": p["district_name"], "source": "OSM"},
            "geometry": rounded(shape(feature["geometry"])),
        })
    housing = read(MAP / "traffic-focus/housing/candidates.geojson")
    for feature in housing["features"]:
        p = feature["properties"]
        places.append({
            "type": "Feature",
            "properties": {
                "id": p["candidate_id"],
                "name": p["name"],
                "category": p["category"],
                "district": p["district_name"],
                "source": "臺北市都市發展局社宅 API",
                "planned_households": p["planned_households"],
                "progress": p["project_progress"],
            },
            "geometry": rounded(shape(feature["geometry"])),
        })
    write("places.geojson", collection(places, {
        "source": ["map-data/traffic-focus/osm/candidates.geojson", "map-data/traffic-focus/housing/candidates.geojson"],
        "osm_source_period": osm_places["metadata"]["source_period"],
        "limitations": "設施候選位置，不是出入口，也不是已驗證的人流排名；OSM 同一設施可能有多個標記，兩個來源未去重。",
    }))

    # YouBike 2.0 stations (official Taipei feed, station attributes only).
    youbike = read(ROOT / "data/youbike/stations.geojson")
    write("youbike_stations.geojson", collection([{
        "type": "Feature",
        "properties": {k: f["properties"][k] for k in ("station_id", "name", "district", "dock_count", "is_active")},
        "geometry": rounded(shape(f["geometry"])),
    } for f in youbike["features"]], {
        "source": "data/youbike/stations.geojson（臺北市政府交通局 YouBike 2.0）",
        "source_data_time": youbike["metadata"]["source_data_time"],
        "limitations": "只有站點與總停車格數，沒有即時可借車數，也不代表使用量；只含臺北市站點。",
    }))

    # Bus network and planned departures.
    routes = read(FEEDS / "routes.json")["records"]
    route_names = {r["route_id"]: r["route_names_zh"][0] for r in routes}
    sequences = read(FEEDS / "route_stop_sequences.json")
    stop_subroutes, stop_routes = defaultdict(set), defaultdict(set)
    subroute_route = {}
    for record in sequences["records"]:
        stop_subroutes[record["stop_location_id"]].add(record["subroute_id"])
        stop_routes[record["stop_location_id"]].add(record["route_id"])
        subroute_route[record["subroute_id"]] = record["route_id"]
    departures = subroute_departures()
    scheduled = {subroute for subroute, _ in departures}
    weekday_total = {subroute: sum(departures.get((subroute, "weekday"), ())) for subroute in scheduled}
    route_weekday = Counter()
    for subroute, total in weekday_total.items():
        if subroute in subroute_route:
            route_weekday[subroute_route[subroute]] += total

    stop_doc = read(FEEDS / "stop_locations.geojson")
    stops, stop_features = {}, []
    for feature in stop_doc["features"]:
        props = feature["properties"]
        lon, lat = props["longitude"], props["latitude"]
        if lon is None or lat is None:
            continue
        stop_id = props["stop_location_id"]
        stops[stop_id] = (lon, lat)
        known = [s for s in stop_subroutes[stop_id] if s in scheduled]
        stop_features.append({
            "type": "Feature",
            "properties": {
                "id": stop_id,
                "name": props["name"],
                "route_count": len(stop_routes[stop_id]),
                "routes": sorted(route_names.get(r, r) for r in stop_routes[stop_id]),
                "weekday_departures": round(sum(weekday_total[s] for s in known)) if known else None,
            },
            "geometry": {"type": "Point", "coordinates": [round(lon, 5), round(lat, 5)]},
        })
    bus_meta = {
        "source": "data/taipei_feeds（臺北市公共運輸處 blob）",
        "source_data_time": sequences["metadata"]["source_data_time"],
        "weekday_departures": "代表平日（週三）各附線計畫發車數加總；固定班表優先，否則以班距區間估算",
    }
    write("bus_stops.geojson", collection(stop_features, bus_meta))

    # Use the exact displayed points, with separate source ID namespaces. Never join
    # a metro or bike station to a nearby bus stop to borrow its population estimate.
    station_points = {
        "bus": {f["properties"]["id"]: f["geometry"]["coordinates"] for f in stop_features},
        "mrt": {f["properties"]["id"]: f["geometry"]["coordinates"] for f in stations},
        "youbike": {f["properties"]["station_id"]: rounded(shape(f["geometry"]))["coordinates"] for f in youbike["features"]},
    }
    catchments, population_meta = build_catchments({
        f"{kind}:{station_id}": point
        for kind, points in station_points.items() for station_id, point in points.items()
    })
    write("station_catchments.json", {
        "schema_version": "1.0",
        "dataset_id": "web_station_catchments",
        "radii": list(CATCHMENT_RADII),
        "fields": ["population", "households", "data_share"],
        "records": {
            kind: {sid: catchments[f"{kind}:{sid}"] for sid in points}
            for kind, points in station_points.items()
        },
        "metadata": {
            **population_meta,
            "station_sources": {"bus": "bus_stops.geojson: id", "mrt": "mrt_stations.geojson: id (OSM)", "youbike": "youbike_stations.geojson: station_id"},
            "built_at": built_at,
            "value": "站位為圓心、各半徑直線圓內的戶籍人口與戶數；統計區只有部分在圓內時，依面積比例分攤（假設區內人口均勻分布）",
            "data_share": "圓面積中有臺北市統計區人口資料的比例；覆蓋低於 0.5% 時人口與戶數為 null、data_share 為 0；其餘比例四捨五入至小數 2 位",
            "limitations": [
                "戶籍人口不等於白天活動人口，也不是搭乘量；來源沒有年齡分布。",
                f"統計區邊界為 {population_meta['boundary_vintage']} 年版，人口為 {population_meta['population_period']['end']}，兩者版本不同。",
                "只有臺北市人口；圓延伸到新北市的部分不計，data_share 會小於 1。",
                "相鄰站點的範圍互相重疊，各站人口不可加總；捷運以車站點位而非出入口計算。",
                "直線距離，未計入路網、河川或高架阻隔。",
            ],
        },
    })

    coverage, inside = build_coverage(city, {
        "bus": list(stops.values()),
        "mrt": [f["geometry"]["coordinates"] for f in stations],
        "youbike": [f["geometry"]["coordinates"] for f in youbike["features"]],
    })
    coverage["metadata"] = {
        "built_at": built_at,
        "cell": f"約 {COVERAGE_CELL_M} m 經緯度方格（以北緯 {GRID_LAT0}° 換算），只保留格心在臺北市界內的 {inside} 格",
        "value": "格心到最近一個該類站點的直線距離（TWD97 平面公尺），以 unit_m 為單位，超過 cap 一律記為 cap",
        "sources": {
            "bus": "data/taipei_feeds stop_locations（含跨市站位）",
            "mrt": "map-data/osm 軌道車站（與 mrt_stations 圖層相同）",
            "youbike": "data/youbike/stations.geojson（只含臺北市 YouBike 2.0，含快照時暫停營運站）",
        },
        "limitations": [
            "直線距離，未計入路網、河川、鐵路或高架阻隔，也不代表實際步行或騎乘時間。",
            "捷運以車站點位計算，不是出入口；公車站位不分方向與班次多寡。",
            "YouBike 只有臺北市站點，靠近新北市界的格子會高估距離；不代表即時有車或有空位。",
        ],
    }
    write(f"coverage_{COVERAGE_CELL_M}.json", coverage)

    # Transport-need grid (research draft from scripts/build_transport_need.py), as column arrays.
    need_path = ROOT / "data/analysis/transport_need_250.json"
    if need_path.exists():
        need = read(need_path)
        meta, records = need["metadata"], need["records"]
        field = {name: i for i, name in enumerate(meta["band_fields"])}
        column = lambda key: [r[key] for r in records]
        write("transport_need_250.json", {
            "cell_m": meta["grid"]["cell_m"],
            "origin": meta["grid"]["origin"],
            "step": meta["grid"]["step_deg"],
            "bands": [{"key": k, "label": v["label"]} for k, v in meta["bands"].items()],
            "weights": meta["weights"],
            "cells": {
                "ix": column("ix"),
                "iy": column("iy"),
                "district": column("district_name"),
                "population": [round(r["population"]) for r in records],
                "in_scope": [int(r["in_scope"]) for r in records],
                "partial": [int(r["data_status"] == "partial") for r in records],
                "r_pop": column("r_population"),
                "s_metro": column("metro_supply_score"),
            },
            "values": {band: {
                short: [r["bands"][band][field[name]] for r in records]
                for short, name in (("poi", "place_score"), ("rail", "metro_activity"), ("f", "bus_weighted_departures_per_hour"),
                                    ("supply", "supply_score"), ("r_poi", "r_places"), ("r_rail", "r_metro"), ("r_supply", "r_supply"),
                                    ("robust", "robust_top5_share"))
            } for band in meta["bands"]},
            "metadata": {
                "source": "data/analysis/transport_need_250.json",
                "built_at": meta["built_at"],
                "scope": meta["scope"]["rule"],
                "population_period": meta["inputs"]["population"]["population_period"],
                "metro_period": meta["inputs"]["metro"]["source_period"],
                "robust_rule": meta["formulas"]["robust_top5_share"],
                "limitations": meta["limitations"],
            },
        })

    route_features = []
    for feature in read(FEEDS / "route_shapes.geojson")["features"]:
        geometry = shape(feature["geometry"])
        if not geometry.intersects(view):
            continue
        route_id = feature["properties"]["route_id"]
        route_features.append({
            "type": "Feature",
            "properties": {
                "route_id": route_id,
                "name": route_names.get(route_id, route_id),
                "direction": feature["properties"]["direction_label"],
                "weekday_departures": round(route_weekday[route_id]) if route_id in route_weekday else None,
            },
            "geometry": rounded(simplified(geometry, 0.00004)),
        })
    write("bus_routes.geojson", collection(route_features, bus_meta))

    for cell_m in CELL_SIZES:
        grid, status = build_grid(city, stops, stop_subroutes, departures, scheduled, cell_m)
        grid["metadata"] = {
            **bus_meta,
            "built_at": built_at,
            "cell": f"約 {cell_m} m 經緯度方格（以北緯 {GRID_LAT0}° 換算），只保留與臺北市界相交的格子",
            "value": "格內站牌所屬附線的每小時計畫發車數加總；同一附線在同一格只計一次，不分方向",
            "day_types": {day: f"ISO weekday {iso}" for day, iso in DAY_TYPES.items()},
            "limitations": [
                "以起站發車時刻歸入小時，未加上行駛到該格所需時間。",
                "只含臺北市公共運輸處 feed；新北市專屬路線不在其中，市界附近可能低估。",
                "班距區間以最長與最短班距平均換算班次，屬估計值。",
                "沒有班表的附線不計入；格內全部附線都沒班表時值為 null（資料缺漏，不是 0）。",
                "這是計畫供給，不是實際發車或載客量。",
            ],
            "status_counts": dict(status),
            "scheduled_subroutes": len(scheduled),
            "sequence_subroutes": len({s for v in stop_subroutes.values() for s in v}),
        }
        write(f"bus_grid_{cell_m}.json", grid)
        print(f"grid {cell_m} m status", dict(status), "scheduled subroutes", len(scheduled))


if __name__ == "__main__":
    main()
