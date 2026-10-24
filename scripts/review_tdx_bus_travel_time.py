#!/usr/bin/env python3
"""Independent raw-to-output review; does not import any producer modules."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import date, datetime
import gzip
import json
import math
from pathlib import Path
import re
from urllib.parse import parse_qs, quote, urlsplit
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
TERMS_URL = "https://tdx.transportdata.tw/term"
GROUP_FIELDS = {
    "EffectiveDate": "effective_date", "ExpireDate": "expire_date", "UpdateTime": "update_time",
    "UpdateInterval": "update_interval", "ValidityDescription": "validity_description",
    "RouteUID": "route_uid", "RouteID": "route_id", "RouteName": "route_name",
    "SubRouteUID": "sub_route_uid", "SubRouteID": "sub_route_id", "SubRouteName": "sub_route_name",
    "Direction": "direction", "TravelTimes": "travel_times",
}
PAIR_FIELDS = {"Sequence": "sequence", "FromStopID": "from_stop_id", "ToStopID": "to_stop_id",
               "RunTime": "run_time", "StartTime": "start_time", "EndTime": "end_time", "ServiceTag": "service_tag"}


def read(path):
    return json.loads(decoded_bytes(path))


def decoded_bytes(path):
    if path.suffix != ".gz":
        return path.read_bytes()
    with gzip.open(path, "rb") as stream:
        body = stream.read(128 * 1024 * 1024 + 1)
    check(len(body) <= 128 * 1024 * 1024, "Compressed review input exceeds decoded byte limit")
    return body


def read_source(root, receipt):
    path = root / receipt["raw_file"]
    compressed = receipt.get("storage_encoding") == "gzip"
    check(compressed == (path.suffix == ".gz"), "Source storage encoding/suffix mismatch")
    stored_bytes = receipt.get("stored_bytes") if compressed else receipt["bytes"]
    check(receipt["http_status"] == 200 and path.stat().st_size == stored_bytes, "Source receipt/stored-byte mismatch")
    body = decoded_bytes(path)
    check(len(body) == receipt["bytes"], "Source decoded-byte receipt mismatch")
    return json.loads(body)


def audit_unavailable_source(root, receipt, covered):
    check(receipt["status"] == "failed" and receipt["http_status"] == 200
          and receipt.get("complete_unparsed_response_retained") is True,
          "Source unavailability requires a complete retained HTTP 200 response")
    raw = read_source(root, receipt)
    message = raw.get("message") if isinstance(raw, dict) else None
    check(isinstance(message, str) and message.startswith("目前沒有")
          and message.endswith("公車路線資料"),
          "Raw response does not explicitly publish missing route data")
    check(receipt.get("upstream_response_message") == covered.get("upstream_response_message") == message
          and covered.get("availability_status") == "published_no_data_message",
          "Source unavailability classification/message differs from raw response")
    return message


def check(condition, message):
    if not condition:
        raise ValueError(message)


def clock(value):
    match = re.fullmatch(r"([0-9]{1,2}):([0-9]{2})", value or "")
    check(match is not None, "Invalid source clock")
    hour, minute = map(int, match.groups())
    check(0 <= hour <= 24 and 0 <= minute <= 59 and (hour < 24 or minute == 0), "Out-of-range source clock")
    return hour * 60 + minute


def audit_catalog_endpoint(row, endpoints):
    matches = [source for source in endpoints if source.get("city") == "Taipei"
               and source.get("route_name") == row["route_name"]]
    check(len(matches) <= 1, "Reference route has multiple acquisition receipts")
    endpoint = matches[0] if matches else None
    expected = {"endpoint_acquisition_status": endpoint["status"] if endpoint else "not_requested",
                "endpoint_response_empty": endpoint.get("record_count") == 0 if endpoint and endpoint["status"] == "downloaded" else None,
                "endpoint_http_status": endpoint.get("http_status") if endpoint else None,
                "endpoint_availability_status": endpoint.get("availability_status") if endpoint else None}
    check(all(type(row.get(key)) is type(value) and row.get(key) == value for key, value in expected.items()),
          "Reference route endpoint availability/accounting differs")
    if endpoint:
        check(endpoint["download_url"] == row["download_url"], "Reference route acquisition URL differs")


def audit_taipei_endpoint_totals(catalog, coverage, delivery):
    endpoints = {}
    for route in catalog["records"]:
        value = (route["endpoint_acquisition_status"], route["endpoint_availability_status"])
        check(value[0] in {"not_requested", "downloaded", "failed"}, "Unknown reference endpoint acquisition state")
        previous = endpoints.setdefault(route["download_url"], value)
        check(previous == value, "Shared reference URL has conflicting acquisition states")
    counts = Counter(state[0] for state in endpoints.values())
    source_unavailable = sum(state == ("failed", "published_no_data_message") for state in endpoints.values())
    expected = {"reference_route_count": len(catalog["records"]), "unique_endpoint_count": len(endpoints),
                "attempted_endpoint_count": len(endpoints) - counts["not_requested"],
                "successful_endpoint_count": counts["downloaded"], "source_unavailable_endpoint_count": source_unavailable,
                "technical_failure_endpoint_count": counts["failed"] - source_unavailable,
                "not_requested_endpoint_count": counts["not_requested"],
                "reference_route_uids_retained_count": sum(bool(r["retained_files"]) for r in catalog["records"]),
                "all_reference_endpoints_attempted": bool(endpoints) and counts["not_requested"] == 0}
    for document in [catalog, coverage, delivery]:
        actual = document["metadata"].get("taipei_endpoint_acquisition")
        check(isinstance(actual, dict) and actual.keys() == expected.keys()
              and all(type(actual[k]) is type(v) and actual[k] == v for k, v in expected.items()),
              "Reference endpoint progress totals differ from catalog states")
    citywide = ("all_taipei_reference_endpoints_attempted_response_coverage_explicit"
                if expected["all_reference_endpoints_attempted"] else "partial_route_selection_not_citywide_travel_time_coverage")
    check(delivery["metadata"]["citywide_status"] == citywide, "Citywide attempt claim differs from reference endpoint population")
    return expected


def audit_delivery_accounting(manifest, delivery):
    """Check the latest HTTP batch separately from cumulative retained responses."""
    batch = manifest["metadata"]["last_batch"]
    planned_rows = batch["planned_requests"]
    planned = {r["source_id"]: r for r in planned_rows}
    check(len(planned) == len(planned_rows), "Duplicate latest-batch planned scope")
    attempted = batch.get("attempted_source_ids")
    check(isinstance(attempted, list) and all(isinstance(s, str) and s for s in attempted),
          "Latest batch requires explicit attempted source IDs")
    check(len(set(attempted)) == len(attempted), "Duplicate latest-batch attempted scope")
    check(set(attempted) <= set(planned), "Attempted scope outside latest-batch plan")
    count = batch.get("new_business_request_count")
    check(isinstance(count, int) and not isinstance(count, bool) and count == len(attempted),
          "Latest-batch attempted IDs/request count mismatch")
    receipts = [r for r in manifest["records"] if r.get("kind") in {"route_reference", "travel_time"}]
    business_receipts = {r["source_id"]: r for r in receipts}
    check(len(business_receipts) == len(receipts), "Duplicate cumulative business receipt")
    check(set(attempted) <= set(business_receipts), "Attempted scope has no retained acquisition result")
    # Only current planned references enter delivery; old references remain in
    # the manifest. All retained travel observations stay visible across batches.
    expected_ids = set(planned) | {r["source_id"] for r in receipts if r["kind"] == "travel_time"}
    delivery_rows = delivery["records"]
    delivery_index = {r["source_id"]: r for r in delivery_rows}
    check(len(delivery_index) == len(delivery_rows) and set(delivery_index) == expected_ids,
          "Delivery population differs from latest planned/retained travel endpoints")
    pending, reused = [], 0
    for source_id, record in delivery_index.items():
        request = planned.get(source_id) or business_receipts[source_id]
        for field in ["kind", "city", "route_name", "reference_date", "download_url", "source_page"]:
            check(record.get(field) == request.get(field), "Delivery planned request field mismatch: " + field)
        receipt = business_receipts.get(source_id)
        if receipt is None:
            check(record["acquisition_status"] == "not_requested_in_this_bounded_stage", "Unrequested delivery marked successful/failed")
            check(all(record.get(k) is None for k in ["downloaded_at", "raw_file", "http_status"]), "Unrequested scope has fabricated receipt")
            check(record["reused_existing_response"] is False, "Unrequested scope marked reused")
            pending.append(source_id)
        else:
            check(record["acquisition_status"] == receipt["status"], "Delivery acquisition status differs from manifest")
            for field in ["downloaded_at", "raw_file", "http_status"]:
                check(record.get(field) == receipt.get(field), "Delivery source receipt changed: " + field)
            is_reused = bool(receipt.get("reused_existing_response", False))
            check(record["reused_existing_response"] is is_reused, "Delivery reuse flag differs")
            reused += is_reused
    meta = delivery["metadata"]
    pending_ids = meta["not_requested_scope_ids"]
    check(len(pending_ids) == len(set(pending_ids)) and set(pending_ids) == set(pending), "Delivery pending list differs from records")
    check(meta["not_requested_is_failure"] is False, "Unrequested scopes mislabeled as failures")
    check(type(meta["new_business_request_count"]) is int and meta["new_business_request_count"] == count,
          "Delivery/latest-batch request count mismatch")
    check(meta["reused_response_count"] == reused, "Reused response accounting differs")
    check(meta.get("acquisition_stop_reason") == batch.get("stop_reason"), "Delivery stop reason differs from batch evidence")
    expected_stage = "partial_bounded_acquisition" if pending else "completed_bounded_acquisition"
    check(meta["stage_status"] == expected_stage, "Stage claim differs from pending scope population")
    successful = batch.get("successful_source_ids")
    if successful is not None:
        check(isinstance(successful, list) and all(isinstance(s, str) and s for s in successful)
              and len(set(successful)) == len(successful) and set(successful) <= set(attempted),
              "Successful scope IDs differ from latest attempts")
        check(all(business_receipts[s]["status"] == "downloaded" for s in successful),
              "Successful latest-batch scope has no successful receipt")
    return {"planned_endpoint_count": len(planned), "delivery_endpoint_count_including_reuse": len(delivery_index),
            "new_business_requests_including_route_reference": count,
            "latest_batch_attempted_source_ids": attempted,
            "cumulative_retained_travel_response_count": sum(r["kind"] == "travel_time" and r["status"] == "downloaded" for r in receipts),
            "reused_responses": reused, "not_requested_scopes": len(pending),
            "not_requested_scopes_are_failures": False, "stage_status": expected_stage,
            "stop_reason_matches_manifest": True}


def audit(root, reviewer_agent=None):
    manifest = read(root / "raw_data/tdx_bus_travel_time/manifest.json")
    check(manifest["metadata"].get("license", {}).get("url") == TERMS_URL, "Manifest lacks TDX terms URL")
    spec = read(root / "raw_data/tdx_bus_travel_time/specifications/travel_time.json")
    check(spec["servers"] == [{"url": "https://tdx.transportdata.tw/api/premium"}], "Unexpected official specification server")
    travel_schema = spec["components"]["schemas"]["TravelTime"]["properties"]
    check("秒" in travel_schema["RunTime"]["description"], "RunTime unit differs from seconds")
    check("0: 周末weekend; 1: 平日weekdays" in travel_schema["ServiceTag"]["description"], "ServiceTag code meaning changed")
    operations = {p: v["get"] for p, v in spec["paths"].items() if "S2STravelTime" in p}
    check(len(operations) == 2, "Expected the two current estimation operations")
    for operation in operations.values():
        names = {p["name"].lower() for p in operation["parameters"]}
        check(not names & {"date", "version", "$top", "$skip"}, "Historical/pagination contract requires review")
    coverage = read(root / "data/tdx_bus_travel_time/coverage.json")
    check(coverage["metadata"].get("license", {}).get("url") == TERMS_URL, "Coverage lacks TDX terms URL")
    coverage_index = {r["source_id"]: r for r in coverage["records"]}
    travel_sources = [r for r in manifest["records"] if r.get("kind") == "travel_time"]
    check(len(coverage_index) == len(coverage["records"]) == len(travel_sources), "Coverage request population differs from manifest")
    review, retained = [], defaultdict(set)
    total_rows = total_missing = total_groups = 0
    for receipt in travel_sources:
        covered = coverage_index[receipt["source_id"]]
        for key in ["status", "city", "route_name", "download_url", "downloaded_at", "http_status"]:
            check(covered.get(key) == receipt.get(key), "Coverage receipt field mismatch: " + key)
        if receipt["status"] != "downloaded":
            if receipt.get("availability_status") == "published_no_data_message":
                message = audit_unavailable_source(root, receipt, covered)
                review.append({"source_id": receipt["source_id"], "status": "passed_source_unavailability_boundary",
                               "provider_status": receipt["status"], "upstream_response_message": message,
                               "available_travel_models": None, "implies_service_withdrawal": False})
                continue
            review.append({"source_id": receipt["source_id"], "status": "not_run_no_successful_source", "provider_status": receipt["status"]})
            continue
        raw = read_source(root, receipt)
        url = urlsplit(receipt["download_url"])
        check(url.scheme == "https" and url.netloc == "tdx.transportdata.tw", "Unrecognized business source host")
        check(parse_qs(url.query) == {"$format": ["JSON"]}, "Response is selected, filtered or paginated")
        output = read(root / covered["output_file"])
        check(output["schema_version"] == 1 and len(raw) == len(output["records"]), "Output group population changed")
        seen, duplicate_count, pairs, raw_values = set(), 0, [], []
        row_checks = 0
        periods, directions, model_details = set(), Counter(), []
        for group, normalized in zip(raw, output["records"]):
            check(not set(group) - set(GROUP_FIELDS), "Unreviewed group field")
            for source_key, target_key in GROUP_FIELDS.items():
                if source_key != "TravelTimes":
                    check(normalized.get(target_key) == group.get(source_key), "Raw group field changed: " + source_key)
            check(isinstance(group["RouteID"], str) and isinstance(group["SubRouteID"], str), "Source identifiers not strings")
            check(date.fromisoformat(group["EffectiveDate"]) <= date.fromisoformat(group["ExpireDate"]), "Inverted effective period")
            periods.add((group["EffectiveDate"], group["ExpireDate"]))
            directions[str(group["Direction"])] += 1
            retained[group["RouteUID"]].add(covered["output_file"])
            source_pairs, target_pairs = group["TravelTimes"], normalized["travel_times"]
            check(len(source_pairs) == len(target_pairs), "Station/time-window population changed")
            tags, intervals = Counter(), set()
            for source, target in zip(source_pairs, target_pairs):
                check(not set(source) - set(PAIR_FIELDS), "Unreviewed travel-time field")
                for source_key, target_key in PAIR_FIELDS.items():
                    check(target_key in target and target[target_key] == source.get(source_key), "Raw pair field changed or missing null: " + source_key)
                runtime = source.get("RunTime")
                check(runtime is None or (not isinstance(runtime, bool) and isinstance(runtime, (int, float)) and math.isfinite(runtime) and runtime >= 0), "Invalid source RunTime")
                check(source.get("ServiceTag") in {"0", "1"}, "Unknown service tag")
                check(clock(source["StartTime"]) < clock(source["EndTime"]), "Invalid source interval")
                for field in ["FromStopID", "ToStopID"]:
                    check(source.get(field) is None or isinstance(source[field], str), "ID string type changed")
                key = (group["RouteUID"], group["SubRouteUID"], group["Direction"], source.get("Sequence"),
                       source.get("FromStopID"), source.get("ToStopID"), source.get("ServiceTag"), source.get("StartTime"), source.get("EndTime"))
                duplicate_count += key in seen
                seen.add(key)
                pairs.append(source)
                if runtime is not None:
                    raw_values.append(runtime)
                tags[source["ServiceTag"]] += 1
                intervals.add((source["StartTime"], source["EndTime"]))
                row_checks += 1
            model_details.append({"route_uid": group["RouteUID"], "sub_route_uid": group["SubRouteUID"],
                                  "direction": group["Direction"], "station_time_rows": len(source_pairs),
                                  "service_tag_row_counts": dict(tags), "observed_time_intervals": [list(x) for x in sorted(intervals, key=lambda x: clock(x[0]))]})
        missing = sum(p.get("RunTime") is None for p in pairs)
        metadata = output["metadata"]
        check(metadata.get("license", {}).get("url") == TERMS_URL, "Observation lacks TDX terms URL")
        for key, count in {"record_count": len(raw), "travel_time_row_count": len(pairs), "missing_run_time_count": missing,
                           "missing_from_stop_id_count": sum(p.get("FromStopID") is None for p in pairs),
                           "missing_to_stop_id_count": sum(p.get("ToStopID") is None for p in pairs),
                           "duplicate_grain_count": duplicate_count}.items():
            check(metadata[key] == count, "Output metadata count mismatch: " + key)
            if key in covered:
                check(covered[key] == count, "Coverage count mismatch: " + key)
        check(metadata["units"]["run_time"] == "seconds" and metadata["service_tag_dictionary"] == {"0": "weekend", "1": "weekdays"}, "Output units/service code meaning differs")
        check(metadata["sources"] == [receipt], "Output source provenance differs from manifest")
        expected_period = {"effective_dates": sorted({a for a, _ in periods}), "expire_dates": sorted({b for _, b in periods}),
                           "historical_input_period": "近3個月；精確起訖未由來源給定"}
        check(metadata["statistical_period"] == expected_period == covered["statistical_period"], "Effective period changed")
        expected_scope = "complete_unpaginated_requested_endpoint_response" if raw else "empty_requested_endpoint_response"
        check(metadata["scope_status"] == covered["scope_status"] == expected_scope, "Response completeness claim differs")
        total_rows += len(pairs); total_missing += missing; total_groups += len(raw)
        review.append({"source_id": receipt["source_id"], "status": "pass_with_limitations", "source": receipt,
                       "output_file": covered["output_file"], "source_model_count": len(raw), "source_station_time_row_count": len(pairs),
                       "raw_fields_compared_pair_rows": row_checks, "missing_run_time_count": missing,
                       "missing_run_time_nonfirst_sequence_count": sum(p.get("RunTime") is None and p.get("Sequence") != 1 for p in pairs),
                       "missing_from_stop_id_count": sum(p.get("FromStopID") is None for p in pairs),
                       "missing_to_stop_id_count": sum(p.get("ToStopID") is None for p in pairs),
                       "known_run_time_sum_seconds_audit_only": math.fsum(raw_values), "duplicate_grain_count": duplicate_count,
                       "effective_periods": [list(x) for x in sorted(periods)], "direction_model_counts": dict(directions), "models": model_details,
                       "source_fields_periods_ids_direction_service_tags_and_intervals_preserved": True,
                       "missing_values_retained_as_null": True})
    route_source = max((r for r in manifest["records"] if r.get("kind") == "route_reference" and r["status"] == "downloaded"), key=lambda r: r["reference_date"])
    route_wrapper = read_source(root, route_source)
    catalog = read(root / "data/tdx_bus_travel_time/route_catalog.json")
    check(catalog["metadata"].get("license", {}).get("url") == TERMS_URL, "Catalog lacks TDX terms URL")
    reference_rows = route_wrapper["BusRoutes"]
    check(len(reference_rows) == route_wrapper["Count"] == len(catalog["records"]), "Catalog reference Count differs")
    for original, row in zip(reference_rows, catalog["records"]):
        audit_catalog_endpoint(row, coverage["records"])
        check((row["route_id"], row["route_uid"], row["route_name"]) == (original["RouteID"], original["RouteUID"], original["RouteName"]["Zh_tw"]), "Catalog route identity changed")
        check(row["retained_files"] == sorted(retained.get(original["RouteUID"], [])), "Catalog retained-file coverage differs")
        check(row["coverage_status"] == ("downloaded_route_uid_present" if row["retained_files"] else "reference_only_not_downloaded"), "Catalog coverage status differs")
        expected_subroutes = [{"sub_route_uid": s.get("SubRouteUID"), "sub_route_id": s.get("SubRouteID"), "sub_route_name": {"zh_tw": s.get("SubRouteName", {}).get("Zh_tw"), "en": s.get("SubRouteName", {}).get("En")}, "direction": s.get("Direction")} for s in original.get("SubRoutes", [])]
        check(row["sub_routes"] == expected_subroutes, "Catalog subroute population/identity changed")
        check(urlsplit(row["download_url"]).path.endswith("/RouteName/" + quote(original["RouteName"]["Zh_tw"], safe="")), "Catalog request route name changed")
    cmeta = coverage["metadata"]
    success = sum(s["status"] == "downloaded" for s in travel_sources)
    for key, expected in {"request_count": len(travel_sources), "successful_response_count": success,
                          "nonempty_response_count": sum(r.get("source_model_count", 0) > 0 for r in review),
                          "failed_scope_count": sum(s["status"] == "failed" for s in travel_sources),
                          "source_unavailable_count": sum(r["status"] == "passed_source_unavailability_boundary" for r in review),
                          "technical_failure_count": sum(s["status"] == "failed" and s.get("availability_status") != "published_no_data_message" for s in travel_sources),
                          "travel_time_row_count": total_rows, "missing_run_time_count": total_missing,
                          "route_reference_count": len(reference_rows),
                          "taipei_route_uids_retained": sum(bool(r["retained_files"]) for r in catalog["records"])}.items():
        check(cmeta[key] == expected, "Coverage aggregate mismatch: " + key)
    delivery = read(root / "data/tdx_bus_travel_time/delivery.json")
    delivery_meta = delivery["metadata"]
    check(delivery_meta.get("license", {}).get("url") == TERMS_URL, "Delivery lacks TDX terms URL")
    check(TERMS_URL in (root / "docs/tdx-bus-travel-time.md").read_text(encoding="utf-8"), "Usage document lacks TDX terms URL")
    delivery_accounting = audit_delivery_accounting(manifest, delivery)
    endpoint_totals = audit_taipei_endpoint_totals(catalog, coverage, delivery)
    return {"schema_version": 1, "dataset_id": "independent_tdx_bus_travel_time_review", "metadata": {
        "reviewer_agent": reviewer_agent, "review_script_author_agent": "/root/crosswalk",
        "reviewed_at": datetime.now(ZoneInfo("Asia/Taipei")).isoformat(timespec="seconds"),
        "independent_review_status": "pass_with_limitations", "producer_module_imported": False,
        "method": "Independent raw JSON parsing and explicit official-schema field mapping; every retained model and station/service/time row compared without calling producer normalize.",
        "review_script": "scripts/review_tdx_bus_travel_time.py", "official_specification": "raw_data/tdx_bus_travel_time/specifications/travel_time.json",
        "review_population": {"successful_endpoint_responses": success, "models": total_groups, "station_time_rows": total_rows,
                              "source_unavailable_responses": cmeta["source_unavailable_count"],
                              "missing_run_time_rows": total_missing, "taipei_route_reference_rows": len(reference_rows),
                              "taipei_route_uids_with_retained_travel_time": cmeta["taipei_route_uids_retained"]},
        "terms_url_checked": TERMS_URL, "terms_scope": "all retained observations, catalog, coverage, delivery, manifest and usage document",
        "delivery_accounting": delivery_accounting,
        "taipei_endpoint_acquisition": endpoint_totals,
        "limitations": ["只巡檢已成功保存的回應；有限選線不代表臺北、雙北或北北基公路客運完整服務涵蓋。",
                        "官方端點是使用近三個月來源資料推估的當期模型，無歷史日期／版本參數；不是近三年觀測或當日實際耗時。",
                        "原始來源時間沒有UTC offset者保持原字串，不推定為臺北時區。",
                        "基隆101原始兩個模型的Direction均為0；依原始子線保留，不補造返程。",
                        "RunTime缺值不能當作零秒；audit-only秒數加總只檢查數值保存，不是路線總旅行時間。",
                        "沿用同批官方原檔與receipt，不重新向TDX發出計費請求。"]}, "records": review}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--reviewer-agent", help="Actual independent agent executing this review, when applicable")
    args = parser.parse_args()
    result = audit(args.root, args.reviewer_agent)
    output = args.root / "data/quality/tdx_bus_travel_time_review.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps(result["metadata"]["review_population"]))


if __name__ == "__main__":
    main()
