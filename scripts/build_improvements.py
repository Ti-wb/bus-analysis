#!/usr/bin/env python3
"""Build page 07's real-data inputs; synthetic demand stays explicitly in the UI.

Run after build_web_data.py. Owns only web/public/data/improvements.json.
"""
from collections import defaultdict
from datetime import datetime, timezone

import numpy as np
import shapely
from pyproj import Transformer
from shapely import STRtree
from shapely.geometry import LineString, Point, box, shape

from build_web_data import FEEDS, MAP, OUT, ROOT, read, subroute_departures, write

DAYS = ("weekday", "saturday", "sunday")


def main():
    scenario = read(OUT / "route_scenario.json")
    variants = scenario["records"]["routes"]
    stops = scenario["records"]["stops"]
    departures = subroute_departures()
    tm = Transformer.from_crs(4326, 3826, always_xy=True)
    ll = Transformer.from_crs(3826, 4326, always_xy=True)

    def project(g):
        return shapely.transform(g, lambda xy: np.column_stack(tm.transform(xy[:, 0], xy[:, 1])))

    districts_doc = read(MAP / "admin/districts.geojson")
    districts = districts_doc["features"]
    district_tree = STRtree([shape(f["geometry"]) for f in districts])
    metro_doc = read(OUT / "mrt_stations.geojson")
    metro = metro_doc["features"]
    metro_tree = STRtree([project(shape(f["geometry"])) for f in metro])
    rail = shapely.union_all([project(shape(f["geometry"])) for f in read(OUT / "mrt_lines.geojson")["features"]]).buffer(100)
    toilets_doc = read(ROOT / "data/urban/public_toilets.geojson")
    toilets = [f for f in toilets_doc["features"] if f.get("geometry")]
    toilet_tree = STRtree([project(shape(f["geometry"])) for f in toilets])
    stop_routes, stop_subs, stop_variants, route_groups = (defaultdict(set) for _ in range(4))
    terminal_variants = defaultdict(set)
    by_variant = {r["id"]: r for r in variants}
    for r in variants:
        route_groups[r["route_id"]].add(r["id"])
        for sid in set(r["stop_ids"]):
            stop_routes[sid].add(r["route_id"])
            stop_subs[sid].add(r["subroute_id"])
            stop_variants[sid].add(r["id"])
        for sid in {r["stop_ids"][0], r["stop_ids"][-1]}:
            terminal_variants[sid].add(r["id"])

    def timetable(subs):
        hourly, missing = {}, {}
        for day in DAYS:
            known = [departures[(sub, day)] for sub in sorted(subs) if (sub, day) in departures]
            hourly[day] = [round(sum(row[h] for row in known), 4) for h in range(24)] if known else None
            missing[day] = len(subs) - len(known)
        return {"hourly": hourly, "missing_schedules": missing}

    def nearest(point, tree, features):
        if not len(features):
            return None
        idx = int(tree.nearest(point))
        return {"name": features[idx]["properties"]["name"], "distance_m": round(point.distance(tree.geometries[idx]), 1)}

    points, base_stops = {}, {}
    for sid, s in stops.items():
        p = Point(s["at"])
        point = project(p)
        points[sid] = point
        # Names are display-only. Spatial containment, not name matching, identifies districts.
        ds = [districts[int(i)]["properties"] for i in district_tree.query(p, predicate="intersects")]
        base_stops[sid] = {
            "id": f"stop:{sid}", "source_id": sid, "kind": "stops", "name": s["name"], "at": s["at"],
            "districts": sorted({f'{d["city_name"]} {d["district_name"]}' for d in ds}),
            "route_ids": sorted(stop_routes[sid]), "stop_ids": [sid],
            "variant_id": next(iter(sorted(stop_variants[sid])), None),
            "shared_routes": len(stop_routes[sid]), "geometry_overlap": None,
            "nearest_mrt": nearest(point, metro_tree, metro),
            "nearest_toilet": nearest(point, toilet_tree, toilets) if any(d["city_code"] == "63000" for d in ds) else None,
            "is_mock": False, **timetable(stop_subs[sid]),
        }
        base_stops[sid]["station_overlap"] = 100 if base_stops[sid]["nearest_mrt"] and base_stops[sid]["nearest_mrt"]["distance_m"] <= 300 else 0

    records = []
    for rid, ids in sorted(route_groups.items()):
        rs = [by_variant[i] for i in sorted(ids)]
        representative = min(rs, key=lambda r: (r["subroute_id"] != rid, r["direction"] != "0", -len(r["stop_ids"]), r["id"]))
        sids = sorted({s for r in rs for s in r["stop_ids"]})
        paths = [project(LineString(r["path"])) for r in rs if r["path"] and len(r["path"]) >= 2]
        geometry = shapely.union_all(paths) if paths else None
        length = geometry.length if geometry is not None else 0
        first, last = stops[representative["stop_ids"][0]], stops[representative["stop_ids"][-1]]
        records.append({
            "id": f"route:{rid}", "source_id": rid, "kind": "routes", "name": representative["name"],
            "subtitle": f'{first["name"]} ↔ {last["name"]}', "at": first["at"],
            "districts": sorted({d for sid in sids for d in base_stops[sid]["districts"]}),
            "route_ids": [rid], "stop_ids": sids, "variant_id": representative["id"],
            "variant_ids": sorted(ids), "profile_stops": representative["stop_ids"],
            "profile_direction": representative["direction_label"],
            "path": representative["path"] or [stops[s]["at"] for s in representative["stop_ids"]],
            "path_is_stop_connection": representative["path"] is None,
            "geometry_overlap": round(geometry.intersection(rail).length / length * 100, 2) if length else None,
            "geometry_directions": len(paths), "total_directions": len(rs),
            "station_overlap": round(sum(base_stops[s]["station_overlap"] == 100 for s in sids) / len(sids) * 100, 2),
            "shared_routes": max(len(stop_routes[s]) for s in sids),
            "nearest_mrt": None, "nearest_toilet": None, "is_mock": False,
            **timetable({r["subroute_id"] for r in rs}),
        })

    records.extend(base_stops[s] for s in sorted(base_stops) if stop_routes[s])
    for sid, ids in sorted(terminal_variants.items()):
        rs = [by_variant[i] for i in sorted(ids)]
        records.append({**base_stops[sid], "id": f"terminal:{sid}", "kind": "terminals",
                        "route_ids": sorted({r["route_id"] for r in rs}), "variant_id": rs[0]["id"],
                        "terminal_directions": len(rs), "shared_routes": len({r["route_id"] for r in rs}),
                        **timetable({r["subroute_id"] for r in rs})})

    # 1 km TM2 blocks aggregate existing 100 m population cells by their centres.
    # These are geographic browsing units; DRTS qualification is explicitly synthetic.
    blocks = defaultdict(list)
    for sid, point in points.items():
        if stop_routes[sid] and any(d.startswith("臺北市 ") for d in base_stops[sid]["districts"]):
            blocks[(int(point.x // 1000), int(point.y // 1000))].append(sid)
    population, missing_cells = defaultdict(float), defaultdict(int)
    c = scenario["records"]["cells"]
    ox, oy = scenario["metadata"]["grid"]["origin"]
    sx, sy = scenario["metadata"]["grid"]["step"]
    for ix, iy, value in zip(c["ix"], c["iy"], c["population"]):
        x, y = tm.transform(ox + (ix + .5) * sx, oy + (iy + .5) * sy)
        key = (int(x // 1000), int(y // 1000))
        if value is None:
            missing_cells[key] += 1
        else:
            population[key] += value
    for (x, y), sids in sorted(blocks.items()):
        at = list(ll.transform(x * 1000 + 500, y * 1000 + 500))
        centre = Point(x * 1000 + 500, y * 1000 + 500)
        anchor = min(sids, key=lambda s: centre.distance(points[s]))
        subs = {sub for sid in sids for sub in stop_subs[sid]}
        route_ids = sorted({r for sid in sids for r in stop_routes[sid]})
        bounds = [list(ll.transform(x * 1000, y * 1000)), list(ll.transform((x + 1) * 1000, (y + 1) * 1000))]
        records.append({
            "id": f"drts:{x}:{y}", "source_id": f"{x}:{y}", "kind": "drts", "name": f'{stops[anchor]["name"]}周邊',
            "subtitle": "1 km 候選區塊", "at": at, "bounds": bounds,
            "districts": sorted({d for s in sids for d in base_stops[s]["districts"]}),
            "route_ids": route_ids, "stop_ids": sorted(sids), "variant_id": base_stops[anchor]["variant_id"],
            "shared_routes": max(len(stop_routes[s]) for s in sids),
            "station_overlap": round(sum(base_stops[s]["station_overlap"] == 100 for s in sids) / len(sids) * 100, 2),
            "geometry_overlap": None, "nearest_mrt": nearest(centre, metro_tree, metro), "nearest_toilet": None,
            "population": round(population[(x, y)], 1) if (x, y) in population else None,
            "population_missing_cells": missing_cells[(x, y)], "is_mock": False, **timetable(subs),
        })

    sources = dict(scenario["metadata"]["sources"])
    sources["map-data/admin/districts.geojson"] = districts_doc["metadata"]
    sources["data/urban/public_toilets.geojson"] = toilets_doc["metadata"]
    write("improvements.json", {
        "schema_version": "1.0", "dataset_id": "web_improvements", "records": records,
        "metadata": {
            "built_at": datetime.now(timezone.utc).isoformat(), "source_data_time": scenario["metadata"]["source_data_time"],
            "sources": sources, "source_exclusions": scenario["metadata"].get("excluded_directions"),
            "day_types": {"weekday": "representative ISO weekday 3", "saturday": "ISO weekday 6", "sunday": "ISO weekday 7"},
            "timezone": "Asia/Taipei",
            "schedule_method": "Fixed departures win per subroute/day/hour; average of the two valid source headway fields fills remaining hours. Source schedules are not direction-specific.",
            "metro_layers": {name: read(OUT / f"{name}.geojson")["metadata"] for name in ("mrt_lines", "mrt_stations")},
            "population_method": {"period": {"start": "2024-12", "end": "2024-12"}, "boundary_vintage": 2015,
                                  "allocation": "100 m cells area-allocated in route_scenario, assigned to 1 km blocks by cell centre; null cells counted separately.",
                                  "upstream": scenario["metadata"]},
            "grain": "one main route, physical stop, native stop-sequence endpoint or 1 km TM2 block",
            "units": {"geometry_overlap": "% length", "station_overlap": "% distinct physical stops", "hourly": "planned departures/hour", "distance_m": "m", "population": "persons, area allocated"},
            "definitions": {
                "geometry_overlap": "Length of union of available exact subroute-direction source shapes within a 100 m OSM metro-line buffer / total available union length, EPSG:3826. Missing shapes excluded, not replaced with straight stop connections.",
                "station_overlap": "Distinct physical stops within 300 m straight-line distance of an OSM metro station centre / all distinct route stops; not exits or walking distance.",
                "shared_routes": "Maximum distinct main route IDs at a physical stop, including the selected route. No direction or branch double counting.",
                "hourly": "Sum of distinct subroutes, no direction double counting; source-start-hour planned departures. All-missing remains null; missing_schedules reports partial totals.",
                "terminals": "First or last physical stop of a native branch/direction sequence; not a verified depot or driver rest area.",
                "drts": "1 km EPSG:3826 squares containing Taipei stops; existing 100 m population cells assigned by centre, population period 2024-12 and statistical boundaries 2015; not H3/Gi* or actual low ridership zones.",
            },
            "mock_fields_generated_in_ui": ["trip_overlap", "passengers_per_trip", "cold_share", "cold_period", "uninterrupted_minutes", "rest_minutes", "load_profile", "affected_trips", "released_vehicle_hours", "population_change", "priority_score", "problems", "recommendation"],
            "limitations": [
                "Only Taipei authority bus feeds; cross-city routes retained but New Taipei-exclusive services absent.",
                "OSM is a snapshot and station centres differ from official entrances. Geometry overlap does not prove substitutable trips.",
                "All synthetic fields use a deterministic demonstration model, not annual ridership disaggregation, fare-card OD, Gi* analysis or actual driver schedules.",
                "Public toilets are Taipei-only; nearest points do not establish opening hours, parking or driver access. Outside Taipei returns null.",
                "Priority and recommendations combine real supply metrics with explicitly marked Mock Data; no operational decision or saved route edit is applied automatically.",
            ],
            "counts": {kind: sum(r["kind"] == kind for r in records) for kind in ("routes", "stops", "drts", "terminals")},
        },
    })


if __name__ == "__main__":
    main()
