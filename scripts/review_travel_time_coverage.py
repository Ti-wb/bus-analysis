"""Independently reconcile frozen premium travel rows with basic stop arrays."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime
import gzip
import json
import math
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
DIRECTORY = Path("data/official_cross_city/travel_time_coverage")
REPORT = Path("data/quality/travel_time_coverage_review.json")
NAMES = {
    "source_inventory.json": "taipei_travel_time_source_inventory",
    "reference_models.json.gz": "taipei_travel_time_reference_models",
    "premium_models.json.gz": "taipei_travel_time_premium_model_accounting",
    "model_service_coverage.json.gz": "taipei_travel_time_model_service_coverage",
    "pair_coverage.json.gz": "taipei_travel_time_pair_service_coverage",
    "unmatched_rows.json.gz": "taipei_travel_time_unmatched_rows",
    "summary.json": "taipei_travel_time_coverage_summary",
}
TAGS = {"0": "weekend", "1": "weekdays"}
SUPPORTED_RELATIONS = {"matched_unique_adjacent_pair", "null_origin_at_current_first_stop", "outside_reference_route_uid"}
MODEL_FIELDS = {
    "EffectiveDate": "effective_date", "ExpireDate": "expire_date", "UpdateTime": "update_time",
    "UpdateInterval": "update_interval", "ValidityDescription": "validity_description", "RouteUID": "route_uid",
    "RouteID": "route_id", "RouteName": "route_name", "SubRouteUID": "sub_route_uid", "SubRouteID": "sub_route_id",
    "SubRouteName": "sub_route_name", "Direction": "direction",
}
ROW_FIELDS = {
    "Sequence": "sequence", "FromStopID": "from_stop_id", "ToStopID": "to_stop_id", "RunTime": "run_time",
    "StartTime": "start_time", "EndTime": "end_time", "ServiceTag": "service_tag",
}


def read_document(path):
    body = path.read_bytes()
    return json.loads(gzip.decompress(body) if path.suffix == ".gz" else body)


def source_id(value):
    return None if value is None or value == "" else str(value)


def raw_model_key(model):
    direction = model.get("Direction")
    if direction is not None and type(direction) is not int:
        raise ValueError("Direction must retain its source integer type")
    return source_id(model.get("RouteUID")), source_id(model.get("SubRouteUID")), direction


def adjacent_occurrences(model):
    """Use array occurrences, including repeats; never index by StopSequence."""
    pairs = defaultdict(list)
    for number, (start, end) in enumerate(zip(model.get("Stops", []), model.get("Stops", [])[1:]), 1):
        pairs[(source_id(start.get("StopID")), source_id(end.get("StopID")))].append(number)
    return dict(pairs)


def classify_raw_pair(model, travel_row):
    start = source_id(travel_row.get("FromStopID"))
    end = source_id(travel_row.get("ToStopID"))
    if start is None:
        return "missing_from_stop_id", []
    if end is None:
        return "missing_to_stop_id", []
    occurrences = adjacent_occurrences(model).get((start, end), [])
    if occurrences:
        return "adjacent_explicit_stop_ids", occurrences
    stop_ids = {source_id(stop.get("StopID")) for stop in model.get("Stops", [])}
    if start not in stop_ids or end not in stop_ids:
        return "stop_id_absent_from_reference_model", []
    return "explicit_stop_ids_not_adjacent", []


def clock_minutes(value):
    if not isinstance(value, str) or not re.fullmatch(r"(?:[0-9]|[01][0-9]|2[0-4]):[0-5][0-9]", value):
        raise ValueError("Invalid source HH:mm clock")
    hour, minute = map(int, value.split(":"))
    if hour == 24 and minute:
        raise ValueError("Only 24:00 is a valid end-of-day boundary")
    return 60 * hour + minute


def strict_equal(left, right):
    if type(left) is not type(right):
        return False
    if isinstance(right, dict):
        return left.keys() == right.keys() and all(strict_equal(left[key], value) for key, value in right.items())
    if isinstance(right, list):
        return len(left) == len(right) and all(strict_equal(a, b) for a, b in zip(left, right))
    return left == right


def normalize_raw_model(model):
    """Explicit documented field map, independent of either collector."""
    output = {target: (source_id(model.get(source)) if source.endswith(("UID", "ID")) else model.get(source))
              for source, target in MODEL_FIELDS.items()}
    output["travel_times"] = []
    for source in model.get("TravelTimes", []):
        output["travel_times"].append({target: (source_id(source.get(field)) if field in {"FromStopID", "ToStopID", "ServiceTag"}
                                               else source.get(field)) for field, target in ROW_FIELDS.items()})
    return output


def read_receipt(root, receipt):
    path = root / receipt["raw_file"]
    stored = path.read_bytes()
    compressed = receipt.get("storage_encoding") == "gzip"
    if compressed and receipt.get("stored_bytes") != len(stored):
        raise ValueError("stored byte count differs: " + str(path))
    if path.suffix == ".gz" and not compressed:
        raise ValueError("gzip raw receipt does not declare encoding")
    body = gzip.decompress(stored) if compressed else stored
    if receipt.get("bytes") != len(body):
        raise ValueError("decoded byte count differs: " + str(path))
    if receipt.get("http_status") != 200:
        raise ValueError("Retained source was not HTTP 200")
    return json.loads(body)


def full_pair_status(reference, row, all_ids):
    start, end = source_id(row.get("FromStopID")), source_id(row.get("ToStopID"))
    pairs = adjacent_occurrences(reference)
    occurrences = pairs.get((start, end), []) if start is not None and end is not None else []
    if occurrences:
        return ("matched_unique_adjacent_pair" if len(occurrences) == 1 else "ambiguous_repeated_adjacent_pair"), occurrences
    ids = [source_id(stop.get("StopID")) for stop in reference.get("Stops", [])]
    if end is None:
        return "missing_to_stop_id", []
    if start is None:
        return ("null_origin_at_current_first_stop" if ids and end == ids[0] else
                "null_origin_at_current_nonfirst_stop" if end in ids else "null_origin_stop_absent_from_current_model"), []
    if start == "0":
        return "unsupported_literal_zero_origin", []
    if start in ids and end in ids:
        return "same_model_stop_ids_not_adjacent", []
    return ("stop_ids_known_only_in_other_models" if start in all_ids and end in all_ids else
            "stop_id_absent_from_current_snapshot"), []


def value_status(value):
    if value is None:
        return "missing"
    if type(value) not in {int, float} or not math.isfinite(value) or value < 0:
        return "unsupported_source_value"
    return "source_zero" if value == 0 else "source_positive"


def window_status(start, end):
    try:
        valid = clock_minutes(start) < clock_minutes(end)
    except ValueError:
        valid = False
    return "valid_source_clock_window" if valid else "unsupported_source_clock_window"


def usable(observation):
    return observation["runtime_status"] in {"source_zero", "source_positive"} and observation["clock_window_status"] == "valid_source_clock_window"


def source_row_references(observations):
    groups = {}
    for observation in observations:
        iid, ordinal = observation["premium_instance_id"], observation["source_row_number"]
        if type(ordinal) is not int or ordinal < 1:
            raise ValueError("Canonical source row ordinals must be one-based integers")
        group = groups.setdefault(iid, {"premium_instance_id": iid, "source_row_numbers": [], "usable_numeric_window_row_numbers": []})
        if ordinal in group["source_row_numbers"]:
            raise ValueError("One canonical source row cannot occur twice in a pair group")
        group["source_row_numbers"].append(ordinal)
        if usable(observation):
            group["usable_numeric_window_row_numbers"].append(ordinal)
    return list(groups.values())


def require_equal(actual, expected, label):
    if not strict_equal(actual, expected):
        raise ValueError("Independent raw reconstruction differs: " + label)


def audit_failed_response(source, receipt, raw):
    """Classify the retained error body, without creating observation data."""
    require_equal(source.get("status"), "failed", "failed source status")
    require_equal(receipt.get("status"), "failed", "failed receipt status")
    require_equal(receipt.get("http_status"), 200, "complete HTTP 200 error receipt")
    message = raw.get("message") if isinstance(raw, dict) else None
    no_data = isinstance(message, str) and re.fullmatch(r"目前沒有.*公車路線資料", message) is not None
    if no_data:
        require_equal(source.get("availability_status"), "published_no_data_message", "official no-data availability class")
        require_equal(message, source["upstream_response_message"], "official no-data message")
    else:
        if not isinstance(message, str) or not receipt.get("complete_unparsed_response_retained") or not receipt.get("raw_parse_error"):
            raise ValueError("Technical source failure lacks its complete original error response/parse diagnostic")
        require_equal(source.get("availability_status"), "technical_failure", "upstream error remains a technical failure")
        require_equal(source.get("failure_reason"), "unusable_response_format_or_contract", "upstream error contract failure")
    require_equal(receipt.get("availability_status"), source["availability_status"], "failure receipt availability class")
    if source.get("output_file") is not None:
        raise ValueError("A failed source must not supply an observation file")
    return message


def compare_rows(actual, expected, label):
    if len(actual) != len(expected):
        raise ValueError(f"{label}: record count {len(actual)} != {len(expected)}")
    for number, (left, right) in enumerate(zip(actual, expected), 1):
        require_equal(left, right, f"{label} record {number}")


def reconstruct(root, documents):
    inventory = documents["source_inventory.json"]
    metadata = documents["reference_models.json.gz"]["metadata"]
    raw_reference, expected_skip = [], 0
    for receipt in metadata["reference_sources"]:
        if receipt["scope"] != "Taipei" or receipt["service"] != "StopOfRoute" or receipt["skip"] != expected_skip:
            raise ValueError("Reference pagination/scope is inconsistent")
        page = read_receipt(root, receipt)
        if len(page) != receipt["record_count"] or len(page) > receipt["top"]:
            raise ValueError("Reference page count differs")
        raw_reference.extend(page)
        expected_skip += receipt["top"]
    if not metadata["reference_sources"] or len(page) >= receipt["top"]:
        raise ValueError("Reference lacks an exhausted final page")
    raw_route_catalog = read_document(root / metadata["route_catalog"])
    urls = {row["route_uid"]: row["download_url"] for row in raw_route_catalog["records"]}
    receipts_by_url = {}
    for source in inventory["records"]:
        if source["download_url"] in receipts_by_url:
            raise ValueError("Frozen source inventory has duplicate request URLs")
        receipts_by_url[source["download_url"]] = source
    reference_rows, reference_by_key = [], defaultdict(list)
    all_ids = {source_id(stop.get("StopID")) for model in raw_reference for stop in model.get("Stops", [])}
    bridge_doc = read_document(root / metadata["namespace_evidence"]["file"])
    bridge = {(row["tdx_model_record_number"], row["tdx_stop_record_number"]): row for row in bridge_doc["records"]}
    for ordinal, model in enumerate(raw_reference, 1):
        route_uid, subroute_uid, direction = raw_model_key(model)
        receipt = receipts_by_url.get(urls[route_uid])
        acquisition = "unrequested" if receipt is None else "source_published_no_data" if receipt.get("availability_status") == "published_no_data_message" else "request_technical_failure" if receipt["status"] != "downloaded" else "requested_complete_response"
        stops = []
        for stop_number, stop in enumerate(model.get("Stops", []), 1):
            ids = {"stop_id": source_id(stop.get("StopID")), "stop_uid": source_id(stop.get("StopUID")), "station_id": source_id(stop.get("StationID"))}
            local = bridge.get((ordinal, stop_number), {})
            if local.get("status") != "verified_explicit_source_identity" or local.get("stop_id") != ids["stop_id"]:
                raise ValueError("Stop lacks previously reviewed explicit local namespace identity")
            stops.append({"source_record_number": stop_number, **ids, "stop_sequence": stop.get("StopSequence"), "stop_boarding": stop.get("StopBoarding")})
        row = {"reference_model_id": f"taipei_stopofroute:{ordinal}", "source_record_number": ordinal,
               "route_uid": route_uid, "route_id": source_id(model.get("RouteID")), "sub_route_uid": subroute_uid,
               "sub_route_id": source_id(model.get("SubRouteID")), "direction": direction,
               "operator_ids": [source_id(operator.get("OperatorID")) for operator in model.get("Operators", [])],
               "source_update_time": model.get("UpdateTime"), "source_version_id": source_id(model.get("VersionID")),
               "request_lineage_url": urls[route_uid], "acquisition_status": acquisition, "stops": stops}
        reference_rows.append(row)
        reference_by_key[(route_uid, subroute_uid, direction)].append((row, model))
    require_equal(len(bridge), sum(len(row["stops"]) for row in reference_rows), "verified namespace denominator")
    expected = {"reference_models.json.gz": reference_rows}
    models, unmatched, source_counts = [], [], []
    numeric_rows_by_instance, failure_diagnostics = {}, []
    matched = defaultdict(list)
    instances_by_reference = defaultdict(list)
    row_counts, source_values = Counter(), Counter()
    seen_source_ids = set()
    for source in inventory["records"]:
        sid = source["source_id"]
        if sid in seen_source_ids:
            raise ValueError("Duplicate frozen source_id")
        seen_source_ids.add(sid)
        if len(source["source_receipts"]) != 1:
            raise ValueError("Expected one whole unpaginated premium response receipt")
        receipt = source["source_receipts"][0]
        require_equal(receipt["source_id"], sid, "receipt source ID")
        require_equal(receipt["download_url"], source["download_url"], "receipt request lineage")
        raw = read_receipt(root, receipt)
        if source["status"] != "downloaded":
            message = audit_failed_response(source, receipt, raw)
            failure_diagnostics.append({"source_id": sid, "availability_status": source["availability_status"],
                                        "reference_route_uids_from_exact_request_url": [uid for uid, url in urls.items() if url == source["download_url"]],
                                        "source_receipt": receipt, "upstream_error_message": message})
            source_counts.append({"source_id": sid, "availability_status": source.get("availability_status"), "source_model_count": None, "source_row_count": None,
                                  "reference_route_uids_from_exact_request_url": [uid for uid, url in urls.items() if url == source["download_url"]]})
            continue
        if not isinstance(raw, list):
            raise ValueError("Successful premium source is not a model array")
        normalized = read_document(root / source["output_file"])
        require_equal(normalized["metadata"]["sources"], source["source_receipts"], "frozen normalized source receipt")
        require_equal(normalized["records"], [normalize_raw_model(model) for model in raw], "all normalized premium model/row fields " + sid)
        total = sum(len(model.get("TravelTimes", [])) for model in raw)
        require_equal(source["record_count"], len(raw), "inventory premium model count")
        require_equal(source["travel_time_row_count"], total, "inventory premium row count")
        require_equal(source["missing_run_time_count"], sum(row.get("RunTime") is None for model in raw for row in model.get("TravelTimes", [])), "inventory null runtime count")
        source_counts.append({"source_id": sid, "availability_status": source.get("availability_status"), "source_model_count": len(raw), "source_row_count": total,
                              "reference_route_uids_from_exact_request_url": [uid for uid, url in urls.items() if url == source["download_url"]]})
        for model_number, model in enumerate(raw, 1):
            iid = f"{sid}:model:{model_number}"
            numeric_rows_by_instance[iid] = sum(value_status(row.get("RunTime")) in {"source_positive", "source_zero"} for row in model.get("TravelTimes", []))
            normalized_model = normalize_raw_model(model)
            identity = raw_model_key(model)
            references = reference_by_key.get(identity, [])
            ref, raw_ref = references[0] if len(references) == 1 else (None, None)
            relation = "matched_explicit_model_key" if ref else "ambiguous_reference_model_key" if references else "reference_subroute_direction_not_published" if identity[0] in urls else "outside_reference_route_uid"
            groups, counts = {}, Counter()
            for row_number, source_row in enumerate(model.get("TravelTimes", []), 1):
                status, occurrences = full_pair_status(raw_ref, source_row, all_ids) if ref else (relation, [])
                counts[status] += 1
                row_counts[status] += 1
                row = normalized_model["travel_times"][row_number - 1]
                source_values["null_origin_rows"] += int(row["from_stop_id"] is None)
                source_values["literal_zero_origin_rows"] += int(row["from_stop_id"] == "0")
                source_values["null_runtime_rows"] += int(row["run_time"] is None)
                source_values["source_zero_runtime_rows"] += int(row["run_time"] == 0)
                source_values["source_sequence_zero_rows"] += int(row["sequence"] == 0)
                tag, interval_key = row["service_tag"], (row["start_time"], row["end_time"])
                group = groups.setdefault(tag, {"service_tag": tag, "service_tag_meaning": TAGS.get(tag), "source_row_count": 0, "row_status_counts": Counter(), "intervals_in_source_first_appearance_order": {}})
                group["source_row_count"] += 1
                group["row_status_counts"][status] += 1
                intervals = group["intervals_in_source_first_appearance_order"]
                interval = intervals.setdefault(interval_key, {"start_time": row["start_time"], "end_time": row["end_time"], "clock_window_status": window_status(*interval_key), "source_row_count": 0, "row_status_counts": Counter(), "matched": set(), "numeric": set(), "missing_runtime_row_count": 0, "source_zero_runtime_row_count": 0})
                interval["source_row_count"] += 1
                interval["row_status_counts"][status] += 1
                runtime = value_status(row["run_time"])
                interval["missing_runtime_row_count"] += int(runtime == "missing")
                interval["source_zero_runtime_row_count"] += int(runtime == "source_zero")
                candidate_ids = [f"{ref['reference_model_id']}:pair:{number}-{number + 1}" for number in occurrences] if ref else []
                observation = {"premium_instance_id": iid, "source_row_number": row_number, "source_sequence": row["sequence"], "start_time": row["start_time"], "end_time": row["end_time"], "run_time": row["run_time"], "runtime_status": runtime, "clock_window_status": window_status(*interval_key)}
                if status == "matched_unique_adjacent_pair":
                    pair_id = candidate_ids[0]
                    matched[(pair_id, tag)].append(observation)
                    interval["matched"].add(pair_id)
                    if usable(observation):
                        interval["numeric"].add(pair_id)
                else:
                    unmatched.append({"premium_instance_id": iid, "source_id": sid, "source_model_number": model_number, "source_row_number": row_number,
                                      "route_uid": identity[0], "sub_route_uid": identity[1], "direction": identity[2],
                                      "reference_model_ids": [reference[0]["reference_model_id"] for reference in references], "status": status,
                                      "candidate_pair_occurrence_ids": candidate_ids, "source_row": row,
                                      "requires_source_review": status not in SUPPORTED_RELATIONS,
                                      "source_review_flags": [] if status in SUPPORTED_RELATIONS else [status]})
            services = []
            for group in groups.values():
                intervals = []
                for interval in group["intervals_in_source_first_appearance_order"].values():
                    interval["matched_pair_occurrence_count"] = len(interval.pop("matched"))
                    interval["numeric_runtime_pair_occurrence_count"] = len(interval.pop("numeric"))
                    interval["row_status_counts"] = dict(interval["row_status_counts"])
                    interval["reference_adjacent_pair_count"] = len(raw_ref.get("Stops", [])) - 1 if ref else None
                    intervals.append(interval)
                group["intervals_in_source_first_appearance_order"] = intervals
                group["row_status_counts"] = dict(group["row_status_counts"])
                services.append(group)
            instance = {"premium_instance_id": iid, "source_id": sid, "source_model_number": model_number,
                        **{name: normalized_model[name] for name in ("route_uid", "sub_route_uid", "direction", "effective_date", "expire_date", "update_time", "validity_description")},
                        "reference_model_ids": [reference[0]["reference_model_id"] for reference in references], "model_relation_status": relation,
                        "source_row_count": len(model.get("TravelTimes", [])), "row_status_counts": dict(counts), "service_groups": services,
                        "requires_source_review": any(status not in SUPPORTED_RELATIONS for status in counts),
                        "source_review_flags": [status for status in counts if status not in SUPPORTED_RELATIONS]}
            models.append(instance)
            if ref:
                instances_by_reference[ref["reference_model_id"]].append(instance)
    model_services, pair_services = [], []
    for reference in reference_rows:
        rid = reference["reference_model_id"]
        instances = instances_by_reference[rid]
        for tag, meaning in TAGS.items():
            published = []
            for instance in instances:
                for group in instance["service_groups"]:
                    if group["service_tag"] == tag:
                        published.append({name: instance[name] for name in ("premium_instance_id", "source_id", "effective_date", "expire_date", "update_time", "validity_description")} | group)
            status = "published_windows" if published else "service_tag_not_observed_in_published_model" if instances else "model_not_observed_in_complete_response" if reference["acquisition_status"] == "requested_complete_response" else reference["acquisition_status"]
            model_services.append({"reference_model_id": rid, **{name: reference[name] for name in ("route_uid", "sub_route_uid", "direction", "operator_ids")},
                                   "service_tag": tag, "service_tag_meaning": meaning, "status": status, "reference_adjacent_pair_count": max(0, len(reference["stops"]) - 1), "published_instances": published,
                                   "requires_source_review": any(status not in SUPPORTED_RELATIONS for group in published for status in group["row_status_counts"]),
                                   "source_review_flags": sorted({status for group in published for status in group["row_status_counts"] if status not in SUPPORTED_RELATIONS}),
                                   "full_day_coverage_asserted": False})
            for number, (start, end) in enumerate(zip(reference["stops"], reference["stops"][1:]), 1):
                pid = f"{rid}:pair:{number}-{number + 1}"
                evidence = matched[(pid, tag)]
                numeric = sum(usable(observation) for observation in evidence)
                pair_status = "matched" if numeric else "matched_pair_runtime_or_window_unsupported" if evidence else "pair_not_observed_in_published_windows" if published else status
                pair_services.append({"pair_occurrence_id": pid, "reference_model_id": rid, **{name: reference[name] for name in ("route_uid", "sub_route_uid", "direction")},
                                      "from_source_record_number": number, "to_source_record_number": number + 1, "from_stop_id": start["stop_id"], "to_stop_id": end["stop_id"],
                                      "from_stop_sequence": start["stop_sequence"], "to_stop_sequence": end["stop_sequence"], "from_stop_boarding": start["stop_boarding"], "to_stop_boarding": end["stop_boarding"],
                                      "service_tag": tag, "status": pair_status, "source_observation_count": len(evidence), "numeric_runtime_observation_count": numeric, "source_rows_by_premium_instance": source_row_references(evidence),
                                      "requires_source_review": pair_status in {"matched_pair_runtime_or_window_unsupported", "pair_not_observed_in_published_windows"},
                                      "source_review_flags": [pair_status] if pair_status in {"matched_pair_runtime_or_window_unsupported", "pair_not_observed_in_published_windows"} else []})
    summary = {"attempted_source_count": len(inventory["records"]), "source_models": len(models), "source_rows": sum(model["source_row_count"] for model in models),
               "reference_route_uid_count": len({model["route_uid"] for model in reference_rows}), "reference_models": len(reference_rows),
               "reference_stop_memberships": sum(len(model["stops"]) for model in reference_rows), "reference_adjacent_pair_occurrences": sum(max(0, len(model["stops"]) - 1) for model in reference_rows),
               "row_status_counts": dict(row_counts), "premium_model_relation_counts": dict(Counter(model["model_relation_status"] for model in models)),
               "source_value_counts": dict(source_values),
               "model_service_status_counts": dict(Counter(row["status"] for row in model_services)), "pair_service_status_counts": dict(Counter(row["status"] for row in pair_services)),
               "unmatched_or_origin_placeholder_rows": len(unmatched), "unmatched_rows_are_not_usable_time_costs": True, "source_population": source_counts}
    expected.update({"premium_models.json.gz": models, "unmatched_rows.json.gz": unmatched, "model_service_coverage.json.gz": model_services, "pair_coverage.json.gz": pair_services, "summary.json": [summary]})
    reference_uids = {row["route_uid"] for row in reference_rows}
    reference_instances = [model for model in models if model["model_relation_status"] == "matched_explicit_model_key"]
    returned_uids = {model["route_uid"] for model in models if model["route_uid"] in reference_uids}
    numeric_uids = {model["route_uid"] for model in models if model["route_uid"] in reference_uids and numeric_rows_by_instance[model["premium_instance_id"]]}
    usable_pairs = [pair for pair in pair_services if pair["status"] == "matched"]
    usable_uids = {pair["route_uid"] for pair in usable_pairs}
    usable_instances = {group["premium_instance_id"] for pair in usable_pairs for group in pair["source_rows_by_premium_instance"] if group["usable_numeric_window_row_numbers"]}
    empty_reference = [model for model in reference_instances if model["source_row_count"] == 0]
    nonempty_all_null = [model for model in reference_instances if model["source_row_count"] > 0 and not numeric_rows_by_instance[model["premium_instance_id"]]
                         and sum(interval["missing_runtime_row_count"] for group in model["service_groups"] for interval in group["intervals_in_source_first_appearance_order"]) == model["source_row_count"]]
    first_null_only = [model for model in nonempty_all_null if set(model["row_status_counts"]) == {"null_origin_at_current_first_stop"}]

    def identities(selected):
        return [{key: model[key] for key in ("premium_instance_id", "source_id", "source_model_number", "route_uid", "sub_route_uid", "direction", "source_row_count", "row_status_counts")} for model in selected]

    def describe_uids(selected):
        names = {row["route_uid"]: row["route_name"] for row in raw_route_catalog["records"]}
        return [{"route_uid": uid, "route_name": names[uid],
                 "returned_model_count": sum(model["route_uid"] == uid for model in models),
                 "source_row_count": sum(model["source_row_count"] for model in models if model["route_uid"] == uid),
                 "source_numeric_runtime_row_count": sum(numeric_rows_by_instance[model["premium_instance_id"]] for model in models if model["route_uid"] == uid),
                 "usable_unique_adjacent_pair_service_count": sum(pair["route_uid"] == uid for pair in usable_pairs)} for uid in sorted(selected)]

    for failure in failure_diagnostics:
        failed_uids = set(failure["reference_route_uids_from_exact_request_url"])
        affected_models = [row for row in model_services if row["route_uid"] in failed_uids]
        affected_pairs = [row for row in pair_services if row["route_uid"] in failed_uids]
        expected_failure_status = "source_published_no_data" if failure["availability_status"] == "published_no_data_message" else "request_technical_failure"
        if any(row["status"] != expected_failure_status for row in affected_models + affected_pairs):
            raise ValueError("Failed source was misclassified as unrequested or usable")
        failure["reference_model_service_row_count"] = len(affected_models)
        failure["reference_pair_service_row_count"] = len(affected_pairs)
        failure["propagated_status"] = expected_failure_status
    diagnostics = {
        "reference_route_uid_count": len(reference_uids), "returned_model_route_uid_count": len(returned_uids), "returned_model_route_uids": sorted(returned_uids),
        "source_numeric_runtime_route_uid_count": len(numeric_uids), "source_numeric_runtime_route_uids": sorted(numeric_uids),
        "usable_unique_adjacent_pair_route_uid_count": len(usable_uids), "usable_unique_adjacent_pair_route_uids": sorted(usable_uids),
        "usable_unique_adjacent_pair_service_count": len(usable_pairs), "usable_premium_instance_count": len(usable_instances),
        "returned_models_without_numeric_runtime_uids": describe_uids(returned_uids - numeric_uids),
        "numeric_runtime_without_usable_unique_adjacent_pair_uids": describe_uids(numeric_uids - usable_uids),
        "reference_uids_without_returned_models": sorted(reference_uids - returned_uids),
        "empty_reference_model_count": len(empty_reference), "empty_reference_model_route_uid_count": len({model["route_uid"] for model in empty_reference}),
        "empty_reference_models": identities(empty_reference), "empty_all_scope_model_count": sum(model["source_row_count"] == 0 for model in models),
        "nonempty_all_null_runtime_reference_models": identities(nonempty_all_null), "only_current_first_stop_null_reference_models": identities(first_null_only),
        "failed_source_propagation": failure_diagnostics,
        "definitions": {"source_numeric_runtime": "Finite nonnegative original RunTime; includes explicit source zero and does not certify IDs or clock windows.",
                        "usable_unique_adjacent_pair": "Exact reference model key, unique ordered adjacent explicit StopIDs, finite nonnegative original RunTime and valid original increasing HH:mm window. No boarding or actual-operation claim.",
                        "empty_model": "TravelTimes has zero original rows; never converted into a zero cost.", "nonempty_all_null_runtime": "At least one original row and every RunTime is null; separate from an empty array."}}
    return expected, summary, diagnostics


def review(root=ROOT):
    documents = {name: read_document(root / DIRECTORY / name) for name in NAMES}
    generation = documents["summary.json"]["metadata"]["generated_at"]
    expected, summary, diagnostics = reconstruct(root, documents)
    spec = read_document(root / "raw_data/tdx_bus_travel_time/specifications/travel_time.json")["components"]["schemas"]["TravelTime"]["properties"]
    results = []
    for name, dataset_id in NAMES.items():
        doc, errors = documents[name], []
        stored = (root / DIRECTORY / name).read_bytes()
        decoded_bytes = len(gzip.decompress(stored) if name.endswith(".gz") else stored)
        try:
            if decoded_bytes > 128 * 1024 * 1024:
                raise ValueError("Decoded dataset exceeds the unchanged 128 MiB catalog input budget")
            require_equal(doc["dataset_id"], dataset_id, "dataset ID")
            require_equal(doc["metadata"]["record_count"], len(doc["records"]), "declared record count")
            if name == "source_inventory.json":
                require_equal(doc["metadata"]["used_in_generated_at"], generation, "frozen inventory generation")
            else:
                require_equal(doc["metadata"]["generated_at"], generation, "common generation")
                require_equal(doc["metadata"]["service_tag_dictionary"], TAGS, "weekend/weekday meaning")
                require_equal(doc["metadata"]["identity_key"], ["route_uid", "sub_route_uid", "direction"], "exact model identity")
                require_equal(doc["metadata"]["license"]["url"], "https://tdx.transportdata.tw/term", "license URL")
                require_equal(doc["metadata"]["field_semantics"], {field: spec[field]["description"] for field in ("Sequence", "FromStopID", "ToStopID", "ServiceTag", "StartTime", "EndTime", "RunTime")}, "official field semantics")
                require_equal(doc["metadata"]["units"]["run_time"], "seconds", "travel time unit")
                require_equal(doc["metadata"]["source_updated_at"], None, "no invented aggregate source update time")
                require_equal(doc["metadata"]["statistical_period"], None, "no collapsed source effective period")
                require_equal(doc["metadata"]["requires_source_review"], any(status not in SUPPORTED_RELATIONS for status in summary["row_status_counts"]), "unresolved source review marker")
                compare_rows(doc["records"], expected[name], name)
            require_equal(doc["metadata"]["independent_review_report"], str(REPORT), "review report pointer")
        except (KeyError, ValueError, TypeError) as error:
            errors.append(str(error))
        results.append({"output_file": str(DIRECTORY / name), "dataset_id": dataset_id,
                        "status": "fail" if errors else "pass_with_source_limitations", "record_count": len(doc["records"]),
                        "stored_bytes": len(stored), "decoded_bytes": decoded_bytes,
                        "reviewed_generated_at": generation, "errors": errors})
    if "0: 周末weekend; 1: 平日weekdays" not in spec["ServiceTag"]["description"]:
        raise ValueError("Saved official ServiceTag semantics differ")
    if not all("站牌代碼" in spec[field]["description"] for field in ("FromStopID", "ToStopID")):
        raise ValueError("Saved official pair IDs are not documented stop IDs")
    for name, doc in documents.items():
        require_equal(read_document(root / DIRECTORY / name), doc, "snapshot changed while reviewing " + name)
    output = {"schema_version": "1.0", "dataset_id": "taipei_travel_time_coverage_independent_review", "metadata": {
        "reviewed_at": datetime.now().astimezone().isoformat(timespec="seconds"), "reviewer_agent": "/root/crosswalk",
        "producer_imported": False, "network_requests": 0, "source_inventory": str(DIRECTORY / "source_inventory.json"),
        "reviewed_generated_at": generation, "status": "fail" if any(row["errors"] for row in results) else "pass_with_source_limitations",
        "method": "Decode frozen raw receipts with byte checks; independently map all premium fields and reconstruct current basic raw array adjacency, model/tag/period/window accounting, row identities and totals. No producer imports or Sequence alignment.",
        "source_scope_count": summary["attempted_source_count"], "premium_model_count": summary["source_models"], "premium_row_count": summary["source_rows"],
        "reference_model_count": summary["reference_models"], "reference_stop_membership_count": summary["reference_stop_memberships"],
        "reference_pair_occurrence_count": summary["reference_adjacent_pair_occurrences"], "row_status_counts": summary["row_status_counts"],
        "model_service_status_counts": summary["model_service_status_counts"], "pair_service_status_counts": summary["pair_service_status_counts"],
        "route_uid_and_empty_model_diagnostics": diagnostics,
        "limitations": ["Only the explicitly frozen endpoint population was reviewed; later raw acquisition is outside this snapshot.",
            "Matching current adjacent stop IDs proves only the retained original model/period/window estimate; it does not establish full-day service, holiday calendars or real-time operation.",
            "Unmatched, null-origin, outside-reference and unavailable source evidence is retained and supplies no usable station-pair cost; no Sequence/name/StationID substitution is allowed."]}, "records": results}
    target = root / REPORT
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args()
    report = review(args.root)
    print(json.dumps({"status": report["metadata"]["status"], "files": len(report["records"]), "source_scopes": report["metadata"]["source_scope_count"], "premium_rows": report["metadata"]["premium_row_count"]}))
    if report["metadata"]["status"] == "fail":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
