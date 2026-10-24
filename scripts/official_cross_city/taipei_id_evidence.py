"""Verify Taipei authority StopOfRoute against explicit direct-feed IDs and order."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime
import gzip
import json
import math
import os
from pathlib import Path

from .common import ROOT, TZ, fetch, now, write_json
from .ntpc_pathdetail import identifier, read_payload

RAW = "raw_data/official_cross_city/taipei_id_evidence"
OUT = "data/official_cross_city/taipei_id_evidence"
SPEC = "https://pto.gov.taipei/News_Content.aspx?n=A1DF07A86105B6BB&s=55E8ADD164E4F579"
TDX = "data/official_cross_city/service_coverage/Taipei_StopOfRoute.json.gz"
FEEDS = ("GetRoute", "GetStop", "GetPathDetail", "GetStopLocation")


def direct_sources(root, refresh=False):
    directory = root / RAW
    directory.mkdir(parents=True, exist_ok=True)
    rows, sources = {}, []
    for name in FEEDS:
        filename = name + ".gz"
        url = "https://tcgbusfs.blob.core.windows.net/blobbus/" + filename
        path = directory / filename
        if refresh:
            temporary, receipt = fetch(directory / "staging", filename, url, SPEC,
                                       {"tcgbusfs.blob.core.windows.net"}, offline=False, max_mb=16)
            os.replace(temporary, path)
            receipt["raw_file"] = str(path.relative_to(root))
            write_json(directory / (filename + ".source.json"), receipt)
        receipt = json.loads((directory / (filename + ".source.json")).read_text())
        if (receipt["requested_url"] != url or receipt["http_status"] != 200 or
                receipt["bytes"] != path.stat().st_size or not receipt.get("downloaded_at")):
            raise ValueError("Taipei direct source provenance mismatch")
        payload = read_payload(path)
        info = payload["EssentialInfo"]
        if info["CoordinateSystem"] != "WGS84" or not isinstance(payload["BusInfo"], list):
            raise ValueError("Unexpected Taipei coordinate system or source container")
        updated = datetime.strptime(info["UpdateTime"], "%Y/%m/%d %H:%M:%S").replace(tzinfo=TZ).isoformat()
        rows[name] = payload["BusInfo"]
        sources.append({**receipt, "source_id": name, "source_updated_at": updated,
                        "source_record_count": len(payload["BusInfo"]), "source_crs": "EPSG:4326"})
    return rows, sources


def point(row, longitude, latitude):
    try:
        lon, lat = float(row[longitude]), float(row[latitude])
        return [lon, lat] if math.isfinite(lon + lat) and 119 < lon < 123 and 21 < lat < 26 else None
    except (KeyError, TypeError, ValueError):
        return None


def nullable(value):
    return None if value in {None, ""} else str(value)


def separation_m(a, b):
    if a is None or b is None:
        return None
    lon1, lat1, lon2, lat2 = map(math.radians, [*a, *b])
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return round(2 * 6371008.8 * math.asin(math.sqrt(min(1, max(0, h)))), 3)


def reconcile(direct, models):
    stops, locations = {}, {}
    for name, key, target in [("GetStop", "Id", stops), ("GetStopLocation", "id", locations)]:
        for row in direct[name]:
            sid = identifier(row[key])
            if sid in target:
                raise ValueError("Duplicate direct identifier")
            target[sid] = row
    route_relations = defaultdict(set)
    for row in direct["GetRoute"]:
        route_relations[(identifier(row["Id"]), identifier(row["pathAttributeId"]))].add(identifier(row["providerId"]))
    path_groups = defaultdict(list)
    for ordinal, row in enumerate(direct["GetPathDetail"], 1):
        stop = stops.get(identifier(row["stopId"]))
        if stop is None:
            raise ValueError("Direct PathDetail refers to missing stop")
        value = row["sequenceNo"]
        if isinstance(value, bool) or not str(value).isdigit():
            raise ValueError("Invalid original PathDetail sequence number")
        path_groups[(identifier(row["pathAttributeId"]), str(stop["goBack"]))].append((int(value), ordinal, row))
    memberships, sequences = [], []
    for index, model in enumerate(models, 1):
        rid, sub = identifier(model["route_id"]), identifier(model["sub_route_id"])
        direction = str(model["direction"])
        operators = sorted(identifier(r["operator_id"]) for r in model["operators"])
        relation_ok = bool(operators) and set(operators).issubset(route_relations.get((rid, sub), set()))
        if model["route_uid"] != "TPE" + rid or model["sub_route_uid"] != "TPE" + sub:
            raise ValueError("Unexpected Taipei UID namespace")
        published_order = []
        for row_number, stop in enumerate(model["stops"], 1):
            sid = identifier(stop["stop_id"])
            if stop["stop_uid"] != "TPE" + sid:
                raise ValueError("Unexpected Taipei stop UID namespace")
            published_order.append(sid)
            original = stops.get(sid)
            station = nullable(stop.get("station_id"))
            reference_station = identifier(original["stopLocationId"]) if original else None
            flags = []
            identity_ok = bool(original and reference_station in locations and relation_ok and str(original["routeId"]) == rid and
                               str(original["goBack"]) == direction and station == reference_station)
            source_name = original.get("nameZh") if original else None
            if source_name != stop.get("stop_name", {}).get("zh_tw"):
                flags.append("source_name_difference")
            p = stop.get("stop_position", {})
            tdx_point = point(p, "position_lon", "position_lat")
            direct_point = point(original or {}, "longitude", "latitude")
            if tdx_point != direct_point:
                flags.append("source_coordinate_difference")
            direct_boarding = nullable(original.get("pgp")) if original else None
            tdx_boarding = stop.get("stop_boarding")
            if direct_boarding is not None and direct_boarding != nullable(tdx_boarding):
                flags.append("source_boarding_permission_difference")
            if reference_station not in locations:
                flags.append("direct_station_record_missing")
            memberships.append({"route_uid": model["route_uid"], "route_id": rid,
                "subroute_uid": model["sub_route_uid"], "subroute_id": sub, "direction": direction,
                "operator_ids": operators, "tdx_model_record_number": index,
                "tdx_stop_record_number": row_number, "stop_uid": stop["stop_uid"], "stop_id": sid,
                "station_id": station, "station_uid": None, "direct_stop_location_id": reference_station,
                "tdx_stop_sequence": stop["stop_sequence"], "direct_stop_sequence": original.get("seqNo") if original else None,
                "direct_name": source_name, "tdx_name": stop.get("stop_name", {}).get("zh_tw"),
                "direct_coordinates": direct_point, "tdx_coordinates": tdx_point,
                "source_coordinate_separation_m": separation_m(direct_point, tdx_point),
                "direct_boarding_code": direct_boarding, "tdx_boarding_code": tdx_boarding,
                "status": "verified_explicit_source_identity" if identity_ok else "requires_review_identifier_relation",
                "source_review_flags": flags})
        source_rows = sorted(path_groups.get((sub, direction), []))
        original_order = [identifier(r[2]["stopId"]) for r in source_rows]
        virtual = [sid for sid in original_order if "虛擬站不停靠" in stops[sid]["nameZh"]]
        without_virtual = [sid for sid in original_order if "虛擬站不停靠" not in stops[sid]["nameZh"]]
        if relation_ok and published_order == original_order:
            status = "verified_full_stop_id_sequence"
            omitted = []
        elif relation_ok and published_order == without_virtual:
            status = "verified_order_with_explicit_virtual_points_omitted"
            omitted = virtual
        else:
            status, omitted = "requires_review_sequence_difference", []
        sequences.append({"route_uid": model["route_uid"], "route_id": rid, "subroute_uid": model["sub_route_uid"],
            "subroute_id": sub, "direction": direction, "operator_ids": operators, "tdx_model_record_number": index,
            "direct_stop_ids_in_source_order": original_order, "tdx_stop_ids_in_source_order": published_order,
            "direct_path_sequence_values": [r[0] for r in source_rows],
            "tdx_stop_sequence_values": [r["stop_sequence"] for r in model["stops"]],
            "explicit_virtual_stop_ids_omitted": omitted, "status": status})
    published_ids = {r["stop_id"] for r in memberships}
    paths_ids = {identifier(r["stopId"]) for r in direct["GetPathDetail"]}
    unlisted = [{"stop_id": sid, "route_id": identifier(row["routeId"]), "direct_stop_location_id": identifier(row["stopLocationId"]),
                 "name": row["nameZh"], "in_direct_pathdetail": sid in paths_ids,
                 "tdx_stopofroute_membership": False, "operational_cause": None,
                 "status": "requires_source_review_no_published_tdx_membership"}
                for sid, row in sorted(stops.items()) if sid not in published_ids]
    return memberships, sequences, unlisted


def save_gzip(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".part")
    temporary.write_bytes(gzip.compress(json.dumps(obj, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode(), mtime=0))
    temporary.replace(path)


def build(root=ROOT, refresh=False):
    direct, sources = direct_sources(root, refresh)
    tdx = json.loads(gzip.decompress((root / TDX).read_bytes()))
    meta = tdx["metadata"]
    if meta["scope_status"] != "complete_published_endpoint_by_exhausted_page" or meta["scope"] != "Taipei":
        raise ValueError("Complete published Taipei StopOfRoute snapshot required")
    memberships, sequences, unlisted = reconcile(direct, tdx["records"])
    shared = {"generated_at": now(), "source_updated_at": None, "sources": sources,
              "tdx_source_dataset": TDX, "tdx_sources": meta["sources"],
              "tdx_update_times": meta["source_record_update_times"], "crs": "EPSG:4326",
              "field_units": {"coordinates": "longitude/latitude degrees", "source_coordinate_separation_m": "metres; haversine radius 6371008.8 m, diagnostic only", "*_sequence": "original source ordinal"},
              "identifier_evidence": {"direct_specification": SPEC, "direct_version": "6.3",
                  "tdx_specification": "https://tdx.transportdata.tw/api-service/swagger/basic/2998e851-81d0-40f5-b26d-77e2f5ac4118",
                  "rule": "TDX ID is source internal code; UID is authority prefix plus ID. Route/subroute/operator, stop route/direction and station IDs corroborate identity."},
              "independent_review_status": "pending", "source_terms": {"direct": "https://data.taipei/rule", "tdx": "https://tdx.transportdata.tw/term"},
              "limitations": ["兩來源快照時間獨立保留；差異不直接推論停駛或搬站。", "站序保留兩來源原值，不重編或假設固定偏移。",
                              "StationUID未由此端點發布，保持null；StationID與直接feed站位ID單獨核對。",
                              "只有來源明示虛擬站不停靠的點可在序列比較中另列省略；其他差異維持待查。"]}
    for filename, dataset_id, rows, grain in [
        ("memberships.json.gz", "taipei_tdx_direct_stop_memberships", memberships, "one published TDX stop membership"),
        ("sequences.json.gz", "taipei_tdx_direct_stop_sequences", sequences, "one published subroute/direction/operator sequence"),
        ("unlisted_direct_stops.json", "taipei_direct_stops_without_tdx_membership", unlisted, "one direct stop ID absent from full TDX membership")]:
        obj = {"schema_version": 1, "dataset_id": dataset_id, "metadata": {**shared, "record_count": len(rows), "grain": grain,
               "status_counts": dict(Counter(r["status"] for r in rows)),
               "requires_source_review": any("requires_" in r["status"] or r.get("source_review_flags") for r in rows)}, "records": rows}
        target = root / OUT / filename
        save_gzip(target, obj) if filename.endswith(".gz") else write_json(target, obj)
    summary = {"schema_version": 1, "dataset_id": "taipei_tdx_direct_identity_summary", "metadata": shared,
        "records": [{"tdx_models": len(tdx["records"]), "tdx_route_uid_count": meta["route_uid_count"],
                     "memberships": len(memberships), "membership_status_counts": dict(Counter(r["status"] for r in memberships)),
                     "sequence_status_counts": dict(Counter(r["status"] for r in sequences)),
                     "source_review_flag_counts": dict(Counter(f for r in memberships for f in r["source_review_flags"])),
                     "direct_stops_without_tdx_membership": len(unlisted)}]}
    summary["metadata"]["record_count"] = 1
    write_json(root / OUT / "summary.json", summary)
    return summary["records"][0]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--fetch-direct", action="store_true", help="Refresh only the four public direct feeds; no TDX requests")
    mode.add_argument("--offline", action="store_true")
    args = parser.parse_args()
    if not (args.fetch_direct or args.offline):
        print(json.dumps({"status": "plan_only", "required_complete_tdx_input": TDX}))
        return
    print(json.dumps(build(args.root, args.fetch_direct), ensure_ascii=False))


if __name__ == "__main__":
    main()
