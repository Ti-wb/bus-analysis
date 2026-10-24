"""Independent retained-source pedestrian review. Does not import the producer."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime
import gzip
import heapq
import json
import math
from pathlib import Path
import re
import subprocess
import sys
from urllib.parse import parse_qs, urlsplit

from pyproj import Transformer
from shapely import transform
from shapely.geometry import LineString, Point, Polygon, box, shape
from shapely.strtree import STRtree

ROOT = Path(__file__).resolve().parents[1]
AREAS = {"shilin": (25.089, 121.520, 25.098, 121.532), "guandu": (25.121, 121.462, 25.129, 121.472),
         "xinzhuang": (25.032, 121.448, 25.040, 121.457)}
STATIONS = {"shilin": "士林", "guandu": "關渡", "xinzhuang": "新莊"}
FACILITIES = {"exits": "7", "sidewalks": "11", "painted_sidewalks": "12", "crossings": "13", "bus_stops": "18"}
MOTOR = {"motorway", "motorway_link", "trunk", "trunk_link", "primary", "primary_link", "secondary", "secondary_link",
         "tertiary", "tertiary_link", "residential", "unclassified", "service", "living_street"}
DENIED = {"no", "private", "customers", "destination", "delivery", "permit", "agricultural", "forestry"}
PROJECT = Transformer.from_crs("EPSG:4326", "EPSG:3826", always_xy=True).transform


def read(path):
    body = path.read_bytes()
    return json.loads(gzip.decompress(body) if path.suffix == ".gz" else body)


def require(value, message):
    if not value:
        raise ValueError(message)


def metric(geometry):
    return transform(geometry, PROJECT, interleaved=False)


def near(a, b, tolerance=1e-5):
    return abs(a - b) <= tolerance


def permission(tags, node=False):
    conditional = {"foot:conditional", "access:conditional", "oneway:foot:conditional", "opening_hours", "construction", "proposed", "disused"}
    foot = tags.get("foot")
    return not (conditional.intersection(tags) or foot in DENIED or foot not in {None, "yes", "designated", "official"}
                or (foot is None and tags.get("access") not in {None, "yes", "designated", "permissive"})
                or tags.get("locked") == "yes" or tags.get("area") == "yes"
                or tags.get("oneway:foot") not in {None, "no", "yes", "1", "-1"}
                or (node and tags.get("barrier") not in {None, "kerb", "bollard", "entrance"} and foot not in {"yes", "designated"})
                or (not node and tags.get("highway") not in {"footway", "pedestrian", "steps", "path"} and foot not in {"yes", "designated", "official"}))


def marked(tags):
    return (tags.get("highway") == "crossing" and tags.get("crossing") not in {"no", "unmarked"}
            and tags.get("crossing:markings") != "no"
            and tags.get("crossing:signals") in {None, "yes", "no"}
            and (tags.get("crossing") in {"marked", "zebra", "traffic_signals"}
                 or tags.get("crossing:markings") in {"yes", "zebra", "ladder", "continental"}
                 or tags.get("crossing:signals") == "yes"))


def grade(tags):
    return tags.get("layer", "0"), tags.get("level", "0")


def spatial(items):
    return STRtree([item[-1] for item in items]) if items else None


def intersecting(items, tree, geometry):
    return [items[int(index)] for index in tree.query(geometry, predicate="intersects")] if tree is not None else []


def check_sources(root, manifest):
    sources, counts = {}, {}
    require(len(manifest["records"]) == 8, "Expected five official facility and three OSM sources")
    for row in manifest["records"]:
        path = root / row["raw_file"]
        raw = read(path)
        receipt = read(path.with_name(path.name + ".source.json"))
        require(path.stat().st_size == row["bytes"] == receipt["bytes"], "Raw byte count: " + row["source_id"])
        require(receipt.get("http_status", receipt.get("status")) == 200 and receipt["url"] == row["url"]
                and receipt["downloaded_at"] == row["downloaded_at"], "Acquisition receipt: " + row["source_id"])
        if row["source_id"].startswith("osm_"):
            require(isinstance(raw.get("elements"), list) and not raw.get("remark"), "Incomplete OSM response")
            query = parse_qs(urlsplit(row["url"]).query)["data"][0]
            area = row["source_id"].removeprefix("osm_")
            require(",".join(map(str, AREAS[area])) in query and all(term in query for term in
                    ['way[highway]', 'node[barrier]', 'way[barrier]', 'relation["restriction:foot"]', '(._;>;)', 'out meta']), "OSM query topology/constraint scope")
            require(row["source_updated_at"] == raw["osm3s"]["timestamp_osm_base"], "OSM snapshot time")
            require(row["license"]["url"] == "https://www.openstreetmap.org/copyright", "OSM attribution")
            count = len(raw["elements"])
        else:
            require(isinstance(raw, list) and row["source_updated_at"] is None, "Official source time is unknown")
            require(row["license"]["url"] == "https://data.gov.tw/license", "Official license")
            count = len(raw)
        require(count == row["record_count"], "Source record count")
        sources[row["source_id"]] = raw
        counts[row["source_id"]] = count
    require(set(sources) == {"wheelroute_" + key for key in FACILITIES} | {"osm_" + key for key in AREAS}, "Source identity population")
    return sources, counts


def check_facilities(sources, delivered):
    expected, invalid, totals, selected = {}, Counter(), {}, {}
    bounds = [box(w, s, e, n) for s, w, n, e in AREAS.values()]
    for name, kind in FACILITIES.items():
        rows = sources["wheelroute_" + name]
        totals[name] = len(rows)
        selected[name] = 0
        for index, row in enumerate(rows, 1):
            require(row["kind"] == kind, "Facility kind")
            try:
                if "location" in row:
                    values = [float(x) for x in row["location"].strip("|").split("|")]
                    require(len(values) >= 6 and len(values) % 2 == 0, "Polygon coordinate pairs")
                    pairs = list(zip(values[::2], values[1::2]))
                    geom = Polygon(pairs)
                else:
                    pairs = [(float(row["lon"]), float(row["lat"]))]
                    geom = Point(pairs[0])
                require(all(math.isfinite(x) and math.isfinite(y) and 119 < x < 123 and 21 < y < 26 for x, y in pairs), "Facility coordinates")
            except (ValueError, TypeError):
                invalid[name] += 1
                continue
            if not any(extent.intersects(geom) for extent in bounds):
                continue
            key = f"wheelroute:{kind}:{index}"
            expected[key] = (geom, row, index)
            selected[name] += 1
    actual = {row["properties"]["facility_record_id"]: row for row in delivered["features"]}
    require(len(actual) == len(delivered["features"]) and set(actual) == set(expected), "Selected facility population")
    polygons = []
    for key, (geom, row, index) in expected.items():
        feature, p = actual[key], actual[key]["properties"]
        require(shape(feature["geometry"]).equals_exact(geom, 1e-12), "Facility raw geometry: " + key)
        require(p["source_name"] == row["kname"] and p["source_width"] == row.get("width") and p["source_slope"] == row.get("slope")
                and p["facility_type"] == row["kind"] and type(p["source_record_sequence"]) is int and p["source_record_sequence"] == index
                and p["source_updated_at"] is None and p["geometry_valid"] == (geom.is_valid and not geom.is_empty), "Facility fields: " + key)
        usable = geom.is_valid and not geom.is_empty and row["kind"] in {"11", "12", "13"}
        require(p["usable_for_connector"] == usable, "Invalid facility cannot create connector")
        if usable:
            polygons.append((key, metric(geom)))
    require(delivered["metadata"]["feature_count"] == len(actual) and delivered["metadata"]["crs_status"] == "inferred_from_official_rendering", "Facility count and CRS inference")
    return polygons, {"source_counts": totals, "selected_counts": selected, "invalid_coordinates": dict(invalid), "selected_feature_count": len(actual)}


def check_graph(area, raw, delivered, polygons):
    raw_nodes = {str(row["id"]): row for row in raw["elements"] if row["type"] == "node"}
    raw_ways = {str(row["id"]): row for row in raw["elements"] if row["type"] == "way"}
    actual_nodes = {row["node_id"]: row for row in delivered["nodes"]}
    require(set(raw_nodes) == set(actual_nodes), "All raw OSM nodes retained")
    points = {}
    for key, row in raw_nodes.items():
        d = actual_nodes[key]
        require(d["longitude"] == row["lon"] and d["latitude"] == row["lat"] and d["tags"] == row.get("tags", {})
                and d["source_updated_at"] == row.get("timestamp") and d["source_version"] == row.get("version"), "OSM node fields")
        points[key] = metric(Point(row["lon"], row["lat"]))
    roads, barriers = [], []
    for key, row in raw_ways.items():
        require(all(str(node) in points for node in row["nodes"]), "Incomplete raw way topology")
        line = LineString([points[str(node)] for node in row["nodes"]])
        tags = row.get("tags", {})
        if tags.get("highway") in MOTOR:
            roads.append((key, tags, line))
        if tags.get("barrier") and not permission(tags, node=True):
            barriers.append((key, line))
    road_tree, barrier_tree = spatial(roads), spatial(barriers)
    restriction_ways = {str(member["ref"]) for row in raw["elements"] if row["type"] == "relation" for member in row.get("members", []) if member["type"] == "way"}
    expected_candidate, expected_strict, reasons, crossing_proofs = {}, {}, {}, {}
    for wid, way in raw_ways.items():
        tags = way.get("tags", {})
        if wid in restriction_ways or not permission(tags):
            continue
        for ordinal, (a, b) in enumerate(zip(way["nodes"], way["nodes"][1:]), 1):
            a, b = str(a), str(b)
            if not permission(raw_nodes[a].get("tags", {}), node=True) or not permission(raw_nodes[b].get("tags", {}), node=True):
                continue
            line = LineString([points[a], points[b]])
            if intersecting(barriers, barrier_tree, line):
                continue
            eid = f"{wid}:{ordinal}"
            expected_candidate[eid] = (wid, a, b, line, tags)
            rejected, proofs = [], []
            if any(tags.get(key) not in {None, "no"} for key in ("bridge", "tunnel", "indoor")) and "layer" not in tags and "level" not in tags:
                rejected.append("unknown_grade")
            for road_id, rt, geometry in intersecting(roads, road_tree, line):
                if grade(tags) != grade(rt):
                    continue
                overlap = line.intersection(geometry)
                nodes = [node for node in (a, b) if marked(raw_nodes[node].get("tags", {})) and points[node].buffer(.01).covers(overlap)]
                official = [key for key, polygon in polygons if key.startswith("wheelroute:13:") and polygon.covers(overlap)]
                if not nodes and not (tags.get("footway") == "crossing" and official):
                    rejected.append("unverified_carriageway_intersection:" + road_id)
                else:
                    proofs.append((tuple(nodes), tuple(official)))
            reasons[eid] = rejected
            crossing_proofs[eid] = proofs
            if not rejected:
                expected_strict[eid] = expected_candidate[eid]
    for field, expected in [("edges", expected_strict), ("candidate_edges", expected_candidate)]:
        actual = {row["edge_id"]: row for row in delivered[field]}
        require(len(actual) == len(delivered[field]) and set(actual) == set(expected), f"{area} independent {field} population; extra={list(set(actual)-set(expected))[:5]}, missing={list(set(expected)-set(actual))[:5]}")
        for eid, (wid, a, b, line, tags) in expected.items():
            row = actual[eid]
            require(row["way_id"] == wid and row["from_node_id"] == a and row["to_node_id"] == b and row["tags"] == tags
                    and near(row["length_m"], line.length) and row["forward_allowed"] == (tags.get("oneway:foot") != "-1")
                    and row["reverse_allowed"] == (tags.get("oneway:foot") not in {"yes", "1"})
                    and row["source_updated_at"] == raw_ways[wid].get("timestamp") and row["source_version"] == raw_ways[wid].get("version")
                    and metric(shape(row["geometry"])).hausdorff_distance(line) < .0001, "Raw adjacent-node edge fields: " + eid)
            require(row["crossing_node_ids"] == [node for node in (a, b) if marked(raw_nodes[node].get("tags", {}))]
                    and row["permission_basis"] == ("explicit_foot_permission" if tags.get("foot") else "mapped_pedestrian_way"), "Edge crossing/permission attribution: " + eid)
            actual_proofs = Counter((tuple(proof["crossing_node_ids"]), tuple(proof["official_crossing_facility_ids"])) for proof in row["crossing_evidence"])
            require(actual_proofs == (Counter(crossing_proofs[eid]) if field == "edges" else Counter()), "Edge crossing source proof: " + eid)
    require(delivered["source_data_time"] == raw["osm3s"]["timestamp_osm_base"]
            and delivered["restriction_relation_count"] == sum(row["type"] == "relation" for row in raw["elements"]), "Graph time and restriction population")
    return {"points": points, "nodes": raw_nodes, "strict": {row["edge_id"]: row for row in delivered["edges"]},
            "candidate": {row["edge_id"]: row for row in delivered["candidate_edges"]}, "roads": roads, "road_tree": road_tree,
            "barriers": barriers, "barrier_tree": barrier_tree, "reasons": reasons}


def connectors(point, edges, context, polygons):
    links = []
    for eid, edge in edges.items():
        tags, points = edge["tags"], context["points"]
        if grade(tags) != ("0", "0") or (any(tags.get(k) not in {None, "no"} for k in ("bridge", "tunnel", "indoor", "covered")) and tags.get("level") != "0"):
            continue
        a, b = points[edge["from_node_id"]], points[edge["to_node_id"]]
        dx, dy = b.x - a.x, b.y - a.y
        denominator = dx * dx + dy * dy
        fraction = max(0, min(1, ((point.x - a.x) * dx + (point.y - a.y) * dy) / denominator)) if denominator else 0
        target = Point(a.x + fraction * dx, a.y + fraction * dy)
        link = LineString([point, target])
        covered = [key for key, polygon in polygons if polygon.covers(link)]
        if link.length > 20 or not covered or intersecting(context["barriers"], context["barrier_tree"], link):
            continue
        if any(grade(tags) == grade(rt) for _, rt, _ in intersecting(context["roads"], context["road_tree"], link)):
            continue
        links.append({"edge_id": eid, "target": target, "length_m": link.length, "facility_ids": covered})
    return links


def distances(start, edges):
    adjacent = defaultdict(list)
    for edge in edges.values():
        a, b = edge["from_node_id"], edge["to_node_id"]
        if edge["forward_allowed"]:
            adjacent[a].append((b, edge["length_m"]))
        if edge["reverse_allowed"]:
            adjacent[b].append((a, edge["length_m"]))
    result, queue = {start: 0.0}, [(0.0, start)]
    while queue:
        cost, node = heapq.heappop(queue)
        if result[node] != cost:
            continue
        for other, length in adjacent[node]:
            proposal = cost + length
            if proposal < result.get(other, math.inf):
                result[other] = proposal
                heapq.heappush(queue, (proposal, other))
    return result


def best_distance(start, edges, context, links):
    costs = distances(start, edges)
    candidates = []
    for link in links:
        edge = edges[link["edge_id"]]
        for node, allowed in [(edge["from_node_id"], edge["forward_allowed"]), (edge["to_node_id"], edge["reverse_allowed"])]:
            if allowed and node in costs:
                candidates.append(costs[node] + context["points"][node].distance(link["target"]) + link["length_m"])
    return min(candidates) if candidates else None


def check_transfers(root, sources, contexts, polygons, transfers, paths, candidate_paths):
    stops = read(root / "data/taipei_feeds/stops.geojson")["features"]
    expected, reviewed, candidates = set(), [], []
    geo_candidates = {feature["properties"]["transfer_id"]: feature for feature in candidate_paths["features"]}
    for area, station in STATIONS.items():
        groups = defaultdict(list)
        for feature in stops:
            if feature["properties"]["name_zh"].startswith("捷運" + station + "站"):
                groups[feature["properties"]["stop_location_id"]].append(feature["properties"])
        for exit_number in ["1", "2"]:
            for loc in groups:
                expected.add(f"{area}:exit:{exit_number}:stop_location:{loc}")
    actual = {row["transfer_id"]: row for row in transfers["records"]}
    require(len(actual) == len(transfers["records"]) and set(actual) == expected, "Finite three-station/two-exit case population")
    require(paths["features"] == [], "No accepted transfer path may be exported from all-unverified cases")
    for tid, row in actual.items():
        area = tid.split(":")[0]
        context = contexts[area]
        nid, exit_number = row["osm_entrance_node_id"], row["exit_number"]
        official = [r for r in sources["wheelroute_exits"] if r["kname"] == row["official_exit_name"]]
        require(len(official) == 1 and row["official_exit_name"] == STATIONS[area] + "站出口" + exit_number + "-" + exit_number, "Official station/exit designation")
        entrance = context["nodes"][nid]
        entrance_distance = context["points"][nid].distance(metric(Point(float(official[0]["lon"]), float(official[0]["lat"]))))
        tags = entrance.get("tags", {})
        require(tags.get("railway") == "subway_entrance" and (tags.get("ref") == exit_number or tags.get("name", "").startswith("出口" + exit_number + " "))
                and entrance_distance <= 20 and near(row["entrance_identity_distance_m"], round(entrance_distance, 3), .0001), "Independent entrance identity")
        source_stops = [feature["properties"] for feature in stops if feature["properties"]["stop_location_id"] == row["stop_location_id"]
                        and feature["properties"]["name_zh"].startswith("捷運" + STATIONS[area] + "站")]
        eligible = [{k: r[k] for k in ("stop_id", "route_id", "stop_sequence", "direction_code", "boarding_alighting_code")} for r in source_stops if r["boarding_alighting_code"] in {"0", "1"}]
        require(row["boarding_eligible_records"] == eligible and row["unknown_boarding_record_count"] == sum(r["boarding_alighting_code"] is None for r in source_stops)
                and row["alighting_only_record_count"] == sum(r["boarding_alighting_code"] == "-1" for r in source_stops), "Boarding eligibility excludes unknown/alighting-only")
        positions = {(r["longitude"], r["latitude"]) for r in source_stops}
        require(len(positions) == 1 and list(next(iter(positions))) == row["stop_coordinates"] and row["bus_stop_name"] == source_stops[0]["name_zh"]
                and row["bus_stop_address"] == source_stops[0]["address"], "Correct explicit stop location/address/coordinate")
        require(row["status"] == "unverified" and row["walking_distance_m"] is None and row["path_edge_ids"] == [] and not row.get("path_steps"), "Unverified case cannot become a usable transfer")
        point = metric(Point(row["stop_coordinates"]))
        if not eligible:
            require(row["reason"] == "no_explicit_boarding_permission_in_delivered_route_records", "Missing boarding reason")
        else:
            strict_links = connectors(point, context["strict"], context, polygons)
            strict_best = best_distance(nid, context["strict"], context, strict_links)
            require(strict_best is None and row["verified_connector_candidate_count"] == len(strict_links), "Strict reachability and connector count")
            require(row["reason"] == ("no_official_facility_connector" if not strict_links else "pedestrian_topology_disconnected"), "Strict failure boundary")
            links = connectors(point, context["candidate"], context, polygons)
            best = best_distance(nid, context["candidate"], context, links)
            require((best is not None) == (row.get("candidate_distance_m") is not None), "Candidate existence independently reproduced")
            if best is not None:
                require(near(row["candidate_distance_m"], round(best, 3), .0001), "Independent shortest candidate length")
                link_data = row["candidate_connector"]
                link_geometry = metric(shape(link_data["geometry"]))
                target = Point(list(link_geometry.coords)[-1])
                matching_links = [link for link in links if link["edge_id"] == link_data["edge_id"] and link["target"].distance(target) < .0001]
                require(len(matching_links) == 1 and near(link_data["length_m"], matching_links[0]["length_m"], .0001)
                        and link_data["official_facility_record_ids"] == matching_links[0]["facility_ids"], "Candidate connector full official coverage/grade/barriers")
                current, length, geometry_points = nid, 0.0, [context["points"][nid]]
                for step in row["candidate_path_steps"]:
                    edge = context["candidate"][step["edge_id"]]
                    require(step["from_node_id"] == current, "Candidate path node continuity")
                    final = step["to_node_id"].startswith("target:")
                    allowed = ((current == edge["from_node_id"] and edge["forward_allowed"] and (final or step["to_node_id"] == edge["to_node_id"]))
                               or (current == edge["to_node_id"] and edge["reverse_allowed"] and (final or step["to_node_id"] == edge["from_node_id"])))
                    require(allowed, "Candidate per-foot direction")
                    length += context["points"][current].distance(target) if final else edge["length_m"]
                    geometry_points.append(target if final else context["points"][step["to_node_id"]])
                    current = step["to_node_id"]
                geometry_points.append(point)
                length += link_data["length_m"]
                require(current.startswith("target:") and near(round(length, 3), row["candidate_distance_m"], .0001), "Candidate sum of actual edge/partial-edge/connector lengths")
                unverified = [step["edge_id"] for step in row["candidate_path_steps"] if step["edge_id"] not in context["strict"]]
                require(unverified == row["candidate_unverified_crossing_edge_ids"] and unverified, "Unverified candidate edges retained")
                feature = geo_candidates[tid]
                require(feature["properties"]["legal_transfer_verified"] is False and feature["properties"]["unverified_crossing_edge_ids"] == unverified
                        and near(feature["properties"]["candidate_distance_m"], row["candidate_distance_m"])
                        and metric(shape(feature["geometry"])).hausdorff_distance(LineString(geometry_points)) < .0001, "Candidate GeoJSON cannot imply acceptance")
                candidates.append({"transfer_id": tid, "candidate_distance_m": row["candidate_distance_m"], "step_count": len(row["candidate_path_steps"]),
                                   "unverified_edge_reasons": {eid: context["reasons"][eid] for eid in unverified}, "legal_transfer_verified": False})
        reviewed.append({"transfer_id": tid, "status": "pass_unverified_boundary_preserved", "usable_transfer": False,
                         "reason": row["reason"], "boarding_eligible_count": len(eligible), "reviewed_entrance_identity_distance_m": round(entrance_distance, 3)})
    require(set(geo_candidates) == {row["transfer_id"] for row in candidates}, "Candidate GeoJSON population")
    return reviewed, candidates


def run(root, run_tests=True):
    manifest = read(root / "map-data/walking/source_manifest.json")
    facilities = read(root / "map-data/walking/facilities.geojson")
    transfers = read(root / "data/official_cross_city/walking_transfers/transfers.json")
    graphs = read(root / "data/official_cross_city/walking_transfers/graphs.json.gz")
    paths = read(root / "map-data/walking/paths.geojson")
    candidate_paths = read(root / "map-data/walking/candidate_paths.geojson")
    for document, key in [(manifest, "records"), (transfers, "records"), (graphs, "records")]:
        require(document["metadata"]["record_count"] == len(document[key]) and document["metadata"]["source_updated_at"] is None, "Output record count/source time")
    for document in (facilities, paths, candidate_paths):
        require(document["metadata"]["feature_count"] == len(document["features"]) and document["metadata"]["crs"] == "EPSG:4326"
                and document["metadata"]["coordinate_order"] == ["longitude", "latitude"], "GeoJSON count/CRS/order")
    sources, source_counts = check_sources(root, manifest)
    polygons, facility_stats = check_facilities(sources, facilities)
    contexts, graph_stats = {}, []
    require({row["area_id"] for row in graphs["records"]} == set(AREAS), "Three delivered graph regions")
    for row in graphs["records"]:
        area = row["area_id"]
        contexts[area] = check_graph(area, sources["osm_" + area], row, polygons)
        graph_stats.append({"area_id": area, "raw_nodes": len(contexts[area]["nodes"]), "strict_edges": len(contexts[area]["strict"]),
                            "candidate_edges": len(contexts[area]["candidate"]), "status": "pass_raw_node_and_adjacent_way_edge_review"})
    records, candidates = check_transfers(root, sources, contexts, polygons, transfers, paths, candidate_paths)
    tests = {"status": "not_run"}
    if run_tests:
        result = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests/official_cross_city", "-p", "test_walking_evidence.py", "-v"], cwd=root, capture_output=True, text=True)
        output = result.stdout + result.stderr
        match = re.search(r"Ran (\d+) tests?", output)
        require(result.returncode == 0 and match and int(match.group(1)) >= 9 and "skipped" not in output, "Local GIS regression checks failed or skipped: " + output[-2000:])
        tests = {"status": "passed", "tests_run": int(match.group(1)), "skipped": 0,
                 "test_file": "tests/official_cross_city/test_walking_evidence.py"}
    return {"schema_version": 1, "dataset_id": "walking_evidence_independent_review", "metadata": {
        "reviewed_at": datetime.now().astimezone().isoformat(), "status": "pass_with_source_limitations", "reviewer_agent": "/root/crosswalk",
        "review_method": "Independent raw parsing, projected geometry, edge membership, permission/crossing checks, Dijkstra node distances and candidate length sums; producer not imported",
        "reviewed_generated_at": {"transfers": transfers["metadata"]["generated_at"], "graphs": graphs["metadata"]["generated_at"], "source_manifest": manifest["metadata"]["generated_at"]},
        "source_record_counts": source_counts, "facility_review": facility_stats, "graph_review": graph_stats,
        "case_count": len(records), "accepted_transfer_count": 0, "unverified_case_count": len(records), "candidate_review": candidates,
        "local_gis_regressions": tests, "requires_source_review": True,
        "limitations": ["18 cases cover only the named exits and same-station-name source stop locations in three bounded areas; no citywide or all-route coverage.",
            "All 18 cases remain unverified; the candidate path is not a usable or legally accepted transfer edge.",
            "Official facility datum is inferred from retained official rendering; individual source update times are unknown.",
            "Official width/slope raw values and negative sentinels are preserved; units/physical accessibility are not independently established by those values.",
            "OSM tags are static mapped evidence; current signal, construction, closures, unrecorded barriers and on-site legal use are not verified.",
            "Motorway lines are geometric centerlines; finite acquired OSM data cannot prove every physical road surface or fence has been mapped."]}, "records": records}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args()
    report = run(args.root.resolve())
    path = args.root / "map-data/quality/walking_evidence_review.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps({"status": report["metadata"]["status"], "cases": len(report["records"]), "accepted_transfers": 0,
                      "candidates": len(report["metadata"]["candidate_review"]), "tests": report["metadata"]["local_gis_regressions"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
