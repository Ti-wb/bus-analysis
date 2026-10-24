"""Independent source and geometry review of the six additional walking findings."""
from __future__ import annotations

import argparse
from collections import Counter
import csv
from datetime import datetime
import json
from pathlib import Path
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from pyproj import Transformer
from shapely import transform
from shapely.geometry import LineString, Point, shape


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def require(condition, message):
    if not condition:
        raise ValueError(message)


def audit(root):
    path = root / "map-data/walking/further_evidence.json"
    obj = read(path)
    records, meta = obj["records"], obj["metadata"]
    require(len(records) == meta["record_count"] == 6, "Evidence record population")
    for source in meta["source_files"]:
        file = root / source["raw_file"]
        receipt = read(root / source["receipt_file"])
        require(file.stat().st_size == source["bytes"] == receipt["bytes"] and
                source["http_status"] == receipt["http_status"] == 200, "Source receipt/size/status")
        require(urlsplit(source["download_url"]).scheme == "https" and
                source["source_data_time"] is None, "HTTPS and unknown per-object update time")
    raw = read(root / meta["osm_raw_file"])
    require(raw["osm3s"]["timestamp_osm_base"] == meta["osm_snapshot_time"], "OSM snapshot")
    nodes = {str(n["id"]): n for n in raw["elements"] if n["type"] == "node"}
    ways = {str(n["id"]): n for n in raw["elements"] if n["type"] == "way"}
    project = Transformer.from_crs(4326, 3826, always_xy=True).transform
    def metric(g):
        return transform(g, project, interleaved=False)
    def road_line(wid):
        return metric(LineString([(nodes[str(n)]["lon"], nodes[str(n)]["lat"]) for n in ways[wid]["nodes"]]))
    directory = root / "map-data/raw/walking/further_review"
    signals = list(csv.DictReader((directory / "signals.csv").read_text(encoding="cp950").splitlines(), delimiter="\t"))
    valid = []
    for ordinal, source in enumerate(signals, 1):
        if source["WGS經度座標"] and source["WGS緯度座標"]:
            coordinates = (float(source["WGS經度座標"]), float(source["WGS緯度座標"]))
            valid.append((ordinal, source, coordinates, metric(Point(coordinates))))
    require(len(signals) == meta["official_signal_source"]["source_rows"] == 2710 and len(valid) == 2699,
            "Signal source population and missing coordinates")
    cases = read(root / meta["original_transfer_file"])["records"]
    candidate = next(r for r in cases if r["transfer_id"] == "shilin:exit:1:stop_location:796")
    require({r["osm_edge_id"] for r in records if "osm_edge_id" in r} == set(candidate["candidate_unverified_crossing_edge_ids"]),
            "Additional evidence must account for all five conservative candidate edge flags")
    for row in records:
        require(row["legal_transfer_verified"] is False and row["walking_distance_m"] is None and row["requires_source_review"] is True,
                "Additional context must not turn unresolved crossings into accepted transfers")
        reference = metric(Point(row.get("review_coordinates", row.get("stop_coordinates"))))
        expected = sorted(valid, key=lambda x: x[3].distance(reference))[:3]
        require(len(row["official_signal_nearby_context"]) == 3, "Signal context population")
        for actual, (ordinal, source, coordinates, point) in zip(row["official_signal_nearby_context"], expected):
            require(actual["source_record_number"] == ordinal and actual["source_sequence"] == source["流水號"] and
                    actual["source_intersection_name"] == source["地點"] and actual["source_district"] == source["行政區"] and
                    actual["coordinates"] == list(coordinates) and abs(actual["distance_from_review_point_m"] - point.distance(reference)) < .00051,
                    "Signal context source values/distances")
            require(actual["relation"] == "nearby_context_only_not_crossing_identity", "Proximity silently became a crossing join")
        if "osm_edge_id" not in row:
            case = next(r for r in cases if r["transfer_id"] == row["transfer_id"])
            require(row["stop_location_id"] == case["stop_location_id"] == "70056" and row["stop_coordinates"] == case["stop_coordinates"] and
                    row["official_bus_stop_address"] == case["bus_stop_address"], "Guandu station-side identity/address")
            continue
        wid, number = row["osm_edge_id"].split(":")
        source_way = ways[wid]
        u, v = source_way["nodes"][int(number) - 1:int(number) + 1]
        original = LineString([(nodes[str(u)]["lon"], nodes[str(u)]["lat"]), (nodes[str(v)]["lon"], nodes[str(v)]["lat"])])
        require(shape(row["full_osm_edge_geometry"]).equals_exact(original, 1e-12) and
                abs(metric(original).length - row["full_osm_edge_length_m"]) < .00001 and
                row["osm_way_source_version"] == source_way["version"] and row["osm_way_source_updated_at"] == source_way["timestamp"],
                "Original way IDs, geometry, length, version/time")
        for road in row["intersected_osm_road_ids"]:
            require(metric(original).intersects(road_line(road)), "Claimed full-edge carriageway intersection")
        if "used_partial_geometry" in row:
            used = metric(shape(row["used_partial_geometry"]))
            target = metric(shape(candidate["candidate_connector"]["geometry"]))
            require(used.distance(Point(target.coords[-1])) < .00001 and abs(used.length - row["used_partial_length_m"]) < .00001,
                    "Used fragment must end at the actual candidate projection")
            for check in row["used_partial_road_checks"]:
                road = road_line(check["osm_road_id"])
                require(used.intersects(road) is check["used_partial_intersects_road"] is False and
                        abs(used.distance(road) - check["used_partial_distance_to_road_m"]) < .00001,
                        "Used fragment intersection/distance differs")
    # These four original images were independently opened in this audit. Keep
    # visual findings narrow: versions and missing detailed crossing geometry.
    visual_versions = {"shilin_location.jpg": "R16-MW(26.08)", "guandu_location.jpg": "R25-MW(26.08)", "guandu_information.jpg": "R25-SW(26.05)"}
    require(meta["printed_map_versions"] == visual_versions, "Printed diagram versions")
    report = {"schema_version": 1, "dataset_id": "independent_walking_further_evidence_review", "metadata": {
        "reviewed_at": datetime.now(ZoneInfo("Asia/Taipei")).isoformat(timespec="seconds"), "reviewer": "root", "independent_of_producer": True,
        "source_updated_at": None, "record_count": 1, "status": "passed_with_unresolved_crossing_requirements",
        "method": "Independent raw OSM/CSV parsing, projected full/partial geometries, carriageway intersection distances, source receipts and visual inspection of four official diagrams",
        "limitations": ["Four conservative edges remain at two actual crossing locations; a full-edge false positive outside the used fragment is clarified separately.",
                        "Historical notices and signal proximity do not prove current crossing permission or absence of other facilities.",
                        "No new legal transfer was accepted; on-site evidence or explicit current pedestrian geometry is still needed."]},
        "records": [{"path": "map-data/walking/further_evidence.json", "record_count": 6,
                     "status": "passed_with_source_limitations", "source_files_checked": len(meta["source_files"]),
                     "source_signals": len(signals), "signals_with_coordinates": len(valid),
                     "status_counts": dict(Counter(r["status"] for r in records)), "accepted_new_transfers": 0,
                     "visual_diagrams_checked": [*visual_versions, "y26_april_diagram_1.jpg"]}]}
    target = root / "map-data/quality/walking_further_evidence_review.json"
    target.write_text(json.dumps(report, ensure_ascii=False, allow_nan=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": report["metadata"]["status"], "cases": len(records), "accepted_new_transfers": 0}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    audit(parser.parse_args().root)
