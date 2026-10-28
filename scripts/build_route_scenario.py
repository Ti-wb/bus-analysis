#!/usr/bin/env python3
"""Build the route editor's source network and disjoint 100 m population cells.

Run after build_web_data.py; only owns web/public/data/route_scenario.json.
"""

from collections import defaultdict
from datetime import datetime, timezone

import numpy as np
import shapely
from pyproj import Transformer
from shapely import STRtree
from shapely.geometry import box, shape

if __package__:
    from .build_web_data import FEEDS, MAP, OUT, POPULATION, ROOT, read, rounded, subroute_departures, write
else:
    from build_web_data import FEEDS, MAP, OUT, POPULATION, ROOT, read, rounded, subroute_departures, write


def main():
    locations = read(OUT / "bus_stops.geojson")["features"]
    stops = {f["properties"]["id"]: {
        "name": f["properties"]["name"], "at": f["geometry"]["coordinates"],
    } for f in locations}
    sequences = read(FEEDS / "route_stop_sequences.json")
    branches = {r["subroute_id"]: r for r in read(FEEDS / "route_branches.json")["records"]}
    groups = defaultdict(list)
    missing = 0
    for r in sequences["records"]:
        if r["stop_location_id"] not in stops:
            missing += 1
            continue
        groups[(r["subroute_id"], r["direction_code"])].append(r)
    shapes = {}
    for f in read(FEEDS / "route_shapes.geojson")["features"]:
        p = f["properties"]
        # Only an exact subroute/direction shape or its explicitly identified main-route shape.
        key = (p["subroute_id"] or p["route_id"], p["direction_code"])
        if key not in shapes or p["subroute_id"]:
            shapes[key] = rounded(shape(f["geometry"]).simplify(.00003))["coordinates"]
    departures = subroute_departures()
    routes = []
    excluded = {"missing_branch": [], "fewer_than_two_stops": []}
    for (sub, direction), rows in sorted(groups.items()):
        b = branches.get(sub)
        if b is None:
            excluded["missing_branch"].append({"id": f"{sub}:{direction}", "stop_occurrences": len(rows)})
            continue
        ordered = sorted(rows, key=lambda r: (r["stop_sequence"], r["path_detail_id"]))
        stop_ids = [r["stop_location_id"] for r in ordered]
        if len(set(stop_ids)) < 2:
            excluded["fewer_than_two_stops"].append({"id": f"{sub}:{direction}", "stop_occurrences": len(rows)})
            continue
        routes.append({
            "id": f"{sub}:{direction}", "route_id": b["route_id"], "subroute_id": sub,
            "name": b["route_name_zh"], "branch_name": b["subroute_name_zh"],
            "direction": direction,
            "direction_label": {"0": "去程", "1": "返程"}.get(direction, f"方向 {direction}"),
            "stop_ids": stop_ids,
            "path": shapes.get((sub, direction)),
            # Schedules are NOT direction-specific. Keep this assumption explicit in metadata/UI.
            "hourly": {day: [round(v, 4) for v in departures[(sub, day)]] if (sub, day) in departures else None
                       for day in ("weekday", "saturday", "sunday")},
        })

    grid = read(OUT / "bus_grid_100.json")
    ox, oy = grid["origin"]
    sx, sy = grid["step"]
    indices = np.array([[r[0], r[1]] for r in grid["cells"]])
    lon = ox + (indices[:, 0] + .5) * sx
    lat = oy + (indices[:, 1] + .5) * sy
    tm = Transformer.from_crs(4326, 3826, always_xy=True)

    def project(g):
        return shapely.transform(g, lambda xy: np.column_stack(tm.transform(xy[:, 0], xy[:, 1])))

    centres = shapely.points(*tm.transform(lon, lat))
    tree = STRtree(centres)
    stop_cells = {}
    for sid, s in stops.items():
        point = shapely.Point(tm.transform(*s["at"]))
        stop_cells[sid] = sorted(int(i) for i in tree.query(point, predicate="dwithin", distance=350))

    def near(features, radius):
        pts = shapely.points(*tm.transform(*np.array([f["geometry"]["coordinates"] for f in features]).T))
        result = np.zeros(len(centres), dtype=np.uint8)
        if len(pts):
            result[np.unique(tree.query(pts, predicate="dwithin", distance=radius)[1])] = 1
        return result.tolist()

    city = project(shape(read(OUT / "taipei_boundary.geojson")["features"][0]["geometry"]))
    squares = np.array([project(box(ox + ix * sx, oy + iy * sy, ox + (ix + 1) * sx, oy + (iy + 1) * sy)).intersection(city)
                        for ix, iy in indices])
    population = read(POPULATION / "taipei_statistical_population_base.json")
    boundaries = read(POPULATION / "taipei_statistical_area_boundaries.geojson")
    counts = {r["base_area_id"]: r for r in population["records"]}
    areas, people = [], []
    for f in boundaries["features"]:
        r = counts.get(f["properties"]["base_area_id"])
        if r is not None and r["population"] is not None:
            areas.append(project(shape(f["geometry"])))
            people.append(r["population"])
    areas = np.array(areas)
    people = np.array(people)
    ci, ai = STRtree(areas).query(squares, predicate="intersects")
    intersections = shapely.area(shapely.intersection(squares[ci], areas[ai]))
    allocated = np.bincount(ci, weights=intersections / shapely.area(areas)[ai] * people[ai], minlength=len(squares))
    covered = np.bincount(ci, weights=intersections, minlength=len(squares))
    cell_areas = shapely.area(squares)
    shares = np.divide(covered, cell_areas, out=np.zeros_like(covered), where=cell_areas > 0)
    # Absent source coverage is null, including coastal/border slivers, never a fabricated zero.
    values = [round(float(v), 4) if s > .005 else None for v, s in zip(allocated, shares)]

    sources = {}
    for name in ("route_stop_sequences.json", "route_branches.json", "fixed_schedules.json", "headway_schedules.json", "route_shapes.geojson"):
        sources[f"data/taipei_feeds/{name}"] = read(FEEDS / name)["metadata"]
    for name in ("taipei_statistical_population_base.json", "taipei_statistical_area_boundaries.geojson"):
        sources[f"data/population/{name}"] = read(POPULATION / name)["metadata"]
    sources["data/taipei_feeds/stop_locations.geojson"] = read(FEEDS / "stop_locations.geojson")["metadata"]
    for name in ("taipei_boundary", "mrt_stations", "youbike_stations"):
        sources[f"web/public/data/{name}.geojson"] = read(OUT / f"{name}.geojson")["metadata"]
    sources["map-data/admin/cities.geojson"] = read(MAP / "admin/cities.geojson")["metadata"]
    sources["map-data/osm/manifest.json"] = read(MAP / "osm/manifest.json")["metadata"]
    sources["data/youbike/stations.geojson"] = read(ROOT / "data/youbike/stations.geojson")["metadata"]
    write("route_scenario.json", {
        "schema_version": "1.0", "dataset_id": "web_route_scenario",
        "metadata": {
            "built_at": datetime.now(timezone.utc).isoformat(),
            "source_data_time": sequences["metadata"]["source_data_time"],
            "sources": sources,
            "scope": "臺北市；公車僅臺北市公共運輸處 feed，保留其中跨市站序。",
            "grid": {"origin": grid["origin"], "step": grid["step"], "cell_m": 100},
            "population_period": population["metadata"].get("source_period"),
            "population_boundary_vintage": 2015,
            "city_geometry": "web taipei_boundary: admin_code=63000, simplify 0.0001 degree then round 5 decimals; population squares clipped to this boundary",
            "day_types": {"weekday": "ISO weekday 3", "saturday": "ISO weekday 6", "sunday": "ISO weekday 7"},
            "timezone": "Asia/Taipei",
            "schedule_method": "同一小時固定發車優先，其餘取兩個班距欄位的平均值換算；起站發車時刻，未加行駛到站時間。",
            "grain": "routes: subroute × direction; cells: disjoint ~100 m squares clipped to Taipei",
            "units": {"population": "persons", "hourly": "planned departures/hour", "stop_cells": "cell array indices within 350 m"},
            "missing_stop_occurrences": missing,
            "excluded_directions": excluded,
            "population_share": "每格裁至臺北市界後，有已匹配人口統計區的面積占比；三位小數。占比 <= 0.005 時 population 為 null。",
            "limitations": [
                "班表沒有去回程欄位；試算假設同一附線班表可套用各方向，非已驗證的方向班次。缺班表維持 null。",
                "同一附線與方向在同一格只計一次；不同附線分開計算。",
                "人口為 2024-12 戶籍人口，2015 邊界；面積比例分攤，均勻分布假設，不是搭乘人數。",
                "人口以互斥網格去重，格心距站 350 m；非步行路網。未提供可直接匹配的高齡或就學身分人口。",
                "捷運保留 350 m 可達範圍，無班次資料；YouBike 1 km 另列參考，不加入步行可達人口。",
                "無班表路線保留可能的可達範圍，受影響處標示不確定，不視為停駛。",
                "原線形只用精確附線與方向鍵，無精確線形時以站序連線；草圖不是可行駛導航。",
                "缺附線主檔及少於兩個不同站的方向排除，詳 excluded_directions；路網並非完整雙北供給。",
            ],
        },
        "records": {
            "routes": routes, "stops": stops, "stop_cells": stop_cells,
            "cells": {"ix": indices[:, 0].tolist(), "iy": indices[:, 1].tolist(), "population": values,
                      "population_share": [round(min(1, float(v)), 3) for v in shares],
                      "mrt": near(read(OUT / "mrt_stations.geojson")["features"], 350),
                      "youbike": near(read(OUT / "youbike_stations.geojson")["features"], 1000)},
        },
    })
    print(f"route editor: {len(routes)} directions, {len(stops)} stops, {len(values)} population cells")


if __name__ == "__main__":
    main()
