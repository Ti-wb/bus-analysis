"""Independent stdlib-only audit of published regional service data.

Reconcile retained HTTP pages to every normalized nested value. This does not
call a producer helper or request TDX data. --scope limits a staged review.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime
import gzip
import json
from pathlib import Path
import re
from urllib.parse import parse_qs, urlsplit
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]


def read(path):
    with (gzip.open(path, "rt", encoding="utf-8") if str(path).endswith(".gz") else path.open(encoding="utf-8")) as stream:
        return json.load(stream)


def field_identity(key):
    return key.replace("_", "").casefold()


def model_identity(row):
    operators = ((str(row["OperatorID"]),) if row.get("OperatorID") is not None else
                 tuple(sorted(str(r["OperatorID"]) for r in row.get("Operators") or [])))
    return row["RouteUID"], row["SubRouteUID"], row.get("Direction"), operators


def compare_values(source, result, path="records", source_key=""):
    """Independent structural parity; only documented non-data omissions allowed."""
    if isinstance(source, dict):
        if not isinstance(result, dict):
            return [path + ": expected object"]
        expected = {field_identity(k): k for k in source if k not in {"GeoHash", "DataHash"}}
        actual = {field_identity(k): k for k in result}
        errors = []
        if len(actual) != len(result) or set(expected) != set(actual):
            return [path + ": different or colliding fields"]
        if not all(re.fullmatch(r"[a-z][a-z0-9_]*", k) for k in result):
            errors.append(path + ": non-snake_case output field")
        for identity, key in expected.items():
            errors.extend(compare_values(source[key], result[actual[identity]], path + "." + key, key))
        return errors
    if isinstance(source, list):
        if not isinstance(result, list) or len(source) != len(result):
            return [path + ": array length differs"]
        return [error for i, (a, b) in enumerate(zip(source, result)) for error in compare_values(a, b, f"{path}[{i}]", source_key)]
    expected = source
    if source is None or isinstance(source, str) and not source.strip():
        expected = None
    elif source_key.endswith(("ID", "UID", "Code")):
        expected = str(source)
    if type(expected) is not type(result) or expected != result:
        return [path + ": scalar type/value differs"]
    return []


def review(root=ROOT, scopes=None):
    raw_dir = root / "raw_data/official_cross_city/service_coverage"
    manifest = read(raw_dir / "manifest.json")
    coverage = read(root / "data/official_cross_city/service_coverage/coverage.json")
    spec = read(raw_dir / "specifications/bus_v2.json")
    if spec["servers"][0]["url"] != "https://tdx.transportdata.tw/api/basic":
        raise ValueError("Official API server differs")
    point = spec["components"]["schemas"]["PTX.Service.DTO.Shared.Specification.V2.Base.PointType"]
    if not all("WGS84" in point["properties"][key]["description"] for key in ("PositionLon", "PositionLat")):
        raise ValueError("Official position CRS contract differs")
    output_rows = []
    for entry in coverage["records"]:
        if scopes and entry["scope"] not in scopes:
            continue
        doc = read(root / entry["output_file"])
        metadata = doc["metadata"]
        failures = []
        def check(condition, message):
            if not condition:
                failures.append(message)
        source_pages = metadata["sources"]
        source_rows = []
        page_counts = []
        prefix_pages = {}
        expected_skip = 0
        tail_observed = False
        request_times = []
        for page_index, receipt in enumerate(source_pages):
            raw_path = root / receipt["raw_file"]
            compressed = raw_path.read_bytes()
            body = gzip.decompress(compressed)
            page = json.loads(body)
            check(receipt["http_status"] == 200 and receipt["status"] == "downloaded", "unsuccessful receipt included")
            check(len(compressed) == receipt["stored_bytes"] and len(body) == receipt["bytes"], "raw HTTP/stored byte count differs")
            check(len(page) == receipt["record_count"], "receipt model count differs")
            check(receipt in manifest["records"], "output source receipt absent from retained manifest")
            check(receipt["skip"] == expected_skip and receipt["scope"] == entry["scope"] and receipt["service"] == entry["service"], "noncontiguous or wrong-scope page")
            check(not tail_observed, "page appended after exhausted tail")
            url = urlsplit(receipt["download_url"])
            query = parse_qs(url.query)
            suffix = "/InterCity" if entry["scope"] == "InterCity" else "/City/" + entry["scope"]
            wanted_path = "/v2/Bus/" + entry["service"] + suffix
            check(url.scheme == "https" and url.netloc == "tdx.transportdata.tw" and url.path == "/api/basic" + wanted_path, "source endpoint differs")
            check(query.get("$skip") == [str(expected_skip)] and query.get("$top") == [str(receipt["top"])]
                  and query.get("$orderby") == ["RouteUID,SubRouteUID,Direction"] and query.get("$format") == ["JSON"], "documented paging/order query differs")
            template = wanted_path if entry["scope"] == "InterCity" else wanted_path.replace(entry["scope"], "{City}")
            params = spec["paths"][template]["get"]["parameters"]
            check({"$skip", "$top", "$orderby", "$format"}.issubset({p["name"] for p in params}), "paging parameters absent from official schema")
            expected_skip += receipt["top"]
            tail_observed = len(page) < receipt["top"]
            page_counts.append(len(page)); source_rows.extend(page)
            for row in page:
                prefix = row["RouteUID"], row["SubRouteUID"], row.get("Direction")
                prefix_pages.setdefault(prefix, set()).add(page_index)
            request_times.append(receipt["downloaded_at"])
        failures.extend(compare_values(source_rows, doc["records"]))
        keys = [(r["RouteUID"], r["SubRouteUID"], r.get("Direction")) for r in source_rows]
        repeated_keys = [list(k) for k, count in Counter(model_identity(r) for r in source_rows).items() if count > 1]
        split_prefixes = [list(k) for k, pages in prefix_pages.items() if len(pages) > 1]
        shared_prefix_count = sum(count > 1 for count in Counter(keys).values())
        check(not repeated_keys, "published complete route/subroute/direction/operator model identities repeat")
        tie_reviews = []
        if split_prefixes:
            stop_file = root / f"data/official_cross_city/service_coverage/{entry['scope']}_StopOfRoute.json.gz"
            stop_doc = read(stop_file) if stop_file.is_file() and entry["service"] == "Schedule" else None
            stop_source = []
            if stop_doc and stop_doc["metadata"]["scope_status"] == "complete_published_endpoint_by_exhausted_page":
                for source in stop_doc["metadata"]["sources"]:
                    stop_source.extend(read(root / source["raw_file"]))
            for prefix in split_prefixes:
                matches = [r for r in source_rows if [r["RouteUID"], r["SubRouteUID"], r.get("Direction")] == prefix]
                expected_operators = sorted({str(op["OperatorID"]) for row in stop_source
                    if [row["RouteUID"], row["SubRouteUID"], row.get("Direction")] == prefix for op in row.get("Operators") or []})
                observed_operators = sorted({str(r["OperatorID"]) for r in matches}) if entry["service"] == "Schedule" else []
                times = sorted({r["UpdateTime"] for r in matches})
                accepted = bool(expected_operators) and observed_operators == expected_operators and len(times) == 1
                tie_reviews.append({"route_uid": prefix[0], "subroute_uid": prefix[1], "direction": prefix[2],
                    "source_page_indices_zero_based": sorted(prefix_pages[tuple(prefix)]), "schedule_operator_ids": observed_operators,
                    "complete_stopofroute_operator_ids": expected_operators, "schedule_source_update_times": times,
                    "status": "all_declared_operator_models_accounted" if accepted else "requires_paging_review"})
                check(accepted, "orderby prefix tie lacks complete operator population evidence: " + str(prefix))
        check(keys == sorted(keys), "published page order is not monotonic")
        check(metadata["scope_status"] == ("complete_published_endpoint_by_exhausted_page" if tail_observed else "partial_contiguous_pages"), "completion does not match source tail")
        check(metadata["empty_endpoint_response"] == (tail_observed and not source_rows), "empty endpoint claim differs")
        unique_routes = {r["RouteUID"] for r in source_rows}
        nested_stops = [stop for r in source_rows for stop in (r.get("Stops") or [])]
        timetables = [t for r in source_rows for t in (r.get("Timetables") or [])]
        frequencies = [t for r in source_rows for t in (r.get("Frequencys") or [])]
        calendars = [t["ServiceDay"] for t in timetables + frequencies if t.get("ServiceDay") is not None]
        special = [s for t in timetables + frequencies for s in (t.get("SpecialDays") or [])]
        expected_counts = {"record_count": len(source_rows), "page_count": len(source_pages), "route_uid_count": len(unique_routes),
                           "nested_stop_count": len(nested_stops), "timetable_count": len(timetables), "frequency_count": len(frequencies),
                           "service_day_object_count": len(calendars), "special_day_object_count": len(special)}
        for field, value in expected_counts.items():
            check(metadata[field] == entry[field] == value, "metadata/coverage aggregate differs: " + field)
        updates = sorted({r["UpdateTime"] for r in source_rows if r.get("UpdateTime")})
        check(metadata["source_record_update_times"] == updates and metadata["source_updated_at"] == (updates[0] if len(updates) == 1 else None), "source update date differs from raw rows")
        reference = read(root / metadata["route_reference"]) if metadata["route_reference"] else None
        reference_uids = {r["route_uid"] for r in reference["records"]} if reference else set()
        check(metadata["reference_route_uids_without_published_service"] == sorted(reference_uids - unique_routes), "reference missing UID list differs")
        check(metadata["published_route_uids_absent_from_dated_reference"] == (sorted(unique_routes - reference_uids) if reference else None), "extra UID list differs")
        check(metadata["statistical_period"] is None, "snapshot must not invent a statistical period")
        output_rows.append({"path": entry["output_file"], "scope": entry["scope"], "service": entry["service"],
                            "record_count": len(source_rows), "status": "needs_correction" if failures else "pass_with_source_limitations",
                            "source_periods": updates, "source_downloaded_at": request_times,
                            "reviewed_source_ids": [r["source_id"] for r in source_pages],
                            "checks": {"failure_count": len(failures), "failures": failures[:100]},
                            "diagnostics": {**expected_counts, "raw_page_counts": page_counts, "exhausted_tail_observed": tail_observed,
                                            "duplicate_paging_keys": repeated_keys, "reference_route_uid_count": len(reference_uids),
                                            "route_subroute_direction_groups_with_multiple_operator_models": shared_prefix_count,
                                            "orderby_prefix_groups_crossing_page_boundaries": split_prefixes,
                                            "orderby_prefix_boundary_reviews": tie_reviews,
                                            "missing_stops_models": sum(r.get("Stops") is None for r in source_rows) if entry["service"] == "StopOfRoute" else None,
                                            "without_calendar_models": sum(not r.get("Timetables") and not r.get("Frequencys") for r in source_rows) if entry["service"] == "Schedule" else None}})
    if not output_rows:
        raise ValueError("No published output matched the requested review scope")
    return {"schema_version": 1, "dataset_id": "independent_regional_service_coverage_review", "metadata": {
        "reviewer_agent": "/root/main_integration", "independent_of_producer": True,
        "reviewed_at": datetime.now(ZoneInfo("Asia/Taipei")).isoformat(timespec="seconds"),
        "status": "needs_correction" if any(r["checks"]["failure_count"] for r in output_rows) else "pass_with_source_limitations",
        "record_count": len(output_rows), "script": "scripts/review_service_coverage.py", "review_scope": scopes,
        "method": "Stdlib-only independent official-spec, HTTPS receipt, byte size, contiguous paging, exhausted tail, full nested field/type/value, complete operator model identity, count, UID, source timestamp and calendar checks; no producer helper imports.",
        "limitations": ["完整為受審發布端點分頁已至尾頁，不代表所有路線都有班表、服務日或實際行駛觀測。", "不同分頁HTTP時點獨立保留；唯一鍵及來源更新時點有檢查，不宣稱API提供交易式快照。", "跨頁相同排序三碼群另以完整StopOfRoute明示業者集合核對所有業者模型，對照與來源時刻留在diagnostics。", "本報告只涵蓋records列出的已產製端點；其他區域／服務不由此推定完成。", "只驗證同一TDX namespace的UID；沒有推定地方feed識別碼橋接。"]}, "records": output_rows}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scope", choices=("Taipei", "NewTaipei", "Keelung", "InterCity"), action="append")
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    result = review(scopes=args.scope)
    if args.write:
        path = ROOT / "data/quality/service_coverage_review.json"
        path.write_text(json.dumps(result, ensure_ascii=False, allow_nan=False, indent=2) + "\n")
    print(json.dumps({"status": result["metadata"]["status"], "records": [{"scope": r["scope"], "service": r["service"], "record_count": r["record_count"], "failures": r["checks"]["failures"]} for r in result["records"]]}, ensure_ascii=False, indent=2))
    raise SystemExit(1 if result["metadata"]["status"] == "needs_correction" else 0)
