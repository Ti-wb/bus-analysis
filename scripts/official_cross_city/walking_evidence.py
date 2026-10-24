"""Conservative, reproducible pedestrian transfer evidence; no proximity-only edges."""
from __future__ import annotations

import argparse
import gzip
import heapq
import json
import math
import os
import tempfile
import time
from collections import Counter, defaultdict
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, build_opener

from pyproj import Transformer
from shapely import transform
from shapely.geometry import LineString, Point, Polygon, box, mapping

from .common import ROOT, OGDL, SafeRedirect, check_url, now, write_json

CATALOG = "https://data.taipei/dataset/detail?id=2b58f15a-dec6-4b9d-91be-4eaccfda5ae7"
OSM_LICENSE = {"name": "ODbL 1.0", "url": "https://www.openstreetmap.org/copyright",
               "attribution": "© OpenStreetMap contributors"}
AREAS = {
    "shilin": (25.089, 121.520, 25.098, 121.532),
    "guandu": (25.121, 121.462, 25.129, 121.472),
    "xinzhuang": (25.032, 121.448, 25.040, 121.457),
}
STATIONS = {"shilin": "士林", "guandu": "關渡", "xinzhuang": "新莊"}
# These are independently inspectable entrance identities, not automatically snapped points.
ENTRANCES = {"shilin": {"1": "3273605596", "2": "3273605595"},
             "guandu": {"1": "4330703774", "2": "4330703773"},
             "xinzhuang": {"1": "2480231501", "2": "2480231500"}}
KINDS = {"exits": "7", "sidewalks": "11", "painted_sidewalks": "12",
         "crossings": "13", "bus_stops": "18"}
PEDESTRIAN = {"footway", "pedestrian", "steps", "path"}
MOTOR_ROADS = {"motorway", "motorway_link", "trunk", "trunk_link", "primary",
               "primary_link", "secondary", "secondary_link", "tertiary",
               "tertiary_link", "residential", "unclassified", "service", "living_street"}
DENIED = {"no", "private", "customers", "destination", "delivery", "permit", "agricultural", "forestry"}
TO_METRIC = Transformer.from_crs(4326, 3826, always_xy=True)
TO_WGS84 = Transformer.from_crs(3826, 4326, always_xy=True)


def projected(geometry):
    return transform(geometry, TO_METRIC.transform, interleaved=False)


def geographic(geometry):
    return transform(geometry, TO_WGS84.transform, interleaved=False)


def query_for(area):
    bounds = ",".join(map(str, AREAS[area]))
    return (f'[out:json][timeout:45];(way[highway]({bounds});'
            f'node[railway=subway_entrance]({bounds});node[highway=bus_stop]({bounds});'
            f'node[barrier]({bounds});way[barrier]({bounds});relation["restriction:foot"]({bounds}););'
            '(._;>;);out meta;')


def specifications():
    result = []
    for name, kind in KINDS.items():
        result.append((f"wheelroute_{name}.json",
                       f"https://wheelroute.gov.taipei/wheelrouteApi/api/facility/Get/{kind}", CATALOG))
    for area in AREAS:
        result.append((f"osm_{area}.json", "https://overpass-api.de/api/interpreter?" +
                       urlencode({"data": query_for(area)}), "https://www.openstreetmap.org/copyright"))
    return result


def fetch_source(raw_dir, filename, url, page_url):
    hosts = {"wheelroute.gov.taipei", "overpass-api.de"}
    check_url(url, hosts)
    req = Request(url, headers={"User-Agent": "bus-analysis/0.1 public pedestrian validation",
                                "Accept-Encoding": "identity"})
    fd, temporary = tempfile.mkstemp(dir=raw_dir, suffix=".part")
    started = time.monotonic()
    try:
        with os.fdopen(fd, "wb") as out, build_opener(SafeRedirect(hosts)).open(req, timeout=60) as response:
            if response.status != 200:
                raise ValueError(f"Expected complete HTTP 200: {filename}")
            effective_url = check_url(response.geturl(), hosts)
            count = 0
            while chunk := response.read(1024 * 1024):
                count += len(chunk)
                if count > 35_000_000 or time.monotonic() - started > 180:
                    raise ValueError("Source exceeds walking acquisition budget")
                out.write(chunk)
            if not count or (response.headers.get("Content-Length") and
                             count != int(response.headers["Content-Length"])):
                raise ValueError("Empty or truncated walking source")
            receipt = {"url": url, "effective_url": effective_url, "page_url": page_url,
                       "downloaded_at": now(), "http_status": response.status, "bytes": count,
                       "content_type": response.headers.get("Content-Type"), "source_updated_at": None}
        # Never install an error/partial body as a successful source.
        parsed = json.loads(Path(temporary).read_text(encoding="utf-8-sig"))
        if isinstance(parsed, dict) and parsed.get("remark"):
            raise ValueError(f"Incomplete Overpass response: {parsed['remark']}")
        os.replace(temporary, raw_dir / filename)
        write_json(raw_dir / (filename + ".source.json"), receipt)
    finally:
        Path(temporary).unlink(missing_ok=True)


def load_sources(root, fetch=False):
    raw = root / "map-data/raw/walking"
    raw.mkdir(parents=True, exist_ok=True)
    result, receipts = {}, []
    for name, url, page in specifications():
        path = raw / name
        if fetch:
            fetch_source(raw, name, url, page)
            if name.startswith("osm_"):
                time.sleep(20)
        receipt = json.loads((raw / (name + ".source.json")).read_text())
        if (receipt.get("url") != url or receipt.get("bytes") != path.stat().st_size or
                receipt.get("http_status", receipt.get("status")) != 200 or not receipt.get("downloaded_at")):
            raise ValueError(f"Walking cache provenance mismatch: {name}")
        obj = json.loads(path.read_text(encoding="utf-8-sig"))
        if (name.startswith("wheelroute_") and not isinstance(obj, list)) or (
                name.startswith("osm_") and (not isinstance(obj.get("elements"), list) or obj.get("remark"))):
            raise ValueError(f"Incomplete walking source: {name}")
        result[name] = obj
        receipts.append({"source_id": name.removesuffix(".json"), "url": url, "page_url": page,
                         "raw_file": str(path.relative_to(root)), "bytes": path.stat().st_size,
                         "downloaded_at": receipt["downloaded_at"],
                         "source_updated_at": obj.get("osm3s", {}).get("timestamp_osm_base")
                         if isinstance(obj, dict) else None,
                         "record_count": len(obj["elements"]) if isinstance(obj, dict) else len(obj),
                         "license": OSM_LICENSE if name.startswith("osm_") else OGDL})
    return result, receipts


def coords(x, y):
    lon, lat = float(x), float(y)
    if not math.isfinite(lon + lat) or not (119 < lon < 123 and 21 < lat < 26):
        raise ValueError("Coordinates outside Taiwan or nonfinite")
    return lon, lat


def facilities(sources):
    polygons, features, diagnostics = [], [], Counter()
    extents = [box(w, s, e, n) for s, w, n, e in AREAS.values()]
    for name, kind in KINDS.items():
        for index, row in enumerate(sources[f"wheelroute_{name}.json"], 1):
            if row["kind"] != kind:
                raise ValueError("Facility source kind mismatch")
            sid = f"wheelroute:{kind}:{index}"
            diagnostics[f"{name}_total"] += 1
            try:
                if "location" in row:
                    v = row["location"].strip("|").split("|")
                    if len(v) % 2 or len(v) < 6:
                        raise ValueError("Invalid facility coordinate ring")
                    geometry = Polygon([coords(v[i], v[i + 1]) for i in range(0, len(v), 2)])
                else:
                    geometry = Point(coords(row["lon"], row["lat"]))
            except (ValueError, TypeError):
                diagnostics[f"{name}_invalid_coordinates"] += 1
                continue
            if not any(b.intersects(geometry) for b in extents):
                continue
            valid = geometry.is_valid and not geometry.is_empty
            diagnostics[f"{name}_selected"] += 1
            if not valid:
                diagnostics[f"{name}_invalid_selected"] += 1
            props = {"facility_record_id": sid, "source_record_sequence": index,
                     "facility_type": kind, "source_name": row["kname"],
                     "source_width": row.get("width"), "source_slope": row.get("slope"),
                     "source_updated_at": None, "geometry_valid": valid,
                     "usable_for_connector": valid and kind in {"11", "12", "13"}}
            features.append({"type": "Feature", "geometry": mapping(geometry), "properties": props})
            if props["usable_for_connector"]:
                polygons.append((sid, projected(geometry)))
    return polygons, features, dict(diagnostics)


def permission(tags, *, node=False):
    """Unknown constraints fail closed; motor-vehicle oneway never overrides foot."""
    if any(key in tags for key in ("foot:conditional", "access:conditional", "oneway:foot:conditional",
                                   "opening_hours", "construction", "proposed", "disused")):
        return "conditional_or_inactive"
    foot = tags.get("foot")
    if foot in DENIED or (foot is None and tags.get("access") in DENIED):
        return "access_restricted"
    if foot not in {None, "yes", "designated", "official"}:
        return "unhandled_foot_permission"
    if tags.get("access") not in {None, "yes", "designated", "permissive"} and foot is None:
        return "unhandled_access_permission"
    if tags.get("locked") == "yes":
        return "locked"
    if node and tags.get("barrier") not in {None, "kerb", "bollard", "entrance"} and foot not in {"yes", "designated"}:
        return "barrier_without_explicit_foot_permission"
    if not node and tags.get("highway") not in PEDESTRIAN and foot not in {"yes", "designated", "official"}:
        return "no_pedestrian_way_or_permission"
    if tags.get("area") == "yes":
        return "area_not_a_linear_path"
    if tags.get("oneway:foot") not in {None, "no", "yes", "1", "-1"}:
        return "unhandled_foot_direction"
    return None


def marked_crossing(tags):
    if (tags.get("crossing") in {"no", "unmarked"} or tags.get("crossing:markings") == "no"
            or tags.get("crossing:signals") not in {None, "yes", "no"}):
        return False
    return tags.get("highway") == "crossing" and (
        tags.get("crossing") in {"marked", "zebra", "traffic_signals"} or
        tags.get("crossing:markings") in {"yes", "zebra", "ladder", "continental"} or
        tags.get("crossing:signals") == "yes")


def same_grade(a, b):
    return (a.get("layer", "0"), a.get("level", "0")) == (b.get("layer", "0"), b.get("level", "0"))


def graph(source, polygons=(), *, candidate=False):
    elements = source["elements"]
    nodes = {str(x["id"]): {"node_id": str(x["id"]), "longitude": x["lon"], "latitude": x["lat"],
                           "tags": x.get("tags", {}), "source_updated_at": x.get("timestamp"),
                           "source_version": x.get("version")}
             for x in elements if x["type"] == "node"}
    points = {k: projected(Point(coords(v["longitude"], v["latitude"]))) for k, v in nodes.items()}
    ways = [x for x in elements if x["type"] == "way"]
    restricted = {str(m["ref"]) for x in elements if x["type"] == "relation"
                  for m in x.get("members", []) if m["type"] == "way"}
    roads, barriers = [], []
    for w in ways:
        if any(str(n) not in nodes for n in w["nodes"]):
            raise ValueError("OSM way lacks referenced node: incomplete topology")
        if w.get("tags", {}).get("highway") in MOTOR_ROADS:
            roads.append((w["tags"], LineString([points[str(n)] for n in w["nodes"]])))
        if w.get("tags", {}).get("barrier") and permission(w["tags"], node=True):
            barriers.append(LineString([points[str(n)] for n in w["nodes"]]))
    edges, rejected = [], Counter()
    for w in ways:
        tags, wid = w.get("tags", {}), str(w["id"])
        reason = permission(tags)
        if wid in restricted:
            reason = "pedestrian_restriction_relation_requires_review"
        if reason:
            rejected[reason] += 1
            continue
        for ordinal, (u, v) in enumerate(zip(w["nodes"], w["nodes"][1:]), 1):
            u, v = str(u), str(v)
            if permission(nodes[u]["tags"], node=True) or permission(nodes[v]["tags"], node=True):
                rejected["node_permission"] += 1
                continue
            line = LineString([points[u], points[v]])
            if any(line.intersects(barrier) for barrier in barriers):
                rejected["linear_barrier_intersection"] += 1
                continue
            if (not candidate and any(tags.get(k) not in {None, "no"} for k in ("bridge", "tunnel", "indoor"))
                    and "layer" not in tags and "level" not in tags):
                rejected["unknown_grade"] += 1
                continue
            crossing_nodes = [n for n in (u, v) if marked_crossing(nodes[n]["tags"])]
            crossing_evidence = []
            if not candidate:
                # A planar intersection is not automatically a legal crossing.
                unverified = False
                for rt, road in roads:
                    if not same_grade(tags, rt) or not line.intersects(road):
                        continue
                    intersection = line.intersection(road)
                    marked_nodes = [n for n in crossing_nodes if points[n].buffer(.01).covers(intersection)]
                    ids = [sid for sid, polygon in polygons if sid.startswith("wheelroute:13:") and polygon.covers(intersection)]
                    if marked_nodes or (tags.get("footway") == "crossing" and ids):
                        crossing_evidence.append({"crossing_node_ids": marked_nodes, "official_crossing_facility_ids": ids})
                        continue
                    unverified = True
                if unverified:
                    rejected["unmarked_carriageway_intersection"] += 1
                    continue
            direction = tags.get("oneway:foot")
            edges.append({"edge_id": f"{wid}:{ordinal}", "way_id": wid, "from_node_id": u,
                          "to_node_id": v, "length_m": line.length,
                          "forward_allowed": direction != "-1", "reverse_allowed": direction not in {"yes", "1"},
                          "tags": tags, "crossing_node_ids": crossing_nodes,
                          "crossing_evidence": crossing_evidence,
                          "source_updated_at": w.get("timestamp"), "source_version": w.get("version"),
                          "permission_basis": "explicit_foot_permission" if tags.get("foot") else "mapped_pedestrian_way",
                          "geometry": mapping(geographic(line))})
    return nodes, points, edges, roads, dict(rejected), barriers


def connector(point, edge, points, polygons, roads, barriers=()):
    """A short line is eligible only when its whole geometry lies in an official facility."""
    line = LineString([points[edge["from_node_id"]], points[edge["to_node_id"]]])
    target = line.interpolate(line.project(point))
    length = point.distance(target)
    if length > 20:
        return None
    link = LineString([point, target])
    covered = [sid for sid, polygon in polygons if polygon.covers(link)]
    if not covered or any(link.intersects(g) for rt, g in roads if same_grade(edge["tags"], rt)):
        return None
    if any(link.intersects(barrier) for barrier in barriers):
        return None
    tags = edge["tags"]
    if tags.get("layer", "0") != "0" or tags.get("level", "0") != "0":
        return None
    if (any(tags.get(k) not in {None, "no"} for k in ("bridge", "tunnel", "indoor", "covered"))
            and tags.get("level") != "0"):
        return None
    return {"geometry": mapping(geographic(link)), "length_m": length,
            "official_facility_record_ids": covered, "edge_id": edge["edge_id"], "target": target}


def shortest_path(start, points, edges, candidates):
    adjacency = defaultdict(list)
    for e in edges:
        u, v, eid, length = e["from_node_id"], e["to_node_id"], e["edge_id"], e["length_m"]
        if e["forward_allowed"]:
            adjacency[u].append((v, length, eid))
        if e["reverse_allowed"]:
            adjacency[v].append((u, length, eid))
    edge_map = {e["edge_id"]: e for e in edges}
    for index, c in enumerate(candidates):
        e = edge_map[c["edge_id"]]
        target = f"target:{index}"
        for n, allowed in [(e["from_node_id"], e["forward_allowed"]),
                           (e["to_node_id"], e["reverse_allowed"])]:
            if allowed:
                adjacency[n].append((target, points[n].distance(c["target"]) + c["length_m"], e["edge_id"]))
    queue, previous, distance = [(0, start)], {}, {start: 0}
    while queue:
        length, u = heapq.heappop(queue)
        if length != distance[u]:
            continue
        if u.startswith("target:"):
            path, current = [], u
            while current != start:
                parent, eid = previous[current]
                path.append({"from_node_id": parent, "to_node_id": current, "edge_id": eid})
                current = parent
            return length, list(reversed(path)), candidates[int(u.split(":")[1])]
        for v, weight, eid in adjacency[u]:
            proposed = length + weight
            if proposed < distance.get(v, math.inf):
                distance[v], previous[v] = proposed, (u, eid)
                heapq.heappush(queue, (proposed, v))
    return None


def envelope(dataset_id, records, **metadata):
    return {"schema_version": 1, "dataset_id": dataset_id,
            "metadata": {"source_updated_at": None, "generated_at": now(), "record_count": len(records), **metadata},
            "records": records}


def build(root, fetch=False):
    sources, receipts = load_sources(root, fetch)
    polygons, facility_features, diagnostics = facilities(sources)
    normalized_exits = sources["wheelroute_exits.json"]
    stop_source = json.loads((root / "data/taipei_feeds/stops.geojson").read_text())
    records, graph_records, path_features, candidate_features = [], [], [], []
    for area, station in STATIONS.items():
        source = sources[f"osm_{area}.json"]
        nodes, points, edges, roads, rejected, barriers = graph(source, polygons)
        _, _, candidate_edges, _, _, _ = graph(source, polygons, candidate=True)
        graph_records.append({"area_id": area, "station_name": station, "nodes": list(nodes.values()),
                              "edges": edges, "candidate_edges": candidate_edges,
                              "candidate_edge_policy": "OSM permission/topology only; carriageway crossings still require verification; never accepted as legal transfers",
                              "rejected_counts": rejected,
                              "source_data_time": source.get("osm3s", {}).get("timestamp_osm_base"),
                              "restriction_relation_count": sum(x["type"] == "relation" for x in source["elements"])})
        groups = defaultdict(list)
        for feature in stop_source["features"]:
            p = feature["properties"]
            if p["name_zh"].startswith(f"捷運{station}站"):
                groups[p["stop_location_id"]].append(p)
        for exit_number, nid in ENTRANCES[area].items():
            name = f"{station}站出口{exit_number}-{exit_number}"
            official = [x for x in normalized_exits if x["kname"] == name]
            if len(official) != 1 or nid not in nodes:
                raise ValueError(f"Missing or ambiguous entrance identity: {name}")
            exit_record = official[0]
            entrance = nodes[nid]
            d = points[nid].distance(projected(Point(coords(exit_record["lon"], exit_record["lat"]))))
            entrance_ok = (d <= 20 and entrance["tags"].get("railway") == "subway_entrance" and
                           (entrance["tags"].get("ref") == exit_number or
                            entrance["tags"].get("name", "").startswith(f"出口{exit_number} ")))
            for loc, stops in sorted(groups.items()):
                positions = {(s["longitude"], s["latitude"]) for s in stops}
                boarding = [s for s in stops if s["boarding_alighting_code"] in {"0", "1"}]
                row = {"transfer_id": f"{area}:exit:{exit_number}:stop_location:{loc}",
                       "station_name": station, "exit_number": exit_number, "osm_entrance_node_id": nid,
                       "official_exit_name": name, "entrance_identity_distance_m": round(d, 3),
                       "entrance_identity_basis": "official station/exit designation, OSM entrance ref/name, coordinate consistency within 20 m; no connector is fabricated between these alternative representations",
                       "stop_location_id": loc, "bus_stop_name": stops[0]["name_zh"],
                       "bus_stop_address": stops[0]["address"],
                       "stop_coordinates": list(next(iter(positions))) if len(positions) == 1 else None,
                       "boarding_eligible_records": [{k: s[k] for k in ("stop_id", "route_id", "stop_sequence", "direction_code", "boarding_alighting_code")} for s in boarding],
                       "unknown_boarding_record_count": sum(s["boarding_alighting_code"] is None for s in stops),
                       "alighting_only_record_count": sum(s["boarding_alighting_code"] == "-1" for s in stops),
                       "status": "unverified", "walking_distance_m": None, "path_edge_ids": [],
                       "legal_use_conditions": ["遵守現場交通指揮與行人號誌；靜態來源不證明即時綠燈或臨時施工狀態。"],
                       "source_files": ["data/taipei_feeds/stops.geojson", f"map-data/raw/walking/osm_{area}.json",
                                        "map-data/raw/walking/wheelroute_exits.json"]}
                if not entrance_ok:
                    row["reason"] = "entrance_identity_not_verified"
                elif len(positions) != 1:
                    row["reason"] = "official_stop_position_conflict"
                elif not boarding:
                    row["reason"] = "no_explicit_boarding_permission_in_delivered_route_records"
                else:
                    point = projected(Point(next(iter(positions))))
                    candidates = [c for e in edges if (c := connector(point, e, points, polygons, roads, barriers))]
                    result = shortest_path(nid, points, edges, candidates)
                    row["verified_connector_candidate_count"] = len(candidates)
                    if result:
                        distance, path, link = result
                        row.update({"status": "mapped_pedestrian_path_verified", "reason": None,
                                    "walking_distance_m": round(distance, 3),
                                    "path_steps": path, "path_edge_ids": [p["edge_id"] for p in path],
                                    "connector": {k: v for k, v in link.items() if k != "target"},
                                    "legal_evidence_scope": "原始 OSM 明示行人線形、權限／障礙／方向／過街檢查及官方人行設施內的末端連接；非現場通行保證或全市路網驗收。"})
                        geometry_points = [points[nid]]
                        for step in path[:-1]:
                            geometry_points.append(points[step["to_node_id"]])
                        geometry_points += [link["target"], point]
                        path_features.append({"type": "Feature", "geometry": mapping(geographic(LineString(geometry_points))),
                                              "properties": {"transfer_id": row["transfer_id"], "walking_distance_m": row["walking_distance_m"],
                                                             "status": row["status"]}})
                    else:
                        row["reason"] = "no_official_facility_connector" if not candidates else "pedestrian_topology_disconnected"
                        candidate_links = [c for e in candidate_edges if (c := connector(point, e, points, polygons, roads, barriers))]
                        candidate_path = shortest_path(nid, points, candidate_edges, candidate_links)
                        if candidate_path:
                            length, steps, link = candidate_path
                            strict_ids = {e["edge_id"] for e in edges}
                            row.update({"candidate_distance_m": round(length, 3),
                                        "candidate_path_steps": steps,
                                        "candidate_connector": {k: v for k, v in link.items() if k != "target"},
                                        "candidate_unverified_crossing_edge_ids": [s["edge_id"] for s in steps if s["edge_id"] not in strict_ids],
                                        "candidate_status": "requires_carriageway_crossing_review_not_a_legal_transfer"})
                            geometry_points = [points[nid]] + [points[s["to_node_id"]] for s in steps[:-1]]
                            geometry_points += [link["target"], point]
                            candidate_features.append({"type": "Feature", "geometry": mapping(geographic(LineString(geometry_points))),
                                "properties": {"transfer_id": row["transfer_id"], "candidate_distance_m": row["candidate_distance_m"],
                                "legal_transfer_verified": False, "status": row["candidate_status"],
                                "unverified_crossing_edge_ids": row["candidate_unverified_crossing_edge_ids"]}})
                records.append(row)
    out = root / "data/official_cross_city/walking_transfers"
    geo = root / "map-data/walking"
    out.mkdir(parents=True, exist_ok=True)
    geo.mkdir(parents=True, exist_ok=True)
    write_json(out / "transfers.json", envelope("official_pedestrian_transfer_evidence", records,
               status_counts=dict(Counter(x["status"] for x in records)), scope="新莊、關渡、士林兩出口與直接 feed 明示捷運同站名站位的有限驗收；未代表全市或所有路線。",
               requires_source_review=any(r["status"] != "mapped_pedestrian_path_verified" for r in records),
               source_manifest="map-data/walking/source_manifest.json", license=OSM_LICENSE,
               independent_review_status="pending_separate_agent"))
    graph_obj = envelope("osm_pedestrian_transfer_graphs", graph_records, license=OSM_LICENSE,
                         crs="EPSG:4326", coordinate_order=["longitude", "latitude"],
                         requires_source_review=True, scope_status="bounded_graphs_include_unverified_candidate_edges",
                         topology="Only identical OSM node IDs connect; intersections and proximity never create graph edges.")
    graph_payload = json.dumps(graph_obj, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode()
    graph_part = out / "graphs.json.gz.part"
    graph_part.write_bytes(gzip.compress(graph_payload, mtime=0))
    graph_part.replace(out / "graphs.json.gz")
    write_json(geo / "facilities.geojson", {"type": "FeatureCollection", "metadata": {
        "dataset_id": "wheelroute_pedestrian_transfer_facilities", "source_page": CATALOG,
        "source_updated_at": None, "license": OGDL, "feature_count": len(facility_features),
        "crs": "EPSG:4326", "coordinate_order": ["longitude", "latitude"],
        "crs_evidence": "Source lon/lat and polygon coordinate order agree with the official site's Google Maps LatLng rendering; datum inferred, not explicitly declared in catalog.",
        "crs_status": "inferred_from_official_rendering", "diagnostics": diagnostics,
        "requires_source_review": True,
        "field_units": {"source_width": "cm (catalog declaration)", "source_slope": "% (catalog declaration)"},
        "source_unit_status": "catalog_declared_values_not_converted_or_used_for_accessibility",
        "width_slope_policy": "Original values preserved; negative sentinel values are not treated as physical width/slope or accessibility proof."}, "features": facility_features})
    write_json(geo / "paths.geojson", {"type": "FeatureCollection", "metadata": {
        "dataset_id": "verified_mapped_pedestrian_transfer_paths", "feature_count": len(path_features),
        "source_updated_at": None, "crs": "EPSG:4326", "coordinate_order": ["longitude", "latitude"],
        "license": OSM_LICENSE, "limitations": "靜態來源驗收；須遵守現場號誌、封閉及施工管制。"}, "features": path_features})
    write_json(geo / "candidate_paths.geojson", {"type": "FeatureCollection", "metadata": {
        "dataset_id": "unverified_pedestrian_transfer_candidate_paths", "feature_count": len(candidate_features),
        "requires_source_review": True,
        "source_updated_at": None, "crs": "EPSG:4326", "coordinate_order": ["longitude", "latitude"],
        "license": OSM_LICENSE, "limitations": "未通過合法過街證據驗收，只供逐段查核；不可作為可用步行轉乘邊。"}, "features": candidate_features})
    write_json(geo / "source_manifest.json", envelope("walking_transfer_sources", receipts,
               official_facility_diagnostics=diagnostics,
               legal_crossing_source="https://law.moj.gov.tw/LawClass/LawSingle.aspx?pcode=K0040013&flno=134"))
    return Counter(x["status"] for x in records)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--fetch", action="store_true", help="Refresh bounded public sources; default rebuilds verified local raw cache")
    args = parser.parse_args()
    print(json.dumps(dict(build(args.root, args.fetch)), ensure_ascii=False))


if __name__ == "__main__":
    main()
