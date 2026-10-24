"""Independent, stdlib-only raw-source audit of Taipei ID and stop order."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime
import gzip
import json
import math
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "raw_data/official_cross_city/taipei_id_evidence"
DATA = ROOT / "data/official_cross_city/taipei_id_evidence"


def read(path):
    with (gzip.open(path, "rt", encoding="utf-8") if path.suffix == ".gz" else path.open(encoding="utf-8")) as stream:
        return json.load(stream)


def separation_metres(a, b):
    lon_a, lat_a, lon_b, lat_b = map(math.radians, (*a, *b))
    value = math.sin((lat_b - lat_a) / 2) ** 2 + math.cos(lat_a) * math.cos(lat_b) * math.sin((lon_b - lon_a) / 2) ** 2
    return 6371008.8 * 2 * math.atan2(math.sqrt(min(1, max(0, value))), math.sqrt(max(0, 1 - value)))


def review():
    names = ("memberships.json.gz", "sequences.json.gz", "unlisted_direct_stops.json", "summary.json")
    docs = {name: read(DATA / name) for name in names}
    failures = {name: [] for name in names}
    def check(name, condition, message):
        if not condition:
            failures[name].append(message)
    feeds = {}; receipts = {}
    for name in ("GetRoute", "GetStop", "GetPathDetail", "GetStopLocation"):
        path = RAW / (name + ".gz")
        receipt = read(path.with_name(path.name + ".source.json"))
        if receipt["bytes"] != path.stat().st_size or receipt["http_status"] != 200 or receipt["requested_url"] != "https://tcgbusfs.blob.core.windows.net/blobbus/" + name + ".gz":
            raise ValueError("Official direct source receipt differs: " + name)
        feeds[name] = read(path); receipts[name] = receipt
        if feeds[name]["EssentialInfo"]["CoordinateSystem"] != "WGS84":
            raise ValueError("Direct-source coordinate CRS is not WGS84")
    tdx_doc = read(ROOT / "data/official_cross_city/service_coverage/Taipei_StopOfRoute.json.gz")
    if tdx_doc["metadata"]["scope_status"] != "complete_published_endpoint_by_exhausted_page":
        raise ValueError("A complete Taipei StopOfRoute source is required")
    tdx_models = []
    for receipt in tdx_doc["metadata"]["sources"]:
        path = ROOT / receipt["raw_file"]
        compressed = path.read_bytes(); body = gzip.decompress(compressed)
        if len(compressed) != receipt["stored_bytes"] or len(body) != receipt["bytes"] or receipt["http_status"] != 200:
            raise ValueError("TDX page HTTP/storage bytes or status differ")
        rows = json.loads(body)
        if len(rows) != receipt["record_count"]:
            raise ValueError("TDX page receipt count differs")
        tdx_models.extend(rows)
    spec = read(ROOT / "raw_data/official_cross_city/service_coverage/specifications/bus_v2.json")
    stop_spec = spec["components"]["schemas"]["PTX.Service.DTO.Bus.Specification.V2.Embedded.Stop"]
    if "原資料內碼" not in stop_spec["properties"]["StopID"]["description"] or "StationUID" in stop_spec["properties"]:
        raise ValueError("Official source internal-code or station identifier contract changed")
    for name, doc in docs.items():
        check(name, doc["schema_version"] == 1 and isinstance(doc["dataset_id"], str)
              and len(doc["records"]) == doc["metadata"]["record_count"], "schema/count")
        check(name, doc["metadata"]["tdx_update_times"] == sorted({r["UpdateTime"] for r in tdx_models}), "TDX source update times")
        for source in doc["metadata"]["sources"]:
            feed = feeds[source["source_id"]]
            updated = datetime.strptime(feed["EssentialInfo"]["UpdateTime"], "%Y/%m/%d %H:%M:%S").replace(tzinfo=ZoneInfo("Asia/Taipei")).isoformat()
            check(name, source["source_record_count"] == len(feed["BusInfo"]) and source["source_updated_at"] == updated
                  and source["source_crs"] == "EPSG:4326", "direct source metadata " + source["source_id"])
    stops = {str(r["Id"]): r for r in feeds["GetStop"]["BusInfo"]}
    locations = {str(r["id"]): r for r in feeds["GetStopLocation"]["BusInfo"]}
    if len(stops) != len(feeds["GetStop"]["BusInfo"]) or len(locations) != len(feeds["GetStopLocation"]["BusInfo"]):
        raise ValueError("Ambiguous direct Stop or StopLocation ID")
    route_operators = defaultdict(set)
    for route in feeds["GetRoute"]["BusInfo"]:
        route_operators[(str(route["Id"]), str(route["pathAttributeId"]))].add(str(route["providerId"]))
    paths = defaultdict(list)
    path_stop_ids = set()
    for path in feeds["GetPathDetail"]["BusInfo"]:
        sid = str(path["stopId"]); path_stop_ids.add(sid)
        stop = stops[sid]
        paths[(str(path["pathAttributeId"]), str(stop["goBack"]))].append(path)
    for rows in paths.values():
        rows.sort(key=lambda r: r["sequenceNo"])
    member_rows = docs["memberships.json.gz"]["records"]
    members = {(r["tdx_model_record_number"], r["tdx_stop_record_number"]): r for r in member_rows}
    seq_rows = docs["sequences.json.gz"]["records"]
    sequences = {r["tdx_model_record_number"]: r for r in seq_rows}
    check("memberships.json.gz", len(members) == len(member_rows), "one row per source model/stop ordinal")
    check("sequences.json.gz", len(sequences) == len(seq_rows), "one sequence per source model ordinal")
    expected_member_keys = set(); expected_sequence_keys = set(); tdx_stop_ids = set(); flags_count = Counter(); seq_statuses = Counter()
    separations = []
    for model_number, model in enumerate(tdx_models, 1):
        rid, sub, direction = model["RouteID"], model["SubRouteID"], str(model["Direction"])
        operators = sorted({r["OperatorID"] for r in model["Operators"]})
        check("sequences.json.gz", model["RouteUID"] == "TPE" + rid and model["SubRouteUID"] == "TPE" + sub
              and route_operators[(rid, sub)] == set(operators), "explicit route/subroute/operator tuple " + str(model_number))
        key = model_number; expected_sequence_keys.add(key)
        seq = sequences[key]
        direct_paths = paths[(sub, direction)]
        direct_ids = [str(r["stopId"]) for r in direct_paths]
        source_ids = [r["StopID"] for r in model["Stops"]]
        expected_sequence = {"route_uid": model["RouteUID"], "route_id": rid, "subroute_uid": model["SubRouteUID"], "subroute_id": sub,
            "direction": direction, "operator_ids": operators, "direct_stop_ids_in_source_order": direct_ids,
            "tdx_stop_ids_in_source_order": source_ids, "direct_path_sequence_values": [r["sequenceNo"] for r in direct_paths],
            "tdx_stop_sequence_values": [r["StopSequence"] for r in model["Stops"]],
            "explicit_virtual_stop_ids_omitted": [], "status": "verified_full_stop_id_sequence"}
        check("sequences.json.gz", direct_ids == source_ids and all(seq[k] == v for k, v in expected_sequence.items())
              and type(seq["tdx_model_record_number"]) is int, "complete ordered StopID arrays and ordinal type " + str(model_number))
        seq_statuses[seq["status"]] += 1
        for stop_number, source in enumerate(model["Stops"], 1):
            member_key = model_number, stop_number; expected_member_keys.add(member_key)
            row = members[member_key]; sid = source["StopID"]; tdx_stop_ids.add(sid)
            direct = stops[sid]
            station = str(direct["stopLocationId"])
            check("memberships.json.gz", station in locations and source.get("StationID") == station and str(direct["routeId"]) == rid
                  and str(direct["goBack"]) == direction and source["StopUID"] == "TPE" + sid, "explicit stop/station/route/direction identity " + str(member_key))
            a = [float(direct["longitude"]), float(direct["latitude"])]
            b = [source["StopPosition"]["PositionLon"], source["StopPosition"]["PositionLat"]]
            distance = separation_metres(a, b); separations.append(distance)
            expected_flags = []
            if a != b: expected_flags.append("source_coordinate_difference")
            if direct["nameZh"] != source["StopName"]["Zh_tw"]: expected_flags.append("source_name_difference")
            expected_fields = {"route_uid": model["RouteUID"], "route_id": rid, "subroute_uid": model["SubRouteUID"], "subroute_id": sub,
                "direction": direction, "operator_ids": operators, "stop_uid": source["StopUID"], "stop_id": sid,
                "station_id": source.get("StationID"), "station_uid": None, "direct_stop_location_id": station,
                "tdx_stop_sequence": source["StopSequence"], "direct_stop_sequence": direct["seqNo"],
                "direct_name": direct["nameZh"], "tdx_name": source["StopName"]["Zh_tw"], "direct_coordinates": a, "tdx_coordinates": b,
                "direct_boarding_code": direct.get("pgp"), "tdx_boarding_code": source.get("StopBoarding"),
                "status": "verified_explicit_source_identity", "source_review_flags": expected_flags}
            check("memberships.json.gz", all(row[k] == v for k, v in expected_fields.items())
                  and abs(row["source_coordinate_separation_m"] - distance) <= 0.000500001
                  and type(row["tdx_model_record_number"]) is int and type(row["tdx_stop_record_number"]) is int,
                  "source membership fields/separation/ordinal types " + str(member_key))
            flags_count.update(expected_flags)
    check("memberships.json.gz", set(members) == expected_member_keys, "every original TDX nested stop accounted")
    check("sequences.json.gz", set(sequences) == expected_sequence_keys, "every original TDX model accounted")
    unlisted = docs["unlisted_direct_stops.json"]["records"]
    missing_ids = set(stops) - tdx_stop_ids
    check("unlisted_direct_stops.json", {r["stop_id"] for r in unlisted} == missing_ids and len(unlisted) == len(missing_ids), "all direct-only Stop IDs accounted once")
    for row in unlisted:
        source = stops[row["stop_id"]]
        check("unlisted_direct_stops.json", row["route_id"] == str(source["routeId"]) and row["direct_stop_location_id"] == str(source["stopLocationId"])
              and row["name"] == source["nameZh"] and row["in_direct_pathdetail"] == (row["stop_id"] in path_stop_ids)
              and row["tdx_stopofroute_membership"] is False and row["operational_cause"] is None
              and row["status"] == "requires_source_review_no_published_tdx_membership", "unlisted raw identity/status " + row["stop_id"])
    expected_summary = {"tdx_models": len(tdx_models), "tdx_route_uid_count": len({r["RouteUID"] for r in tdx_models}),
        "memberships": len(expected_member_keys), "membership_status_counts": dict(Counter(r["status"] for r in member_rows)),
        "sequence_status_counts": dict(seq_statuses), "source_review_flag_counts": dict(flags_count), "direct_stops_without_tdx_membership": len(missing_ids)}
    check("summary.json", docs["summary.json"]["records"] == [expected_summary], "independently recomputed summary")
    records = [{"path": str((DATA / name).relative_to(ROOT)), "record_count": len(docs[name]["records"]),
                "status": "needs_correction" if failures[name] else "pass_with_source_limitations",
                "reviewed_generated_at": docs[name]["metadata"]["generated_at"],
                "checks": {"failure_count": len(failures[name]), "failures": failures[name][:100]}} for name in names]
    return {"schema_version": 1, "dataset_id": "taipei_id_evidence_independent_review", "metadata": {
        "reviewer_agent": "/root/main_integration", "independent_of_producer": True,
        "reviewed_at": datetime.now(ZoneInfo("Asia/Taipei")).isoformat(timespec="seconds"), "record_count": len(records),
        "status": "needs_correction" if any(failures.values()) else "pass_with_source_limitations",
        "script": "scripts/review_taipei_id_evidence.py", "method": "Stdlib-only independent parsing of four original blobbus feeds, their HTTP receipts and all three TDX Taipei StopOfRoute pages; complete identity, operator, direction, station, ordered arrays, null boarding, both coordinates and haversine diagnostics; no producer imports.",
        "diagnostics": {**expected_summary, "direct_source_counts": {k: len(v["BusInfo"]) for k, v in feeds.items()},
            "direct_source_update_times": {k: v["EssentialInfo"]["UpdateTime"] for k, v in feeds.items()},
            "tdx_source_update_times": sorted({r["UpdateTime"] for r in tdx_models}),
            "maximum_coordinate_separation_m": max(separations) if separations else None,
            "published_separation_precision_m": 0.001,
            "coordinate_difference_count_over_1_m": sum(value > 1 for value in separations), "unlisted_direct_stop_ids": sorted(missing_ids)},
        "limitations": ["1335個會員座標差異包含來源精度或位置差異，保留兩端原值；距離只是差異診斷，不表示移站或可互換位置。", "3個未列站保留operational_cause:null；未列完整TDX及PathDetail不等於停駛。", "原序數只核對完整順序，沒有套固定1位偏移或重編。", "StationUID未由StopOfRoute發布，保持null；所有ID結論限所列快照。"]}, "records": records}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    result = review()
    if args.write:
        path = ROOT / "data/quality/taipei_id_evidence_review.json"
        path.write_text(json.dumps(result, ensure_ascii=False, allow_nan=False, indent=2) + "\n")
    print(json.dumps({"status": result["metadata"]["status"], "diagnostics": result["metadata"]["diagnostics"],
                      "failures": {r["path"]: r["checks"]["failures"] for r in result["records"]}}, ensure_ascii=False, indent=2))
    raise SystemExit(1 if result["metadata"]["status"] == "needs_correction" else 0)
