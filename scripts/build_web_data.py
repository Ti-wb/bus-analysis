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

from pyproj import Geod
from shapely import make_valid
from shapely.geometry import box, mapping, shape
from shapely.ops import unary_union
from shapely.prepared import prep

ROOT = Path(__file__).resolve().parents[1]
FEEDS = ROOT / "data/taipei_feeds"
MAP = ROOT / "map-data"
OUT = ROOT / "web/public/data"

# Taipei City plus a margin of the surrounding New Taipei area.
VIEW_BBOX = (121.40, 24.93, 121.70, 25.23)
CELL_SIZES = (250, 500)
GRID_LAT0 = 25.05
# ISO weekday used as the representative service day for each day type.
DAY_TYPES = {"weekday": 3, "saturday": 6, "sunday": 7}

ROAD_CLASSES = {"motorway", "trunk", "primary", "secondary"}
WATER_CLASSES = {"water", "riverbank", "reservoir", "dock"}
GREEN_CLASSES = {"park", "forest", "recreation_ground"}
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
        if end <= start:
            end += 24 * 60
        for day, iso in DAY_TYPES.items():
            if record["service_weekday_iso"] != iso:
                continue
            counts = headway[(record["subroute_id"], day)]
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
    def clipped(layer, classes, tolerance, min_area=0.0):
        features = []
        for feature in osm_layer(layer):
            cls = feature["properties"]["feature_class"]
            if cls not in classes:
                continue
            geometry = shape(feature["geometry"])
            if not geometry.intersects(view):
                continue
            geometry = simplified(geometry.intersection(view), tolerance)
            if geometry is None or (min_area and geometry.area < min_area):
                continue
            features.append({"type": "Feature", "properties": {"class": cls}, "geometry": rounded(geometry)})
        return features

    write("water.geojson", collection(clipped("water", WATER_CLASSES, 0.00005, 2e-7), osm_meta))
    write("green.geojson", collection(clipped("landuse", GREEN_CLASSES, 0.0001, 1e-6), osm_meta))
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
        stations.append({"type": "Feature", "properties": {"name": props["name"]}, "geometry": rounded(geometry)})
    write("mrt_stations.geojson", collection(stations, {**osm_meta, "selection": "OSM railway_station within about 80 m of a selected metro line; deduplicated by name"}))

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
