"""Additional official identifier evidence; strict full-field equivalence, not name joins."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict, deque
import json
import os
from pathlib import Path
import re
import xml.etree.ElementTree as ET
import gzip
from urllib.parse import parse_qs, urlsplit

from .common import ROOT, csv_rows, fetch, now, write_json
from .crosswalk import nullable, route_map_key
from .ntpc import SOURCES
from .ntpc_pathdetail import read_payload, identifier, unique_object, reject_constant

RAW = "raw_data/official_cross_city/id_evidence"
OUT = "data/official_cross_city/id_evidence"
NTPC_SPEC = "https://pto.gov.taipei/News_Content.aspx?n=A1DF07A86105B6BB&s=C668AE404DCD2EFE"
TPE_SPEC = "https://pto.gov.taipei/News_Content.aspx?n=A1DF07A86105B6BB&s=55E8ADD164E4F579"
NS = {"p": "https://ptx.transportdata.tw/standard/schema/"}
PUBLIC_SOURCES = {
    **{f"ntpc_{name}.gz": (f"https://tcgbusfs.blob.core.windows.net/ntpcbus/{name}.gz", NTPC_SPEC)
       for name in ["GetStopLocation", "GetStop", "GetPathDetail", "GetEstimateTime"]},
    "blobbus_route_fares.gz": ("https://tcgbusfs.blob.core.windows.net/blobbus/GetBusRouteFareList.gz", TPE_SPEC),
    "ntpcbus_route_fares.gz": ("https://tcgbusfs.blob.core.windows.net/ntpcbus/GetBusRouteFareList.gz", NTPC_SPEC),
}


def source(root, name, url, page, *, offline):
    directory = root / RAW
    path, receipt_path = directory / name, directory / (name + ".source.json")
    if not offline:
        downloaded, receipt = fetch(directory / "staging", name, url, page, {urlsplit(url).hostname}, offline=False, max_mb=16)
        directory.mkdir(parents=True, exist_ok=True)
        receipt_path.unlink(missing_ok=True)
        os.replace(downloaded, path)
        receipt["raw_file"] = str(path.relative_to(root))
        write_json(receipt_path, receipt)
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    if (receipt.get("requested_url") != url or receipt.get("page_url") != page or receipt.get("http_status") != 200
            or receipt.get("bytes") != path.stat().st_size or not receipt.get("downloaded_at")
            or receipt.get("raw_file") != str(path.relative_to(root))):
        raise ValueError("Evidence source provenance mismatch: " + name)
    return path, receipt


def hours(value):
    match = re.fullmatch(r"去程：(.*?)；返程：(.*)", value)
    if not match:
        raise ValueError("Unrecognized leapfrog operation-hours grammar")
    result = []
    for part in match.groups():
        part = part.strip()
        if part == "-":
            result.extend([None, None])
        elif re.fullmatch(r"(?:[01][0-9]|2[0-3])[0-5][0-9]-(?:[01][0-9]|2[0-3])[0-5][0-9]", part):
            result.extend(part.split("-"))
        else:
            raise ValueError("Unrecognized leapfrog clock interval")
    return result


def content_fields(row):
    if not nullable(row.get("departureZh")) or not nullable(row.get("destinationZh")):
        raise ValueError("Missing explicit route endpoints")
    return {"route_name": nullable(row.get("nameZh")),
            "origin_destination": nullable(row.get("departureZh")) + "-" + nullable(row.get("destinationZh")),
            "weekday_clocks": [nullable(row.get(k)) for k in ["goFirstBusTime", "goLastBusTime", "backFirstBusTime", "backLastBusTime"]],
            "holiday_clocks": [nullable(row.get(k)) for k in ["holidayGoFirstBusTime", "holidayGoLastBusTime", "holidayBackFirstBusTime", "holidayBackLastBusTime"]]}


def leapfrog_equivalence(csv_path, route_rows, category_rows):
    accepted = {r["route_nid"]: r["route_id"] for r in category_rows}
    results = []
    for line, row in csv_rows(csv_path, SOURCES["leapfrog"]["required"], lower_headers=True):
        if row["agency_codes"] != "382290000H" or row["type"] != "跳蛙公車":
            raise ValueError("Leapfrog authority/category changed")
        wanted = {"route_name": row["routename"], "origin_destination": row["start_and_end"],
                  "weekday_clocks": hours(row["weekday_operation_hours"]), "holiday_clocks": hours(row["holiday_operation_hours"])}
        # A name alone cannot accept an ID: every shared published field,
        # eight clock values and the explicit official category must agree.
        exact, near = [], []
        for route in route_rows:
            if str(route.get("Id")) != accepted.get(route.get("NId")):
                continue
            actual = content_fields(route)
            if actual == wanted:
                exact.append(route)
            elif actual["route_name"] == wanted["route_name"]:
                near.append({"candidate_route_id_for_review_only": identifier(route["Id"]),
                             "candidate_subroute_id_for_review_only": identifier(route["pathAttributeId"]),
                             "differing_fields": {key: {"csv": wanted[key], "feed": actual[key]} for key in wanted if wanted[key] != actual[key]}})
        ids = sorted({identifier(r["Id"]) for r in exact})
        valid = len(ids) == 1
        results.append({"source_sequence": row["seqno"], "agency_code": row["agency_codes"], "source_row_number": line,
                        "published_fields": wanted, "route_id": ids[0] if valid else None,
                        "route_nid": exact[0]["NId"] if valid else None,
                        "matching_subroute_ids": sorted({identifier(r["pathAttributeId"]) for r in exact}) if valid else [],
                        "status": "verified_full_published_field_snapshot_equivalence" if valid else "requires_review_published_field_conflict",
                        "match_method": "all_shared_fields_and_explicit_official_category; seqno never interpreted as route ID",
                        "conflicting_candidate_evidence": [] if valid else near})
    return results


def fare_rows(path):
    with gzip.open(path, "rb") as stream:
        body = stream.read(32 * 1024 * 1024 + 1)
    if len(body) > 32 * 1024 * 1024 or b"<!DOCTYPE" in body or b"<!ENTITY" in body:
        raise ValueError("Unsafe or oversized official fare XML")
    root = ET.fromstring(body)
    authority = root.findtext("p:AuthorityCode", namespaces=NS)
    if authority not in {"TPE", "NWT"}:
        raise ValueError("Unexpected official fare authority")
    result = []
    for index, row in enumerate(root.findall("p:RouteFares/p:RouteFare", NS), 1):
        result.append({"authority_code": authority, "route_id": identifier(row.findtext("p:RouteID", namespaces=NS)),
                       "subroute_id": identifier(row.findtext("p:SubRouteID", namespaces=NS)),
                       "operator_id": identifier(row.findtext("p:OperatorID", namespaces=NS)),
                       "route_name": row.findtext("p:RouteName/p:Zh_tw", namespaces=NS),
                       "subroute_name": row.findtext("p:SubRouteName/p:Zh_tw", namespaces=NS), "source_record_number": index})
    return result, root.findtext("p:UpdateTime", namespaces=NS)


def missing_nid_routes(tdx_routes, direct_branches, fares):
    records = []
    for row in tdx_routes:
        if route_map_key(row.get("route_map_image_url")) is not None:
            continue
        rid = identifier(row["route_id"])
        if row.get("route_uid") != "TPE" + rid:
            raise ValueError("TDX official authority+RouteID rule changed")
        xml = [r for r in fares if r["authority_code"] == "TPE" and r["route_id"] == rid]
        direct = [r for r in direct_branches if r["route_id"] == rid]
        expected = {(r["subroute_id"], r["operator_id"]) for r in direct}
        xml_tuples = {(r["subroute_id"], r["operator_id"]) for r in xml}
        tdx_tuples = {(s["sub_route_id"], operator) for s in row.get("sub_routes", []) for operator in s.get("operator_i_ds", [])}
        if not expected or expected != xml_tuples or expected != tdx_tuples:
            raise ValueError("Official authority/route/subroute/operator relation conflict: " + rid)
        for item in xml:
            direct_names = {r["route_name_zh"] for r in direct}
            if item["route_name"] not in direct_names or item["route_name"] != row["route_name"]["zh_tw"]:
                raise ValueError("ID tuple name corroboration conflict")
        records.append({"tdx_route_uid": row["route_uid"], "tdx_route_id": rid, "reference_route_id": rid,
                        "authority_code": "TPE", "route_nid": None,
                        "status": "verified_official_ptx_authority_route_subroute_operator_tuple",
                        "evidence_tuples": xml, "tdx_direction_rows": len(row["sub_routes"]),
                        "limitations": ["NId仍為null；已核實的是主管機關與主線/附線/業者ID組合，不補造NId或宣稱實際營運。"]})
    return records


def route_page(text, nid):
    marker = "var routeJsonString = JSON.stringify("
    if text.count(marker) != 1:
        raise ValueError("Missing/ambiguous official route-page JSON")
    body, _ = json.JSONDecoder(object_pairs_hook=unique_object, parse_constant=reject_constant).raw_decode(text[text.index(marker) + len(marker):])
    keys = ["GoDirStops", "BackDirStops"]
    if (body.get("UniRouteId") != nid or not all(k in body for k in keys)
            or not all(body[k] is None or isinstance(body[k], list) for k in keys)
            or not any(isinstance(body[k], list) and body[k] for k in keys)):
        raise ValueError("Official route-page NId or direction arrays changed")
    return body


def stop_evidence(missing, old_paths, stops, locations, paths, estimates, pages, route_nids):
    old_members = {identifier(r["stopId"]) for r in old_paths}
    members = {identifier(r["stopId"]) for r in paths}
    stop_index = {identifier(r["Id"]): r for r in stops}
    locations = {identifier(r["id"]): r for r in locations}
    eta = defaultdict(list)
    for index, row in enumerate(estimates, 1):
        eta[(identifier(row["RouteID"]), identifier(row["StopID"]))].append((index, row))
    results = []
    for original in missing:
        sid, rid = original["stop_id"], original["route_id"]
        item = stop_index.get(sid)
        if item is None or identifier(item["routeId"]) != rid or identifier(item["stopLocationId"]) != original["stop_location_id"]:
            raise ValueError("Original missing-stop identity changed; review new snapshot")
        location = locations[original["stop_location_id"]]
        stop_nid = location["nid"]
        nid = route_nids[rid]
        page = pages[rid]
        memberships = [{"direction": direction, "position_in_direction_array": index,
                        "public_stop_nid": r["UniStopId"], "source_stop_name": r["Name"]}
                       for direction, key in [("0", "GoDirStops"), ("1", "BackDirStops")]
                       for index, r in enumerate(page[key] or []) if r.get("UniStopId") == stop_nid]
        statuses = eta.get((rid, sid), [])
        codes = sorted({r["EstimateTime"] for _, r in statuses})
        verified_no_service = bool(statuses) and codes == ["-4"]
        results.append({"stop_id": sid, "route_id": rid, "stop_location_id": original["stop_location_id"],
                        "official_stop_nid": stop_nid, "official_route_nid": nid,
                        "stop_name": item["nameZh"], "source_sequence_no": item["seqNo"],
                        "source_direction_code": item["goBack"], "pathdetail_membership_old_snapshot": sid in old_members,
                        "pathdetail_membership_new_snapshot": sid in members,
                        "official_main_route_page_memberships": memberships,
                        "route_page_null_direction_arrays": [key for key in ["GoDirStops", "BackDirStops"] if page[key] is None],
                        "estimate_time_codes": codes, "estimate_source_record_numbers": [i for i, _ in statuses],
                        "today_service_status": "official_not_operating_today" if verified_no_service else "requires_review",
                        "status": "verified_source_catalog_and_today_status" if verified_no_service else "requires_review",
                        "subroute_id": None, "branch_membership_status": "not_supplied_by_either_pathdetail_snapshot",
                        "permanent_withdrawal_status": "not_established",
                        "cause_status": "not_inferred_from_current_day_or_name",
                        "route_page_relation": "verified_explicit_stop_location_nid" if memberships else "not_observed_in_published_main_route_page_arrays"})
    return results


def envelope(dataset_id, records, sources, grain, **extra):
    return {"schema_version": 1, "dataset_id": dataset_id, "metadata": {
        "generated_at": now(), "source_data_time": None, "grain": grain, "sources": sources,
        "independent_review_status": "pending", "field_units": {"*_id": "source identifier string", "source_sequence_no": "source ordinal"},
        "source_terms": {"ntpc_csv": "https://data.gov.tw/license", "direct_bus_feeds": "https://data.taipei/rule",
                         "tdx": "https://tdx.transportdata.tw/term", "ebus_pages": "https://ebus.gov.taipei/Home/CopyrightNotice",
                         "note": "保留各來源條款；不將eBus網頁宣稱為開放資料授權。"},
        "limitations": ["各來源快照時間獨立保留；多欄等價只核對所列快照，不推論跨版本永久ID規則。",
                        "今日未營運不等於永久停用；未列PathDetail不補造附線會員。"], **extra}, "records": records}


def stop_sequence_evidence(models, routes, stops, paths, missing):
    """Join explicit source internal IDs, keeping source ordinals and discrepancies."""
    stop_index = {identifier(row["Id"]): (number, row) for number, row in enumerate(stops, 1)}
    if len(stop_index) != len(stops):
        raise ValueError("Duplicate direct stop IDs")
    branches, path_groups = defaultdict(list), defaultdict(list)
    for number, row in enumerate(routes, 1):
        branches[identifier(row["pathAttributeId"])].append((number, row))
    for number, row in enumerate(paths, 1):
        sid = identifier(row["stopId"])
        if sid not in stop_index:
            raise ValueError("PathDetail references an absent direct stop")
        stop = stop_index[sid][1]
        # PathDetail.type is 0/normal or CPT/control point, NOT direction.
        direction = stop["goBack"]
        if direction not in {"0", "1"}:
            raise ValueError("Unknown direct stop direction")
        path_groups[(identifier(row["pathAttributeId"]), direction)].append((number, row))
    for group in path_groups.values():
        group.sort(key=lambda pair: pair[1]["sequenceNo"])
        if len({row["sequenceNo"] for _, row in group}) != len(group):
            raise ValueError("Ambiguous PathDetail sequence numbers")

    memberships, sequences, seen_models, seen_stops = [], [], set(), defaultdict(list)
    for source_id, model_number, model in models:
        rid, sub = identifier(model["RouteID"]), identifier(model["SubRouteID"])
        direction = str(model["Direction"])
        key = (sub, direction)
        if key in seen_models:
            raise ValueError("Duplicate TDX subroute/direction across pages")
        seen_models.add(key)
        direct_routes = branches.get(sub, [])
        if (not direct_routes or model["RouteUID"] != "NWT" + rid or model["SubRouteUID"] != "NWT" + sub
                or model.get("CityCode") != "NWT" or model.get("City") != "NewTaipei"
                or {identifier(row["Id"]) for _, row in direct_routes} != {rid}
                or {identifier(row["providerId"]) for _, row in direct_routes} != {identifier(row["OperatorID"]) for row in model["Operators"]}
                or {row["nameZh"] for _, row in direct_routes} != {model["RouteName"]["Zh_tw"]}
                or {row["pathAttributeName"] for _, row in direct_routes} != {model["SubRouteName"]["Zh_tw"]}):
            raise ValueError("Official main/subroute/operator tuple mismatch")
        direct_paths = path_groups.get(key, [])
        direct_ids = [identifier(row["stopId"]) for _, row in direct_paths]
        tdx_ids = [identifier(row["StopID"]) for row in model["Stops"]]
        ordinals = [row["StopSequence"] for row in model["Stops"]]
        if not ordinals or any(type(n) is not int or n < 1 for n in ordinals) or ordinals != sorted(set(ordinals)):
            raise ValueError("Invalid or ambiguous TDX StopSequence")
        available = Counter(tdx_ids)
        filtered, omissions, direct_lookup = [], [], defaultdict(deque)
        for number, row in direct_paths:
            sid = identifier(row["stopId"])
            if available[sid]:
                filtered.append(sid)
                available[sid] -= 1
                direct_lookup[sid].append((number, row))
            else:
                stop = stop_index[sid][1]
                omissions.append({"stop_id": sid, "stop_name": stop["nameZh"], "pathdetail_record_number": number,
                                  "pathdetail_id": identifier(row["id"]), "source_sequence_no": row["sequenceNo"],
                                  "source_type": row["type"], "source_type_attribute": nullable(row.get("typeAttribute")),
                                  "explicit_virtual_non_boarding_name_annotation": "(虛擬站不停靠)" in stop["nameZh"]})
        sequence_status = ("verified_full_stop_id_sequence" if direct_ids == tdx_ids else
                           "verified_order_with_explicit_non_boarding_virtual_points_omitted"
                           if filtered == tdx_ids and omissions and all(row["explicit_virtual_non_boarding_name_annotation"] for row in omissions)
                           else "requires_source_review_sequence_conflict")
        sequences.append({"route_uid": model["RouteUID"], "route_id": rid, "subroute_uid": model["SubRouteUID"],
                          "subroute_id": sub, "direction": direction, "operator_ids": sorted(identifier(row["OperatorID"]) for row in model["Operators"]),
                          "tdx_source_id": source_id, "tdx_model_record_number": model_number,
                          "direct_route_record_numbers": [number for number, _ in direct_routes],
                          "tdx_update_time": model["UpdateTime"], "tdx_version_id": identifier(model["VersionID"]),
                          "direct_pathdetail_count": len(direct_ids), "tdx_stop_count": len(tdx_ids),
                          "direct_stop_ids_in_source_order": direct_ids, "tdx_stop_ids_in_source_order": tdx_ids,
                          "tdx_stop_sequence_values": ordinals,
                          "tdx_sequence_is_contiguous_one_based": ordinals == list(range(1, len(ordinals) + 1)),
                          "direct_only_memberships": omissions,
                          "tdx_only_stop_ids": list((Counter(tdx_ids) - Counter(direct_ids)).elements()),
                          "status": sequence_status})
        for stop_number, row in enumerate(model["Stops"], 1):
            sid = identifier(row["StopID"])
            if sid not in stop_index or not direct_lookup[sid]:
                raise ValueError("TDX stop ID has no explicit direct-feed membership")
            stop_source_number, stop = stop_index[sid]
            path_number, path = direct_lookup[sid].popleft()
            if (row["StopUID"] != "NWT" + sid or identifier(stop["routeId"]) != rid
                    or stop["goBack"] != direction or identifier(stop["stopLocationId"]) != row.get("StationID")
                    or stop["nameZh"] != row["StopName"]["Zh_tw"]):
                raise ValueError("TDX/direct stop ID, route, direction, station or name conflict")
            direct_position = {"longitude": float(stop["longitude"]), "latitude": float(stop["latitude"])}
            tdx_position = {"longitude": row["StopPosition"].get("PositionLon"), "latitude": row["StopPosition"].get("PositionLat")}
            position_equal = direct_position == tdx_position
            memberships.append({"route_uid": model["RouteUID"], "route_id": rid, "subroute_uid": model["SubRouteUID"],
                                "subroute_id": sub, "direction": direction, "stop_uid": row["StopUID"], "stop_id": sid,
                                "station_id": row["StationID"], "stop_name": stop["nameZh"],
                                "tdx_source_id": source_id, "tdx_model_record_number": model_number, "tdx_stop_record_number": stop_number,
                                "tdx_stop_sequence": row["StopSequence"], "tdx_stop_boarding": row.get("StopBoarding"),
                                "direct_stop_record_number": stop_source_number, "direct_stop_sequence_no": stop["seqNo"],
                                "direct_pathdetail_record_number": path_number, "direct_pathdetail_id": identifier(path["id"]),
                                "direct_pathdetail_sequence_no": path["sequenceNo"], "direct_pathdetail_type": path["type"],
                                "direct_position": direct_position, "tdx_position": tdx_position,
                                "position_comparison": "equal" if position_equal else "source_position_conflict",
                                "requires_source_review": not position_equal,
                                "status": "verified_explicit_internal_id_and_membership"})
            seen_stops[sid].append({"subroute_id": sub, "direction": direction, "tdx_stop_sequence": row["StopSequence"]})
    if set(path_groups) != seen_models:
        raise ValueError("TDX pages do not account for all direct subroute/direction groups")
    accounts = []
    path_stop_ids = {identifier(row["stopId"]) for row in paths}
    for original in missing:
        sid = original["stop_id"]
        if sid not in stop_index:
            raise ValueError("Original unlinked stop disappeared from direct catalog")
        accounts.append({"stop_id": sid, "route_id": original["route_id"], "stop_location_id": original["stop_location_id"],
                         "stop_name": stop_index[sid][1]["nameZh"], "direct_stop_record_number": stop_index[sid][0],
                         "present_in_direct_stop_catalog": True, "present_in_current_pathdetail": sid in path_stop_ids,
                         "tdx_memberships": seen_stops.get(sid, []), "tdx_membership_count": len(seen_stops.get(sid, [])),
                         "status": "accounted_absent_in_complete_published_tdx_and_pathdetail" if sid not in path_stop_ids and sid not in seen_stops else "requires_source_review",
                         "subroute_id": None, "operational_cause": None,
                         "requires_source_review": True,
                         "prior_official_status_evidence": "data/official_cross_city/id_evidence/unlinked_stop_status.json"})
    return memberships, sequences, accounts


def retained_tdx_stop_models(root):
    manifest_path = root / "raw_data/official_cross_city/service_coverage/manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    receipts = sorted((row for row in manifest["records"] if row.get("scope") == "NewTaipei" and row.get("service") == "StopOfRoute"), key=lambda row: row["skip"])
    if not receipts or receipts[0]["skip"] != 0:
        raise ValueError("Missing NewTaipei StopOfRoute first page")
    models, expected_skip = [], 0
    for page_index, receipt in enumerate(receipts):
        parsed = urlsplit(receipt["download_url"])
        query = parse_qs(parsed.query)
        if (parsed.scheme != "https" or parsed.hostname != "tdx.transportdata.tw"
                or parsed.path != "/api/basic/v2/Bus/StopOfRoute/City/NewTaipei"
                or query != {"$format": ["JSON"], "$top": [str(receipt["top"])], "$skip": [str(receipt["skip"])],
                             "$orderby": ["RouteUID,SubRouteUID,Direction"]}):
            raise ValueError("Unexpected TDX StopOfRoute scope or pagination URL")
        path = root / receipt["raw_file"]
        stored = path.read_bytes()
        if (len(stored) > 32 * 1024 * 1024 or (path.suffix == ".gz") != (receipt.get("storage_encoding") == "gzip")):
            raise ValueError("Invalid or oversized TDX raw storage")
        body = gzip.decompress(stored) if receipt.get("storage_encoding") == "gzip" else stored
        if (receipt.get("http_status") != 200 or receipt.get("status") != "downloaded" or receipt["skip"] != expected_skip
                or len(body) > 32 * 1024 * 1024 or receipt.get("stored_bytes", len(stored)) != len(stored) or receipt["bytes"] != len(body)):
            raise ValueError("TDX raw receipt or contiguous pagination mismatch")
        rows = json.loads(body, object_pairs_hook=unique_object, parse_constant=reject_constant)
        if not isinstance(rows, list) or len(rows) != receipt["record_count"] or len(rows) > receipt["top"]:
            raise ValueError("TDX source page count mismatch")
        if page_index < len(receipts) - 1 and len(rows) != receipt["top"]:
            raise ValueError("Unexpected TDX page after exhausted page")
        if page_index == len(receipts) - 1 and len(rows) >= receipt["top"]:
            raise ValueError("TDX tail exhaustion not established")
        models.extend((receipt["source_id"], number, row) for number, row in enumerate(rows, 1))
        expected_skip += receipt["top"]
    return models, receipts


def write_gzip_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    try:
        temporary.write_bytes(gzip.compress((json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8"), mtime=0))
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def run_stop_sequence(root):
    models, tdx_receipts = retained_tdx_stop_models(root)
    direct_paths = {"routes": "raw_data/official_cross_city/crosswalk/ntpc_route_feed.gz",
                    "stops": RAW + "/ntpc_GetStop.gz", "paths": RAW + "/ntpc_GetPathDetail.gz"}
    payloads = {key: read_payload(root / path) for key, path in direct_paths.items()}
    direct_receipts = [json.loads((root / (path + ".source.json")).read_text(encoding="utf-8")) for path in direct_paths.values()]
    for path, receipt in zip(direct_paths.values(), direct_receipts):
        if receipt.get("http_status") != 200 or receipt.get("bytes") != (root / path).stat().st_size or receipt.get("raw_file") != path:
            raise ValueError("Direct-feed source receipt mismatch")
    missing = json.loads((root / OUT / "unlinked_stop_status.json").read_text(encoding="utf-8"))["records"]
    memberships, sequences, accounts = stop_sequence_evidence(models, payloads["routes"]["BusInfo"], payloads["stops"]["BusInfo"], payloads["paths"]["BusInfo"], missing)
    shared = {"source_data_times_raw": {key: value["EssentialInfo"]["UpdateTime"] for key, value in payloads.items()},
              "tdx_update_times": sorted({row[2]["UpdateTime"] for row in models}),
              "tdx_version_ids": sorted({identifier(row[2]["VersionID"]) for row in models}),
              "coverage": {"scope": "all published NewTaipei StopOfRoute pages at retained snapshots", "pagination_status": "exhausted_page",
                           "source_page_record_counts": [row["record_count"] for row in tdx_receipts], "route_uid_count": len({row[2]["RouteUID"] for row in models}),
                           "subroute_direction_count": len(sequences), "tdx_stop_membership_count": len(memberships)},
              "identifier_evidence": {"specification_file": "raw_data/official_cross_city/service_coverage/specifications/bus_v2.json",
                  "source_page": "https://tdx.transportdata.tw/api-service/swagger/basic/2998e851-81d0-40f5-b26d-77e2f5ac4118",
                  "tdx_rule": "StopID/RouteID/SubRouteID are source internal codes; UID is authority prefix plus internal ID",
                  "direct_specification": NTPC_SPEC, "pathdetail_type_page": 7, "getstop_direction_page": 9,
                  "direction_rule": "GetStop.goBack joined by explicit PathDetail.stopId; PathDetail.type is never used as direction"},
              "comparison_rules": ["Join only explicit ID and main/subroute/operator relations; names and positions corroborate.",
                  "Compare complete ordered StopID arrays; preserve original ordinals without assuming a fixed offset.",
                  "Direct-only points are classified only by their explicit 虛擬站不停靠 source annotation; omitted IDs are retained.",
                  "Coordinate conflicts do not alter either source coordinate. No cause or permanent withdrawal is inferred."],
              "source_field_conflict_count": sum(row["position_comparison"] != "equal" for row in memberships),
              "sequence_status_counts": dict(Counter(row["status"] for row in sequences)),
              "direct_only_membership_count": sum(len(row["direct_only_memberships"]) for row in sequences)}
    sources = tdx_receipts + direct_receipts
    outputs = {
        "ntpc_tdx_stop_memberships.json.gz": envelope("ntpc_tdx_explicit_stop_membership_evidence", memberships, sources,
            "one TDX subroute/direction/stop-sequence membership", **shared),
        "ntpc_tdx_stop_sequences.json.gz": envelope("ntpc_tdx_complete_stop_sequence_evidence", sequences, sources,
            "one published TDX subroute/direction ordered StopID sequence", **shared),
        "ntpc_tdx_unlinked_stop_accounting.json": envelope("ntpc_tdx_original_unlinked_stop_accounting", accounts, sources,
            "one original unlinked GetStop ID, accounted against complete published TDX station order", **shared),
    }
    for name, value in outputs.items():
        (write_gzip_json if name.endswith(".gz") else write_json)(root / OUT / name, value)
    return {name: len(value["records"]) for name, value in outputs.items()}


def run(root, *, offline):
    files, receipts = {}, {}
    for name, (url, page) in PUBLIC_SOURCES.items():
        files[name], receipts[name] = source(root, name, url, page, offline=offline)
    cross_raw = root / "raw_data/official_cross_city/crosswalk"
    routes = read_payload(cross_raw / "ntpc_route_feed.gz")["BusInfo"]
    categories = json.loads((root / "data/official_cross_city/ntpc_leapfrog_crosswalk.json").read_text(encoding="utf-8"))["records"]
    leapfrog = leapfrog_equivalence(cross_raw / "ntpc_leapfrog.csv", routes, categories)
    tdx = json.loads((root / "data/tdx/observations/Taipei_2026-10-20_Route_top_100000.json").read_text(encoding="utf-8"))
    direct = json.loads((root / "data/taipei_feeds/route_branches.json").read_text(encoding="utf-8"))
    fares, fare_time = fare_rows(files["blobbus_route_fares.gz"])
    missing_routes = missing_nid_routes(tdx["records"], direct["records"], fares)
    missing = json.loads((root / "data/official_cross_city/ntpc_unlinked_stops_crosswalk.json").read_text(encoding="utf-8"))["records"]
    route_nids = {identifier(r["Id"]): r["NId"] for r in routes}
    pages = {}
    for rid in sorted({r["route_id"] for r in missing}, key=int):
        url = "https://ebus.gov.taipei/Route/StopsOfRoute?routeid=" + route_nids[rid]
        name = "ntpc_route_" + rid + ".html"
        files[name], receipts[name] = source(root, name, url, url, offline=offline)
        pages[rid] = route_page(files[name].read_text(encoding="utf-8"), route_nids[rid])
    payloads = {name: read_payload(files[name]) for name in PUBLIC_SOURCES if name.startswith("ntpc_Get")}
    stops = stop_evidence(missing, read_payload(cross_raw / "ntpc_pathdetail.gz")["BusInfo"],
                          payloads["ntpc_GetStop.gz"]["BusInfo"], payloads["ntpc_GetStopLocation.gz"]["BusInfo"],
                          payloads["ntpc_GetPathDetail.gz"]["BusInfo"], payloads["ntpc_GetEstimateTime.gz"]["BusInfo"], pages, route_nids)
    sources = list(receipts.values())
    prior_receipts = {name: json.loads((cross_raw / (name + ".source.json")).read_text(encoding="utf-8"))
                      for name in ["ntpc_route_feed.gz", "ntpc_leapfrog.csv", "ntpc_pathdetail.gz", "ebus_ntpc_routes.html"]}
    outputs = {
        "leapfrog_list_equivalence.json": envelope("ntpc_leapfrog_list_full_field_equivalence", leapfrog,
            [prior_receipts[name] for name in ["ntpc_route_feed.gz", "ntpc_leapfrog.csv", "ebus_ntpc_routes.html"]],
            "one original leapfrog CSV item", status_counts=dict(Counter(r["status"] for r in leapfrog)),
            source_data_times_raw={"ntpc_route_feed": read_payload(cross_raw / "ntpc_route_feed.gz")["EssentialInfo"].get("UpdateTime")}),
        "missing_nid_routes.json": envelope("taipei_missing_nid_official_id_evidence", missing_routes,
            [receipts["blobbus_route_fares.gz"], {"normalized_file": "data/taipei_feeds/route_branches.json", "metadata": direct["metadata"]},
             {"normalized_file": "data/tdx/observations/Taipei_2026-10-20_Route_top_100000.json", "metadata": tdx["metadata"]}],
            "one TDX main route with null NId", official_fare_update_time=fare_time,
            uid_rule_reference={"normalized_file": "data/tdx/field_dictionary.json", "specification_id": "historical",
                "model": "PTXPlus.Model.Historical.POCO.Content.V2.Bus.BusRoute", "source_field": "RouteUID",
                "rule": "業管機關簡碼 + RouteID; RouteID is 地區既用中之路線代碼(為原資料內碼)"}),
        "unlinked_stop_status.json": envelope("ntpc_unlinked_stop_official_status_evidence", stops, sources + [prior_receipts["ntpc_pathdetail.gz"]],
            "one original unlinked route-stop ID", status_counts=dict(Counter(r["today_service_status"] for r in stops)),
            source_data_times_raw={key: value["EssentialInfo"].get("UpdateTime") for key, value in payloads.items()},
            semantic_specification={"url": NTPC_SPEC, "page": 4, "estimate_time_minus_4": "今日未營運"}),
    }
    for name, value in outputs.items():
        write_json(root / OUT / name, value)
    write_json(root / RAW / "manifest.json", envelope("official_id_evidence_source_manifest", sources, [], "one source acquisition receipt"))
    return {name: len(value["records"]) for name, value in outputs.items()}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--offline", action="store_true")
    mode.add_argument("--fetch", action="store_true")
    mode.add_argument("--stop-sequence", action="store_true", help="Build only additional evidence from retained complete NewTaipei TDX pages; no network")
    args = parser.parse_args(argv)
    if args.stop_sequence:
        print(json.dumps(run_stop_sequence(args.root.resolve()), ensure_ascii=False))
        return
    if not (args.offline or args.fetch):
        print(json.dumps({"status": "plan_only_no_download", "public_sources": PUBLIC_SOURCES}, ensure_ascii=False))
        return
    print(json.dumps(run(args.root.resolve(), offline=args.offline), ensure_ascii=False))


if __name__ == "__main__":
    main()
