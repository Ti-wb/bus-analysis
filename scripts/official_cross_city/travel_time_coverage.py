"""Offline Taipei premium travel-time coverage by explicit model and stop pair.

Default rebuilds the saved source inventory. --refresh-inventory selects the
currently retained endpoint population; neither mode issues network requests.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime
import gzip
import json
import math
from pathlib import Path
import re
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[2]
OUT = "data/official_cross_city/travel_time_coverage"
REFERENCE = "data/official_cross_city/service_coverage/Taipei_StopOfRoute.json.gz"
CATALOG = "data/tdx_bus_travel_time/route_catalog.json"
SPEC = "raw_data/tdx_bus_travel_time/specifications/travel_time.json"
TAGS = {"0": "weekend", "1": "weekdays"}
KNOWN_RELATIONS = {"matched_unique_adjacent_pair", "null_origin_at_current_first_stop", "outside_reference_route_uid"}


def read(root, path):
    path = root / path
    with (gzip.open(path, "rt", encoding="utf-8") if path.suffix == ".gz" else path.open(encoding="utf-8")) as stream:
        return json.load(stream)


def write(root, path, doc):
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    body = (json.dumps(doc, ensure_ascii=False, allow_nan=False, indent=None if target.suffix == ".gz" else 2) + "\n").encode()
    temporary = target.with_name(target.name + ".part")
    temporary.write_bytes(gzip.compress(body, mtime=0) if target.suffix == ".gz" else body)
    temporary.replace(target)


def key(row):
    return row.get("route_uid"), row.get("sub_route_uid"), row.get("direction")


def clock_minute(value):
    if not isinstance(value, str) or not re.fullmatch(r"\d{1,2}:\d{2}", value):
        return None
    hour, minute = map(int, value.split(":"))
    if minute >= 60 or hour > 24 or hour == 24 and minute:
        return None
    return hour * 60 + minute


def window_status(start, end):
    a, b = clock_minute(start), clock_minute(end)
    return "valid_source_clock_window" if a is not None and b is not None and a < b else "unsupported_source_clock_window"


def runtime_status(value):
    if value is None:
        return "missing"
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        return "unsupported_source_value"
    return "source_zero" if value == 0 else "source_positive"


def pair_match(row, stops, all_stop_ids):
    """Use source IDs/array adjacency only, never the premium Sequence number."""
    from_id, to_id = row.get("from_stop_id"), row.get("to_stop_id")
    ids = [s["stop_id"] for s in stops]
    occurrences = [i for i in range(len(ids) - 1) if (ids[i], ids[i + 1]) == (from_id, to_id)]
    if len(occurrences) == 1:
        return "matched_unique_adjacent_pair", occurrences
    if occurrences:
        return "ambiguous_repeated_adjacent_pair", occurrences
    if to_id is None:
        return "missing_to_stop_id", []
    if from_id is None:
        return ("null_origin_at_current_first_stop" if ids and to_id == ids[0]
                else "null_origin_at_current_nonfirst_stop" if to_id in ids
                else "null_origin_stop_absent_from_current_model"), []
    if from_id == "0":
        return "unsupported_literal_zero_origin", []
    if from_id in ids and to_id in ids:
        return "same_model_stop_ids_not_adjacent", []
    if from_id in all_stop_ids and to_id in all_stop_ids:
        return "stop_ids_known_only_in_other_models", []
    return "stop_id_absent_from_current_snapshot", []


def acquisition_status(reference, by_url):
    """Catalog URL is request lineage; returned models still require UID joins."""
    receipt = by_url.get(reference["download_url"])
    if receipt is None:
        return "unrequested"
    if receipt.get("availability_status") == "published_no_data_message":
        return "source_published_no_data"
    if receipt.get("status") != "downloaded":
        return "request_technical_failure"
    return "requested_complete_response"


def pack_pair_evidence(rows):
    """Keep complete row lineage while storing original values only once."""
    groups = {}
    for row in rows:
        iid = row["premium_instance_id"]
        group = groups.setdefault(iid, {"premium_instance_id": iid, "source_row_numbers": [],
                                        "usable_numeric_window_row_numbers": []})
        group["source_row_numbers"].append(row["source_row_number"])
        if (row["runtime_status"] in {"source_positive", "source_zero"}
                and row["clock_window_status"] == "valid_source_clock_window"):
            group["usable_numeric_window_row_numbers"].append(row["source_row_number"])
    return list(groups.values())


def select_inventory(root, refresh):
    path = OUT + "/source_inventory.json"
    if (root / path).exists() and not refresh:
        return read(root, path)
    coverage = read(root, "data/tdx_bus_travel_time/coverage.json")
    receipts = {r["source_id"]: r for r in read(root, "raw_data/tdx_bus_travel_time/manifest.json")["records"]}
    selected = []
    for entry in coverage["records"]:
        row = dict(entry)
        row["source_receipts"] = (read(root, row["output_file"])["metadata"]["sources"]
                                   if row["status"] == "downloaded" else [receipts[row["source_id"]]])
        selected.append(row)
    return {"schema_version": "1.0", "dataset_id": "taipei_travel_time_source_inventory", "metadata": {
        "record_count": len(selected), "source_updated_at": None,
        "selection_time": datetime.now(ZoneInfo("Asia/Taipei")).isoformat(timespec="seconds"),
        "selection_source": "data/tdx_bus_travel_time/coverage.json",
        "selection_policy": "Retained attempted endpoint population; default offline rebuild keeps this inventory until --refresh-inventory.",
    }, "records": selected}


def build(root=ROOT, refresh=False):
    inventory = select_inventory(root, refresh)
    spec = read(root, SPEC)["components"]["schemas"]
    travel_props = spec["TravelTime"]["properties"]
    if not all("站牌代碼" in travel_props[f]["description"] for f in ("FromStopID", "ToStopID")):
        raise ValueError("premium stop ID semantics changed; keep join inconclusive")
    reference = read(root, REFERENCE)
    if reference["metadata"]["scope_status"] != "complete_published_endpoint_by_exhausted_page":
        raise ValueError("Taipei reference StopOfRoute is incomplete")
    catalog = read(root, CATALOG)
    route_catalog = {r["route_uid"]: r for r in catalog["records"]}
    by_url = {r["download_url"]: r for r in inventory["records"]}
    refs, by_key = [], defaultdict(list)
    all_ids = {s["stop_id"] for r in reference["records"] for s in r["stops"]}
    id_bridge = read(root, "data/official_cross_city/taipei_id_evidence/memberships.json.gz")
    bridge = {(r["tdx_model_record_number"], r["tdx_stop_record_number"]): r for r in id_bridge["records"]}
    for number, model in enumerate(reference["records"], 1):
        rid = f"taipei_stopofroute:{number}"
        stops = []
        for ordinal, stop in enumerate(model["stops"], 1):
            matched = bridge.get((number, ordinal))
            if (not matched or matched["status"] != "verified_explicit_source_identity"
                    or matched["stop_id"] != stop["stop_id"] or matched["stop_uid"] != stop["stop_uid"]
                    or matched["route_uid"] != model["route_uid"] or matched["subroute_uid"] != model["sub_route_uid"]
                    or matched["direction"] != str(model["direction"])
                    or matched["tdx_stop_sequence"] != stop["stop_sequence"]
                    or sorted(matched["operator_ids"]) != sorted(r["operator_id"] for r in model.get("operators", []))):
                raise ValueError("reference stop lacks verified Taipei local namespace evidence")
            stops.append({"source_record_number": ordinal, **{k: stop.get(k) for k in
                         ("stop_id", "stop_uid", "station_id", "stop_sequence", "stop_boarding")}})
        row = {"reference_model_id": rid, "source_record_number": number,
               **{k: model[k] for k in ("route_uid", "route_id", "sub_route_uid", "sub_route_id", "direction")},
               "operator_ids": [r["operator_id"] for r in model.get("operators", [])],
               "source_update_time": model.get("update_time"), "source_version_id": model.get("version_id"),
               "request_lineage_url": route_catalog[model["route_uid"]]["download_url"],
               "acquisition_status": acquisition_status(route_catalog[model["route_uid"]], by_url),
               "stops": stops}
        refs.append(row)
        by_key[key(row)].append(row)

    matched_evidence = defaultdict(list)
    instances = defaultdict(list)
    unmatched = []
    row_statuses = Counter()
    instance_summaries = []
    source_summaries = []
    source_value_counts = Counter()
    all_premium_models = all_premium_rows = 0
    for source in inventory["records"]:
        source_lineage = {"reference_route_uids_from_exact_request_url":
                          [r["route_uid"] for r in catalog["records"] if r["download_url"] == source["download_url"]]}
        if source["status"] != "downloaded":
            source_summaries.append({"source_id": source["source_id"], "availability_status": source.get("availability_status"),
                                     "source_model_count": None, "source_row_count": None, **source_lineage})
            continue
        doc = read(root, source["output_file"])
        if len(doc["records"]) != source["record_count"] or doc["metadata"]["sources"] != source["source_receipts"]:
            raise ValueError("saved inventory no longer matches source: " + source["source_id"])
        source_rows = sum(len(m["travel_times"]) for m in doc["records"])
        if source_rows != source["travel_time_row_count"]:
            raise ValueError("saved source row population differs: " + source["source_id"])
        source_summaries.append({"source_id": source["source_id"], "availability_status": source.get("availability_status"),
                                 "source_model_count": len(doc["records"]), "source_row_count": source_rows, **source_lineage})
        all_premium_models += len(doc["records"])
        all_premium_rows += source_rows
        for model_number, model in enumerate(doc["records"], 1):
            iid = f"{source['source_id']}:model:{model_number}"
            candidates = by_key.get(key(model), [])
            ref = candidates[0] if len(candidates) == 1 else None
            relation = ("matched_explicit_model_key" if ref else "ambiguous_reference_model_key" if candidates
                        else "reference_subroute_direction_not_published" if model["route_uid"] in route_catalog
                        else "outside_reference_route_uid")
            counters = Counter()
            tag_groups = defaultdict(lambda: {"intervals": {}, "row_count": 0, "status_counts": Counter()})
            for row_number, row in enumerate(model["travel_times"], 1):
                status, occurrences = pair_match(row, ref["stops"], all_ids) if ref else (relation, [])
                if row.get("service_tag") not in TAGS and status == "matched_unique_adjacent_pair":
                    status = "unsupported_service_tag"
                counters[status] += 1
                row_statuses[status] += 1
                source_value_counts["null_origin_rows"] += row.get("from_stop_id") is None
                source_value_counts["literal_zero_origin_rows"] += row.get("from_stop_id") == "0"
                source_value_counts["null_runtime_rows"] += row.get("run_time") is None
                source_value_counts["source_zero_runtime_rows"] += row.get("run_time") == 0
                source_value_counts["source_sequence_zero_rows"] += row.get("sequence") == 0
                tag = row.get("service_tag")
                clock = (row.get("start_time"), row.get("end_time"))
                group = tag_groups[tag]
                group["row_count"] += 1
                group["status_counts"][status] += 1
                if clock not in group["intervals"]:
                    group["intervals"][clock] = {"start_time": clock[0], "end_time": clock[1],
                        "clock_window_status": window_status(*clock), "source_row_count": 0, "row_status_counts": Counter(),
                        "matched_pair_occurrence_ids": set(), "numeric_runtime_pair_occurrence_ids": set(),
                        "missing_runtime_row_count": 0, "source_zero_runtime_row_count": 0}
                interval = group["intervals"][clock]
                interval["source_row_count"] += 1
                interval["row_status_counts"][status] += 1
                rt_status = runtime_status(row.get("run_time"))
                interval["missing_runtime_row_count"] += rt_status == "missing"
                interval["source_zero_runtime_row_count"] += rt_status == "source_zero"
                occurrence_ids = [f"{ref['reference_model_id']}:pair:{i + 1}-{i + 2}" for i in occurrences] if ref else []
                evidence = {"premium_instance_id": iid, "source_row_number": row_number, "source_sequence": row.get("sequence"),
                            "start_time": clock[0], "end_time": clock[1], "run_time": row.get("run_time"),
                            "runtime_status": rt_status, "clock_window_status": window_status(*clock)}
                if status == "matched_unique_adjacent_pair":
                    pid = occurrence_ids[0]
                    interval["matched_pair_occurrence_ids"].add(pid)
                    if rt_status in {"source_positive", "source_zero"} and window_status(*clock) == "valid_source_clock_window":
                        interval["numeric_runtime_pair_occurrence_ids"].add(pid)
                    matched_evidence[(pid, tag)].append(evidence)
                else:
                    unmatched.append({"premium_instance_id": iid, "source_id": source["source_id"],
                        "source_model_number": model_number, "source_row_number": row_number,
                        **{k: model.get(k) for k in ("route_uid", "sub_route_uid", "direction")},
                        "reference_model_ids": [r["reference_model_id"] for r in candidates],
                        "status": status, "candidate_pair_occurrence_ids": occurrence_ids,
                        "requires_source_review": status not in KNOWN_RELATIONS,
                        "source_review_flags": [] if status in KNOWN_RELATIONS else [status],
                        "source_row": row})
            tag_summaries = []
            for tag, group in tag_groups.items():
                interval_rows = []
                for interval in group["intervals"].values():
                    for field in ("matched_pair_occurrence_ids", "numeric_runtime_pair_occurrence_ids"):
                        interval[field.replace("_ids", "_count")] = len(interval.pop(field))
                    interval["row_status_counts"] = dict(interval["row_status_counts"])
                    interval["reference_adjacent_pair_count"] = max(0, len(ref["stops"]) - 1) if ref else None
                    interval_rows.append(interval)
                tag_summaries.append({"service_tag": tag, "service_tag_meaning": TAGS.get(tag),
                                      "source_row_count": group["row_count"], "row_status_counts": dict(group["status_counts"]),
                                      "intervals_in_source_first_appearance_order": interval_rows})
            instance = {"premium_instance_id": iid, "source_id": source["source_id"], "source_model_number": model_number,
                **{k: model.get(k) for k in ("route_uid", "sub_route_uid", "direction", "effective_date", "expire_date", "update_time", "validity_description")},
                "reference_model_ids": [r["reference_model_id"] for r in candidates], "model_relation_status": relation,
                "source_row_count": len(model["travel_times"]), "row_status_counts": dict(counters), "service_groups": tag_summaries,
                "requires_source_review": any(s not in KNOWN_RELATIONS for s in counters),
                "source_review_flags": [s for s in counters if s not in KNOWN_RELATIONS]}
            instance_summaries.append(instance)
            if ref:
                instances[ref["reference_model_id"]].append(instance)

    model_services, pairs = [], []
    for ref in refs:
        published = instances[ref["reference_model_id"]]
        for tag, meaning in TAGS.items():
            groups = [{"premium_instance_id": p["premium_instance_id"], "source_id": p["source_id"],
                       **{k: p[k] for k in ("effective_date", "expire_date", "update_time", "validity_description")},
                       **group} for p in published for group in p["service_groups"] if group["service_tag"] == tag]
            status = ("published_windows" if groups else "service_tag_not_observed_in_published_model" if published
                      else "model_not_observed_in_complete_response" if ref["acquisition_status"] == "requested_complete_response"
                      else ref["acquisition_status"])
            model_services.append({"reference_model_id": ref["reference_model_id"],
                **{k: ref[k] for k in ("route_uid", "sub_route_uid", "direction", "operator_ids")},
                "service_tag": tag, "service_tag_meaning": meaning, "status": status,
                "reference_adjacent_pair_count": max(0, len(ref["stops"]) - 1), "published_instances": groups,
                "requires_source_review": any(s not in KNOWN_RELATIONS for g in groups for s in g["row_status_counts"]),
                "source_review_flags": sorted({s for g in groups for s in g["row_status_counts"] if s not in KNOWN_RELATIONS}),
                "full_day_coverage_asserted": False})
            for i, (from_stop, to_stop) in enumerate(zip(ref["stops"], ref["stops"][1:]), 1):
                pid = f"{ref['reference_model_id']}:pair:{i}-{i + 1}"
                evidence = matched_evidence.get((pid, tag), [])
                usable = [e for e in evidence if e["runtime_status"] in {"source_positive", "source_zero"}
                          and e["clock_window_status"] == "valid_source_clock_window"]
                pair_status = ("matched" if usable else "matched_pair_runtime_or_window_unsupported" if evidence
                               else "pair_not_observed_in_published_windows" if groups else status)
                pairs.append({"pair_occurrence_id": pid, "reference_model_id": ref["reference_model_id"],
                    **{k: ref[k] for k in ("route_uid", "sub_route_uid", "direction")},
                    "from_source_record_number": i, "to_source_record_number": i + 1,
                    "from_stop_id": from_stop["stop_id"], "to_stop_id": to_stop["stop_id"],
                    "from_stop_sequence": from_stop["stop_sequence"], "to_stop_sequence": to_stop["stop_sequence"],
                    "from_stop_boarding": from_stop["stop_boarding"], "to_stop_boarding": to_stop["stop_boarding"],
                    "service_tag": tag, "status": pair_status, "source_observation_count": len(evidence),
                    "numeric_runtime_observation_count": len(usable), "source_rows_by_premium_instance": pack_pair_evidence(evidence),
                    "requires_source_review": pair_status in {"matched_pair_runtime_or_window_unsupported", "pair_not_observed_in_published_windows"},
                    "source_review_flags": [pair_status] if pair_status in {"matched_pair_runtime_or_window_unsupported", "pair_not_observed_in_published_windows"} else []})

    generated = datetime.now(ZoneInfo("Asia/Taipei")).isoformat(timespec="seconds")
    metadata = {"generated_at": generated, "source_updated_at": None, "statistical_period": None,
        "scope": "All retained premium response models accounted; coverage denominator is the full Taipei StopOfRoute reference.",
        "source_inventory": OUT + "/source_inventory.json", "reference_file": REFERENCE,
        "reference_sources": reference["metadata"]["sources"], "route_catalog": CATALOG,
        "premium_specification": SPEC,
        "premium_specification_url": "https://tdx.transportdata.tw/webapi/File/Swagger/V3/b22c71d8-5af0-4de2-ab0a-be28983fa051",
        "field_semantics": {k: travel_props[k]["description"] for k in ("Sequence", "FromStopID", "ToStopID", "ServiceTag", "StartTime", "EndTime", "RunTime")},
        "identity_key": ["route_uid", "sub_route_uid", "direction"],
        "namespace_evidence": {"file": "data/official_cross_city/taipei_id_evidence/memberships.json.gz",
                               "independent_review": "data/quality/taipei_id_evidence_review.json", "verified_reference_memberships": len(bridge)},
        "pair_policy": "Only exact ordered adjacent StopID pairs in source array order; repeated pairs are ambiguous. Sequence is retained but never used to infer alignment or an offset.",
        "pair_evidence_resolution": "For each pair source_rows_by_premium_instance group, premium_instance_id resolves through premium_models.json.gz to source_id and source_model_number (one-based). source_inventory.json resolves source_id to output_file; records[source_model_number-1].travel_times[source_row_number-1] retains original interval/RunTime/Sequence. usable_numeric_window_row_numbers is an ordered subset, never a reindexed or inferred row.",
        "service_tag_dictionary": TAGS, "units": {"run_time": "seconds", "start_time": "source civil clock HH:mm", "end_time": "source civil clock HH:mm including 24:00"},
        "license": {"name": "TDX資料使用條款", "url": "https://tdx.transportdata.tw/term"},
        "independent_review_status": "pending_separate_agent",
        "independent_review_report": "data/quality/travel_time_coverage_review.json",
        "requires_source_review": any(s not in KNOWN_RELATIONS for s in row_statuses),
        "limitations": ["版本不同可造成同一模型部分站對不相鄰或ID缺列；不以Sequence偏移、名稱或StationID補配。",
            "ServiceTag 0為周末，1為平日；不是完整法定假日日曆。有效期與來源UpdateTime原樣分開保留。",
            "匹配只證明所列原時段有發布估值，不是全日、三年歷史或實際當日旅行時間。",
            "首站null起站列不構成站對；null RunTime不是0，來源明示0另列。",
            "未取用、來源明示無資料、模型/服務日/站對未發布及ID不支援分開，均不能解讀停駛。",
            "保留原始站序、迴圈及上下客碼；有行駛時間不等於端點可上下客。premium未提供業者，不按業者複製或加總模型。",
            "各來源模型及有效期獨立，不選最新值或合併為單一完整班次；相同站對多次出現不按Sequence任配。"],
    }
    summary = {"attempted_source_count": len(inventory["records"]), "source_models": all_premium_models,
        "source_rows": all_premium_rows, "reference_route_uid_count": len({r["route_uid"] for r in refs}),
        "reference_models": len(refs), "reference_stop_memberships": sum(len(r["stops"]) for r in refs),
        "reference_adjacent_pair_occurrences": sum(max(0, len(r["stops"]) - 1) for r in refs),
        "row_status_counts": dict(row_statuses), "premium_model_relation_counts": dict(Counter(r["model_relation_status"] for r in instance_summaries)),
        "source_value_counts": dict(source_value_counts),
        "model_service_status_counts": dict(Counter(r["status"] for r in model_services)),
        "pair_service_status_counts": dict(Counter(r["status"] for r in pairs)), "unmatched_or_origin_placeholder_rows": len(unmatched),
        "unmatched_rows_are_not_usable_time_costs": True,
        "source_population": source_summaries}
    def envelope(dataset_id, rows, grain):
        return {"schema_version": "1.0", "dataset_id": dataset_id,
                "metadata": {**metadata, "record_count": len(rows), "grain": grain}, "records": rows}
    inventory["metadata"]["used_in_generated_at"] = generated
    inventory["metadata"].update({"statistical_period": None, "grain": "one retained attempted endpoint and original HTTP receipts",
        "license": metadata["license"], "independent_review_status": "pending_separate_agent",
        "independent_review_report": "data/quality/travel_time_coverage_review.json"})
    outputs = {
        "source_inventory.json": inventory,
        "reference_models.json.gz": envelope("taipei_travel_time_reference_models", refs, "one complete published StopOfRoute model with source-ordered stops"),
        "model_service_coverage.json.gz": envelope("taipei_travel_time_model_service_coverage", model_services, "one reference model and expected service tag; nested independent effective-period/clock-window evidence"),
        "pair_coverage.json.gz": envelope("taipei_travel_time_pair_service_coverage", pairs, "one source adjacent-pair occurrence and expected service tag; exact source row numbers grouped by premium model instance"),
        "unmatched_rows.json.gz": envelope("taipei_travel_time_unmatched_rows", unmatched, "one premium row that cannot be assigned to a unique current adjacent occurrence, including null-origin first-stop rows"),
        "summary.json": envelope("taipei_travel_time_coverage_summary", [summary], "one frozen retained source population and full reference denominator"),
        "premium_models.json.gz": envelope("taipei_travel_time_premium_model_accounting", instance_summaries, "one retained premium model including outside-reference and zero-row models"),
    }
    for name, doc in outputs.items():
        write(root, OUT + "/" + name, doc)
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--refresh-inventory", action="store_true")
    args = parser.parse_args()
    summary = build(args.root, args.refresh_inventory)
    print(json.dumps({k: v for k, v in summary.items() if k != "source_population"}))


if __name__ == "__main__":
    main()
