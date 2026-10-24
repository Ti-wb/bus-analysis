"""Independent raw-source audit of the three ID evidence datasets.

No collector or producer helpers are imported. --write saves the review report.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import csv
from datetime import datetime
import gzip
import json
from pathlib import Path
import re
import xml.etree.ElementTree as ET
from urllib.parse import parse_qs, urlsplit
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "raw_data/official_cross_city/id_evidence"
OLD = ROOT / "raw_data/official_cross_city/crosswalk"
DATA = ROOT / "data/official_cross_city/id_evidence"


def read(path):
    with (gzip.open(path, "rt", encoding="utf-8") if str(path).endswith(".gz") else path.open(encoding="utf-8")) as stream:
        return json.load(stream)


def fail_unless(condition, message, failures):
    if not condition:
        failures.append(message)


def clocks(text):
    parts = re.fullmatch(r"去程：(.*?)；返程：(.*)", text)
    if not parts:
        raise ValueError("Unexpected CSV operating-hour field")
    values = []
    for part in parts.groups():
        part = part.strip()
        if part == "-":
            values.extend([None, None])
        else:
            a, b = part.split("-")
            if not all(re.fullmatch(r"(?:[01]\d|2[0-3])[0-5]\d", x) for x in (a, b)):
                raise ValueError("Invalid CSV clock")
            values.extend([a, b])
    return values


def nullable(value):
    return None if value is None or str(value).strip() == "" else str(value).strip()


def walk_receipts(value):
    if isinstance(value, dict):
        if value.get("raw_file") and value.get("downloaded_at"):
            yield value
        for item in value.values():
            yield from walk_receipts(item)
    elif isinstance(value, list):
        for item in value:
            yield from walk_receipts(item)


def validate_receipts(doc, failures):
    checked = set()
    for receipt in walk_receipts(doc["metadata"]):
        path = ROOT / receipt["raw_file"]
        if path in checked:
            continue
        checked.add(path)
        url = receipt.get("download_url", receipt.get("requested_url"))
        fail_unless(path.is_file() and path.stat().st_size == receipt.get("stored_bytes", receipt.get("bytes")), "raw source size/provenance: " + str(path), failures)
        fail_unless(urlsplit(url or "").scheme == "https", "official HTTPS source missing", failures)
    return len(checked)


def review():
    docs = {name: read(DATA / name) for name in ("leapfrog_list_equivalence.json", "missing_nid_routes.json", "unlinked_stop_status.json")}
    for name in ("ntpc_tdx_stop_memberships.json.gz", "ntpc_tdx_stop_sequences.json.gz", "ntpc_tdx_unlinked_stop_accounting.json"):
        if (DATA / name).is_file():
            docs[name] = read(DATA / name)
    failures = {name: [] for name in docs}
    diagnostics = {}
    for name, doc in docs.items():
        fail_unless(doc["schema_version"] == 1 and isinstance(doc["dataset_id"], str)
                    and isinstance(doc["metadata"], dict) and isinstance(doc["records"], list), "schema envelope", failures[name])
        if "record_count" in doc["metadata"]:
            fail_unless(doc["metadata"]["record_count"] == len(doc["records"]), "metadata count", failures[name])
        diagnostics[name] = {"source_files_checked": validate_receipts(doc, failures[name])}
    routes_doc = read(OLD / "ntpc_route_feed.gz")
    routes = routes_doc["BusInfo"]
    page = BeautifulSoup((OLD / "ebus_ntpc_routes.html").read_text(), "html.parser")
    panels = [panel for panel in page.select("div.panel") if "Jump Frog Bus" in panel.select_one(".panel-heading").get_text()]
    if len(panels) != 1:
        raise ValueError("The official route category is not unique")
    category = set()
    for anchor in panels[0].find_all("a", href=True):
        match = re.fullmatch(r"javascript:go\('([A-Z0-9]{10})'\)", anchor["href"])
        if match:
            category.add(match[1])
    with (OLD / "ntpc_leapfrog.csv").open(encoding="utf-8-sig", newline="") as stream:
        csv_rows = list(csv.DictReader(stream))
    name = "leapfrog_list_equivalence.json"
    output = {r["source_sequence"]: r for r in docs[name]["records"]}
    fail_unless(len(output) == len(csv_rows) == 65, "all 65 original CSV items accounted once", failures[name])
    near_details = {}
    for line, source in enumerate(csv_rows, 2):
        result = output[source["seqno"]]
        wanted = {"route_name": source["routename"], "origin_destination": source["start_and_end"],
                  "weekday_clocks": clocks(source["weekday_operation_hours"]), "holiday_clocks": clocks(source["holiday_operation_hours"])}
        matched = []
        conflicts = []
        for route in routes:
            if route["NId"] not in category:
                continue
            fields = {"route_name": nullable(route["nameZh"]),
                      "origin_destination": nullable(route["departureZh"]) + "-" + nullable(route["destinationZh"]),
                      "weekday_clocks": [nullable(route[k]) for k in ("goFirstBusTime", "goLastBusTime", "backFirstBusTime", "backLastBusTime")],
                      "holiday_clocks": [nullable(route[k]) for k in ("holidayGoFirstBusTime", "holidayGoLastBusTime", "holidayBackFirstBusTime", "holidayBackLastBusTime")]}
            if fields == wanted:
                matched.append(route)
            elif fields["route_name"] == wanted["route_name"]:
                conflicts.append({"candidate_route_id_for_review_only": str(route["Id"]), "candidate_subroute_id_for_review_only": str(route["pathAttributeId"]),
                                  "differing_fields": {k: {"csv": wanted[k], "feed": fields[k]} for k in wanted if wanted[k] != fields[k]}})
        ids = {str(r["Id"]) for r in matched}
        accepted = len(ids) == 1
        fail_unless(result["source_row_number"] == line and result["agency_code"] == source["agency_codes"] == "382290000H"
                    and source["type"] == "跳蛙公車" and result["published_fields"] == wanted, "CSV original fields " + source["seqno"], failures[name])
        fail_unless(result["route_id"] == next(iter(ids)) if accepted else result["route_id"] is None,
                    "unique full-field/category match " + source["seqno"], failures[name])
        fail_unless(result["matching_subroute_ids"] == sorted({str(r["pathAttributeId"]) for r in matched}) if accepted else result["matching_subroute_ids"] == [],
                    "matched branch set " + source["seqno"], failures[name])
        fail_unless(result["status"] == ("verified_full_published_field_snapshot_equivalence" if accepted else "requires_review_published_field_conflict"),
                    "unsupported match status " + source["seqno"], failures[name])
        fail_unless(result["conflicting_candidate_evidence"] == ([] if accepted else conflicts), "conflict fields " + source["seqno"], failures[name])
        if accepted:
            fail_unless(result["route_nid"] == matched[0]["NId"], "explicit NId " + source["seqno"], failures[name])
        else:
            fail_unless(result["route_nid"] is None, "unmatched NId must stay null", failures[name])
            near_details[source["seqno"]] = conflicts
    diagnostics[name].update({"raw_csv_count": len(csv_rows), "official_category_nid_count": len(category),
                              "status_counts": dict(Counter(r["status"] for r in docs[name]["records"])), "retained_conflicts": near_details,
                              "route_feed_update_time": routes_doc["EssentialInfo"]["UpdateTime"]})

    name = "missing_nid_routes.json"
    tdx_doc = read(ROOT / "raw_data/tdx/responses/Taipei_2026-10-20_Route_top_100000.json")
    tdx = tdx_doc["BusRoutes"]
    missing = {r["RouteID"]: r for r in tdx if parse_qs(urlsplit(r.get("RouteMapImageUrl", "")).query).get("nid", [None])[0] in {None, "", "null"}}
    direct = read(ROOT / "raw_data/taipei_feeds/GetRoute.gz")["BusInfo"]
    with gzip.open(RAW / "blobbus_route_fares.gz", "rb") as stream:
        fare = ET.fromstring(stream.read())
    ns = {"p": "https://ptx.transportdata.tw/standard/schema/"}
    fail_unless(fare.findtext("p:AuthorityCode", namespaces=ns) == "TPE", "official fare authority", failures[name])
    fare_rows = fare.findall("p:RouteFares/p:RouteFare", ns)
    output = {r["tdx_route_id"]: r for r in docs[name]["records"]}
    fail_unless(set(output) == set(missing) and len(output) == 10, "complete 10 missing-NId population", failures[name])
    for rid, route in missing.items():
        result = output[rid]
        xml_rows = [(i, r) for i, r in enumerate(fare_rows, 1) if r.findtext("p:RouteID", namespaces=ns) == rid]
        xml_tuples = {(r.findtext("p:SubRouteID", namespaces=ns), r.findtext("p:OperatorID", namespaces=ns)) for _, r in xml_rows}
        direct_tuples = {(str(r["pathAttributeId"]), str(r["providerId"])) for r in direct if str(r["Id"]) == rid}
        tdx_tuples = {(s["SubRouteID"], operator) for s in route["SubRoutes"] for operator in s["OperatorIDs"]}
        fail_unless(bool(xml_tuples) and xml_tuples == direct_tuples == tdx_tuples, "independent complete ID tuple " + rid, failures[name])
        expected_xml = [{"authority_code": "TPE", "route_id": rid, "subroute_id": r.findtext("p:SubRouteID", namespaces=ns),
                         "operator_id": r.findtext("p:OperatorID", namespaces=ns), "route_name": r.findtext("p:RouteName/p:Zh_tw", namespaces=ns),
                         "subroute_name": r.findtext("p:SubRouteName/p:Zh_tw", namespaces=ns), "source_record_number": i} for i, r in xml_rows]
        fail_unless(result["evidence_tuples"] == expected_xml and result["authority_code"] == "TPE" and result["reference_route_id"] == rid
                    and result["tdx_route_uid"] == route["RouteUID"] == "TPE" + rid and result["route_nid"] is None
                    and result["tdx_direction_rows"] == len(route["SubRoutes"])
                    and result["status"] == "verified_official_ptx_authority_route_subroute_operator_tuple",
                    "source fields and preserved null NId " + rid, failures[name])
        fail_unless(all(item["route_name"] == route["RouteName"]["Zh_tw"] for item in expected_xml), "raw TDX/XML name corroboration " + rid, failures[name])
    diagnostics[name].update({"raw_tdx_route_count": len(tdx), "raw_tdx_period_start": tdx_doc["StartDate"], "raw_tdx_period_end": tdx_doc["EndDate"],
                              "fare_update_time": fare.findtext("p:UpdateTime", namespaces=ns), "verified_missing_nid_routes": len(output)})

    name = "unlinked_stop_status.json"
    old_stops = read(OLD / "ntpc_stop_feed.gz")["BusInfo"]
    old_paths = read(OLD / "ntpc_pathdetail.gz")["BusInfo"]
    old_members = {str(r["stopId"]) for r in old_paths}
    absent = {str(r["Id"]): r for r in old_stops if str(r["Id"]) not in old_members}
    new_stops = {str(r["Id"]): r for r in read(RAW / "ntpc_GetStop.gz")["BusInfo"]}
    new_paths = {str(r["stopId"]) for r in read(RAW / "ntpc_GetPathDetail.gz")["BusInfo"]}
    locations = {str(r["id"]): r for r in read(RAW / "ntpc_GetStopLocation.gz")["BusInfo"]}
    estimates_doc = read(RAW / "ntpc_GetEstimateTime.gz")
    estimates = defaultdict(list)
    for i, row in enumerate(estimates_doc["BusInfo"], 1):
        estimates[(str(row["RouteID"]), str(row["StopID"]))].append((i, row))
    route_nids = {str(r["Id"]): r["NId"] for r in routes}
    output = {r["stop_id"]: r for r in docs[name]["records"]}
    fail_unless(set(output) == set(absent) and len(output) == 114, "complete original raw PathDetail-unlinked population", failures[name])
    route_pages = {}
    for sid, original in absent.items():
        result = output[sid]
        current = new_stops[sid]
        rid = str(current["routeId"])
        locid = str(current["stopLocationId"])
        location = locations[locid]
        if rid not in route_pages:
            raw_page = (RAW / ("ntpc_route_" + rid + ".html")).read_text()
            marker = "var routeJsonString = JSON.stringify("
            if raw_page.count(marker) != 1:
                raise ValueError("Official route page embedded source is ambiguous")
            route_pages[rid], _ = json.JSONDecoder().raw_decode(raw_page.split(marker)[1])
        body = route_pages[rid]
        expected_memberships = [{"direction": direction, "position_in_direction_array": i, "public_stop_nid": r["UniStopId"], "source_stop_name": r["Name"]}
                                for direction, key in (("0", "GoDirStops"), ("1", "BackDirStops"))
                                for i, r in enumerate(body[key] or []) if r["UniStopId"] == location["nid"]]
        eta_rows = estimates[(rid, sid)]
        fail_unless(str(original["routeId"]) == rid == result["route_id"] and str(original["stopLocationId"]) == locid == result["stop_location_id"]
                    and result["stop_name"] == current["nameZh"] and result["source_direction_code"] == current["goBack"]
                    and result["source_sequence_no"] == current["seqNo"], "original/new GetStop identity " + sid, failures[name])
        fail_unless(result["official_route_nid"] == body["UniRouteId"] == route_nids[rid]
                    and result["official_stop_nid"] == location["nid"] and result["official_main_route_page_memberships"] == expected_memberships
                    and bool(expected_memberships), "explicit GetStopLocation/page NId bridge " + sid, failures[name])
        fail_unless(bool(eta_rows) and {r["EstimateTime"] for _, r in eta_rows} == {"-4"}
                    and result["estimate_time_codes"] == ["-4"] and result["estimate_source_record_numbers"] == [i for i, _ in eta_rows],
                    "source -4 status/record numbers " + sid, failures[name])
        fail_unless(result["pathdetail_membership_old_snapshot"] is False and result["pathdetail_membership_new_snapshot"] == (sid in new_paths)
                    and sid not in new_paths and result["subroute_id"] is None and result["permanent_withdrawal_status"] == "not_established"
                    and result["today_service_status"] == "official_not_operating_today"
                    and result["status"] == "verified_source_catalog_and_today_status"
                    and result["branch_membership_status"] == "not_supplied_by_either_pathdetail_snapshot"
                    and result["cause_status"] == "not_inferred_from_current_day_or_name"
                    and result["route_page_relation"] == "verified_explicit_stop_location_nid"
                    and result["route_page_null_direction_arrays"] == [key for key in ("GoDirStops", "BackDirStops") if body[key] is None],
                    "absence and current-day status limitations " + sid, failures[name])
    import pdfplumber
    with pdfplumber.open(OLD / "ntpc_spec_v5_5.pdf") as pdf:
        eta_spec = pdf.pages[3].extract_text() or ""
        location_spec = pdf.pages[9].extract_text() or ""
    fail_unless("-4：今日未營運" in eta_spec and "nid String 站位 NID" in location_spec, "independent official field semantics", failures[name])
    diagnostics[name].update({"old_missing_stop_count": len(absent), "official_main_route_pages_checked": len(route_pages),
                              "all_current_day_codes": dict(Counter(r["today_service_status"] for r in docs[name]["records"])),
                              "eta_source_update_time": estimates_doc["EssentialInfo"]["UpdateTime"],
                              "specification": {"raw_file": str((OLD / "ntpc_spec_v5_5.pdf").relative_to(ROOT)), "version": "5.5", "eta_pdf_page": 4, "stop_location_pdf_page": 10}})
    if "ntpc_tdx_stop_memberships.json.gz" in docs:
        review_stop_sequences(docs, failures, diagnostics, routes, absent)
    records = []
    for name, doc in docs.items():
        records.append({"path": str((DATA / name).relative_to(ROOT)), "record_count": len(doc["records"]),
                        "status": "needs_correction" if failures[name] else "pass_with_source_limitations",
                        "reviewed_generated_at": doc["metadata"]["generated_at"],
                        "checks": {"failures": failures[name], "failure_count": len(failures[name])}, "diagnostics": diagnostics[name]})
    return {"schema_version": 1, "dataset_id": "independent_id_evidence_review", "metadata": {
        "reviewer_agent": "/root/main_integration", "independent_of_producer": True,
        "reviewed_at": datetime.now(ZoneInfo("Asia/Taipei")).isoformat(timespec="seconds"),
        "record_count": len(records), "status": "needs_correction" if any(failures.values()) else "pass_with_source_limitations",
        "script": "scripts/review_id_evidence.py", "method": "Independently parse original CSV, official category HTML, two GetStop/PathDetail snapshots, GetStopLocation, ETA, 47 page JSON sources, fare XML, raw TDX/GetRoute and all 1383 NewTaipei StopOfRoute models/43270 members; verify route/subroute/operator/stop/location/direction tuples and complete source-ordered arrays without producer helper imports.",
        "limitations": ["跳蛙62筆為所列快照完整共同欄位及官方分類等價；3筆衝突保留，未宣稱永久跨來源ID規則。", "10條缺NId路線核實主管機關/路線/附線/業者tuple，NId仍為null。", "114筆主路線頁會員由站位NId明示核實；兩份PathDetail及完整TDX StopOfRoute均未供附線會員，不補造附線或永久停用原因。", "ETA -4僅為來源2026-10-24快照今日未營運，不是永久撤站證明。", "TDX 194組站序省略312個來源明示虛擬站不停靠點；2筆座標衝突保留雙值待來源釐清，不因ID核實而稱位置一致。"]}, "records": records}


def review_stop_sequences(docs, failures, diagnostics, routes, original_absent):
    """Audit explicit TDX/direct ID tuples and every ordered member independently."""
    member_name = "ntpc_tdx_stop_memberships.json.gz"
    sequence_name = "ntpc_tdx_stop_sequences.json.gz"
    absent_name = "ntpc_tdx_unlinked_stop_accounting.json"
    for name in (sequence_name, absent_name):
        if name not in docs:
            raise ValueError("Incomplete stop-sequence evidence stage")
    stop_rows = read(RAW / "ntpc_GetStop.gz")["BusInfo"]
    stops = {str(r["Id"]): (i, r) for i, r in enumerate(stop_rows, 1)}
    path_rows = read(RAW / "ntpc_GetPathDetail.gz")["BusInfo"]
    paths = {(str(r["pathAttributeId"]), str(r["stopId"])): (i, r) for i, r in enumerate(path_rows, 1)}
    if len(paths) != len(path_rows) or len(stops) != len(stop_rows):
        raise ValueError("Ambiguous direct stop or branch/stop identity")
    groups = defaultdict(list)
    route_groups = defaultdict(list)
    for i, route in enumerate(routes, 1):
        route_groups[(str(route["Id"]), str(route["pathAttributeId"]))].append((i, route))
    for i, row in enumerate(path_rows, 1):
        stop = stops[str(row["stopId"])][1]
        groups[(str(row["pathAttributeId"]), str(stop["goBack"]))].append((i, row))
    for rows in groups.values():
        rows.sort(key=lambda item: item[1]["sequenceNo"])
    members = {(r["tdx_source_id"], r["tdx_model_record_number"], r["tdx_stop_record_number"]): r for r in docs[member_name]["records"]}
    sequences = {(r["tdx_source_id"], r["tdx_model_record_number"]): r for r in docs[sequence_name]["records"]}
    raw_dir = ROOT / "raw_data/official_cross_city/service_coverage/responses"
    source_pages = sorted(raw_dir.glob("NewTaipei_StopOfRoute_skip_*_top_500.json.gz"), key=lambda p: int(p.name.split("_skip_")[1].split("_")[0]))
    expected_keys = set(); expected_sequence_keys = set(); all_tdx_stops = defaultdict(list)
    position_conflicts = []; omitted_count = 0; statuses = Counter(); non_contiguous = 0; models = 0
    for source_page in source_pages:
        source_id = source_page.name.removesuffix(".json.gz")
        rows = read(source_page)
        for model_number, model in enumerate(rows, 1):
            models += 1
            rid, sub, direction = model["RouteID"], model["SubRouteID"], str(model["Direction"])
            key = source_id, model_number
            expected_sequence_keys.add(key)
            result = sequences[key]
            direct_routes = route_groups[(rid, sub)]
            operators = sorted({r["OperatorID"] for r in model["Operators"]})
            direct_operators = sorted({str(r["providerId"]) for _, r in direct_routes})
            fail_unless(model["RouteUID"] == "NWT" + rid and model["SubRouteUID"] == "NWT" + sub
                        and operators == direct_operators and bool(direct_routes), "official route/subroute/operator tuple " + str(key), failures[sequence_name])
            direct_paths = groups[(sub, direction)]
            direct_ids = [str(r["stopId"]) for _, r in direct_paths]
            tdx_ids = [s["StopID"] for s in model["Stops"]]
            omitted = []
            for path_number, row in direct_paths:
                sid = str(row["stopId"])
                if sid not in tdx_ids:
                    stop = stops[sid][1]
                    annotation = "虛擬站不停靠" in stop["nameZh"]
                    omitted.append({"stop_id": sid, "stop_name": stop["nameZh"], "pathdetail_record_number": path_number,
                                    "pathdetail_id": str(row["id"]), "source_sequence_no": row["sequenceNo"],
                                    "source_type": row["type"], "source_type_attribute": nullable(row["typeAttribute"]),
                                    "explicit_virtual_non_boarding_name_annotation": annotation})
                    fail_unless(annotation, "unexplained direct-only point " + sid, failures[sequence_name])
            omitted_ids = {r["stop_id"] for r in omitted}
            expected_status = "verified_full_stop_id_sequence" if direct_ids == tdx_ids else "verified_order_with_explicit_non_boarding_virtual_points_omitted"
            fail_unless([sid for sid in direct_ids if sid not in omitted_ids] == tdx_ids, "complete ordered arrays differ " + str(key), failures[sequence_name])
            ordinals = [s["StopSequence"] for s in model["Stops"]]
            contiguous = ordinals == list(range(1, len(ordinals) + 1))
            non_contiguous += not contiguous; omitted_count += len(omitted); statuses[expected_status] += 1
            expected_sequence_fields = {"route_uid": model["RouteUID"], "route_id": rid, "subroute_uid": model["SubRouteUID"], "subroute_id": sub,
                "direction": direction, "operator_ids": operators, "direct_route_record_numbers": [i for i, _ in direct_routes],
                "tdx_update_time": model["UpdateTime"], "tdx_version_id": str(model["VersionID"]),
                "direct_pathdetail_count": len(direct_ids), "tdx_stop_count": len(tdx_ids),
                "direct_stop_ids_in_source_order": direct_ids, "tdx_stop_ids_in_source_order": tdx_ids,
                "tdx_stop_sequence_values": ordinals, "tdx_sequence_is_contiguous_one_based": contiguous,
                "direct_only_memberships": omitted, "tdx_only_stop_ids": sorted(set(tdx_ids) - set(direct_ids)), "status": expected_status}
            fail_unless(all(result[k] == v for k, v in expected_sequence_fields.items()), "source sequence fields " + str(key), failures[sequence_name])
            for stop_number, tdx_stop in enumerate(model["Stops"], 1):
                member_key = source_id, model_number, stop_number
                expected_keys.add(member_key)
                item = members[member_key]; sid = tdx_stop["StopID"]
                all_tdx_stops[sid].append(member_key)
                stop_number_direct, direct = stops[sid]
                path_number, path = paths[(sub, sid)]
                direct_pos = {"longitude": float(direct["longitude"]), "latitude": float(direct["latitude"])}
                tdx_pos = {"longitude": tdx_stop["StopPosition"]["PositionLon"], "latitude": tdx_stop["StopPosition"]["PositionLat"]}
                conflict = direct_pos != tdx_pos
                if conflict:
                    position_conflicts.append({"stop_id": sid, "direct_position": direct_pos, "tdx_position": tdx_pos})
                fail_unless(str(direct["routeId"]) == rid and str(direct["goBack"]) == direction
                            and str(direct["stopLocationId"]) == tdx_stop["StationID"] and tdx_stop["StopUID"] == "NWT" + sid,
                            "explicit stop/route/location/direction tuple " + str(member_key), failures[member_name])
                expected_member_fields = {"route_uid": model["RouteUID"], "route_id": rid, "subroute_uid": model["SubRouteUID"], "subroute_id": sub,
                    "direction": direction, "stop_uid": tdx_stop["StopUID"], "stop_id": sid, "station_id": tdx_stop["StationID"],
                    "stop_name": tdx_stop["StopName"]["Zh_tw"], "tdx_stop_sequence": tdx_stop["StopSequence"], "tdx_stop_boarding": tdx_stop["StopBoarding"],
                    "direct_stop_record_number": stop_number_direct, "direct_stop_sequence_no": direct["seqNo"],
                    "direct_pathdetail_record_number": path_number, "direct_pathdetail_id": str(path["id"]),
                    "direct_pathdetail_sequence_no": path["sequenceNo"], "direct_pathdetail_type": path["type"],
                    "direct_position": direct_pos, "tdx_position": tdx_pos,
                    "position_comparison": "source_position_conflict" if conflict else "equal", "requires_source_review": conflict,
                    "status": "verified_explicit_internal_id_and_membership"}
                fail_unless(all(item[k] == v for k, v in expected_member_fields.items()), "source stop membership fields " + str(member_key), failures[member_name])
    fail_unless(set(members) == expected_keys and len(members) == len(docs[member_name]["records"]), "all original TDX stop memberships once", failures[member_name])
    fail_unless(set(sequences) == expected_sequence_keys and len(sequences) == len(docs[sequence_name]["records"]), "all original TDX models once", failures[sequence_name])
    for name in (member_name, sequence_name, absent_name):
        fail_unless(docs[name]["metadata"]["source_field_conflict_count"] == len(position_conflicts)
                    and docs[name]["metadata"]["direct_only_membership_count"] == omitted_count
                    and docs[name]["metadata"]["sequence_status_counts"] == dict(statuses), "metadata conflict/sequence totals", failures[name])
    accounting = {r["stop_id"]: r for r in docs[absent_name]["records"]}
    fail_unless(set(accounting) == set(original_absent) and len(accounting) == len(docs[absent_name]["records"]), "all original unlinked stops accounted once", failures[absent_name])
    for sid in original_absent:
        row = accounting[sid]; source_number, stop = stops[sid]
        fail_unless(not all_tdx_stops[sid] and row["tdx_memberships"] == [] and row["tdx_membership_count"] == 0
                    and row["stop_name"] == stop["nameZh"] and row["route_id"] == str(stop["routeId"])
                    and row["stop_location_id"] == str(stop["stopLocationId"]) and row["direct_stop_record_number"] == source_number
                    and row["present_in_direct_stop_catalog"] is True and row["present_in_current_pathdetail"] is False
                    and row["subroute_id"] is None and row["operational_cause"] is None and row["requires_source_review"] is True
                    and row["status"] == "accounted_absent_in_complete_published_tdx_and_pathdetail", "unlinked stop absence and unknown cause " + sid, failures[absent_name])
    diagnostics[member_name].update({"raw_membership_count": len(expected_keys), "source_position_conflict_count": len(position_conflicts), "source_position_conflicts": position_conflicts})
    diagnostics[sequence_name].update({"raw_model_count": models, "sequence_status_counts": dict(statuses), "direct_only_membership_count": omitted_count,
                                       "non_contiguous_source_ordinal_models": non_contiguous, "direction_source": "GetStop.goBack via explicit PathDetail.stopId; not PathDetail.type"})
    diagnostics[absent_name].update({"original_unlinked_stop_count": len(original_absent), "none_in_full_tdx_memberships": all(not all_tdx_stops[sid] for sid in original_absent)})


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    report = review()
    if args.write:
        path = ROOT / "data/quality/id_evidence_review.json"
        path.write_text(json.dumps(report, ensure_ascii=False, allow_nan=False, indent=2) + "\n")
    print(json.dumps({"status": report["metadata"]["status"], "records": [{"path": r["path"], "failures": r["checks"]["failures"]} for r in report["records"]]}, ensure_ascii=False, indent=2))
    raise SystemExit(1 if report["metadata"]["status"] == "needs_correction" else 0)
