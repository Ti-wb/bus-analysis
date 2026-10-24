#!/usr/bin/env python3
"""Independently reconcile supplemental web layers with retained source evidence."""

import collections
import datetime as dt
import gzip
import json
import math
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web/public/data"
REPORT = ROOT / "data/quality/web_supplemental_review.json"
FINDINGS = []


def read(path):
    path = ROOT / path
    with (gzip.open(path, "rt") if path.suffix == ".gz" else path.open()) as stream:
        return json.load(stream)


def need(condition, message):
    if not condition:
        FINDINGS.append(message)


def round_geometry(value):
    if isinstance(value, list):
        return [round_geometry(item) for item in value]
    return round(value, 6) if isinstance(value, (int, float)) else value


def positions(value):
    if isinstance(value, list) and len(value) >= 2 and all(isinstance(x, (int, float)) for x in value):
        yield value
    elif isinstance(value, list):
        for item in value:
            yield from positions(item)


def check_geojson(name):
    doc = read(f"web/public/data/{name}.geojson")
    features = doc["features"]
    need(doc["type"] == "FeatureCollection" and bool(doc.get("metadata")), f"{name}: GeoJSON envelope")
    ids = [feature.get("id") for feature in features]
    need(all(isinstance(i, str) and i for i in ids) and len(ids) == len(set(ids)), f"{name}: unique string feature IDs")
    need(all(feature["properties"].get("id") == feature["id"] for feature in features), f"{name}: property IDs")
    need(all(math.isfinite(x) and math.isfinite(y) and -180 <= x <= 180 and -90 <= y <= 90
             for feature in features for x, y, *_ in positions(feature["geometry"]["coordinates"])), f"{name}: WGS84 coordinate ranges")
    need(doc["metadata"].get("crs") == "EPSG:4326", f"{name}: explicit WGS84 metadata")
    need(bool(doc["metadata"].get("sources")) and bool(doc["metadata"].get("limitations")), f"{name}: source lineage and limitations")
    return doc, {feature["id"]: feature for feature in features}


def review_regional():
    routes_doc, routes = check_geojson("regional_bus_routes")
    stops_doc, stops = check_geojson("regional_bus_stops")
    source = read("data/official_cross_city/service_coverage/NewTaipei_StopOfRoute.json.gz")
    evidence = read("data/official_cross_city/id_evidence/ntpc_tdx_stop_sequences.json.gz")
    category = read("data/official_cross_city/ntpc_leapfrog_crosswalk.json")["records"]
    allowed_statuses = {"verified_full_stop_id_sequence", "verified_order_with_explicit_non_boarding_virtual_points_omitted"}
    statuses = collections.Counter(x["status"] for x in evidence["records"])
    need(set(statuses) <= allowed_statuses, "regional: unknown sequence evidence status")
    verified = {(x["route_uid"], x["subroute_uid"], str(x["direction"])): x
                for x in evidence["records"] if x["status"] in allowed_statuses}
    leapfrog = {(str(x["route_id"]), str(sub)) for x in category
                if x["linkage_status"] == "verified_explicit_category_nid_and_getroute_ids"
                for sub in x["subroute_ids"]}
    expected_ids = set()
    expected_stops = {}
    leapfrog_count = 0
    memberships = 0
    for model in source["records"]:
        key = (model["route_uid"], model["sub_route_uid"], str(model["direction"]))
        proof = verified.get(key)
        need(proof is not None, f"regional: missing verified sequence {key}")
        if proof is None:
            continue
        sequence = model["stops"]
        need([str(x["stop_id"]) for x in sequence] == proof["tdx_stop_ids_in_source_order"], f"regional: literal stop sequence {key}")
        need([x["stop_sequence"] for x in sequence] == proof["tdx_stop_sequence_values"], f"regional: source sequence values {key}")
        if proof["status"] == "verified_order_with_explicit_non_boarding_virtual_points_omitted":
            need(bool(proof["direct_only_memberships"]) and all(x["explicit_virtual_non_boarding_name_annotation"] is True for x in proof["direct_only_memberships"]), f"regional: explicit omitted virtual points {key}")
        fid = "NWT:" + ":".join(key)
        expected_ids.add(fid)
        feature = routes.get(fid)
        need(feature is not None, f"regional: route missing {fid}")
        is_leapfrog = (str(model["route_id"]), str(model["sub_route_id"])) in leapfrog
        leapfrog_count += is_leapfrog
        coords = [[x["stop_position"]["position_lon"], x["stop_position"]["position_lat"]] for x in sequence]
        if feature:
            p = feature["properties"]
            need(feature["geometry"] == {"type": "MultiLineString", "coordinates": [coords]}, f"regional: full schematic vertices {fid}")
            need(p["stop_count"] == len(sequence) and p["sequence_status"] == proof["status"], f"regional: route evidence fields {fid}")
            need(p["is_leapfrog"] == is_leapfrog and p["route_id"] == str(model["route_id"]) and p["subroute_id"] == str(model["sub_route_id"]), f"regional: route category/IDs {fid}")
            need(p["name"] == model["route_name"]["zh_tw"] and p["source_updated_at"] == model["update_time"], f"regional: source label/time {fid}")
        for stop, coordinate in zip(sequence, coords):
            memberships += 1
            item = expected_stops.setdefault(stop["stop_uid"], {"coordinates": set(), "names": set(), "ids": set(), "routes": set(), "route_uids": set(), "leapfrog": False})
            item["coordinates"].add(tuple(coordinate))
            item["names"].add(stop["stop_name"]["zh_tw"])
            item["ids"].add(str(stop["stop_id"]))
            item["routes"].add(model["route_name"]["zh_tw"])
            item["route_uids"].add(model["route_uid"])
            item["leapfrog"] |= is_leapfrog
    need(set(routes) == expected_ids, "regional: exact route model ID set")
    need(set(stops) == set(expected_stops), "regional: exact stop UID set")
    for uid, expected in expected_stops.items():
        feature = stops.get(uid)
        if not feature:
            continue
        p = feature["properties"]
        need(tuple(feature["geometry"]["coordinates"]) in expected["coordinates"], f"regional: retained stop coordinate {uid}")
        need(p["coordinate_variant_count"] == len(expected["coordinates"]), f"regional: coordinate variants {uid}")
        need(p["name"] in expected["names"] and p["stop_id"] in expected["ids"] and p["stop_uid"] == uid, f"regional: stop identity {uid}")
        need(set(p["routes"]) == expected["routes"] and set(p["route_ids"]) == expected["route_uids"] and p["route_count"] == len(expected["route_uids"]), f"regional: stop route memberships {uid}")
        need(p["is_leapfrog"] == expected["leapfrog"] and p["weekday_departures"] is None, f"regional: leapfrog and unmodeled departures {uid}")
    return {"files": ["regional_bus_routes.geojson", "regional_bus_stops.geojson"], "route_model_count": len(routes), "unique_route_uid_count": len({x["route_uid"] for x in source["records"]}), "leapfrog_model_count": leapfrog_count, "stop_count": len(stops), "source_stop_memberships_checked": memberships, "sequence_evidence_status_counts": dict(statuses), "source_updated_at": source["metadata"]["source_updated_at"], "checks": "Every complete source sequence, category ID, unique stop UID, coordinate, name, route membership and unmodeled null departure checked."}


def review_villages():
    doc, features = check_geojson("villages")
    population = read("data/official_cross_city/population_2026-09.json")
    pop = {x["source_area_code"]: x for x in population["records"]}
    crosswalk = {x["boundary_village_code"]: x for x in read("data/official_cross_city/population_boundary_crosswalk.json")["records"]}
    boundaries = read("map-data/admin/villages.geojson")["features"] + read("map-data/admin/keelung_villages.geojson")["features"]
    need(set(features) == {x["id"] for x in boundaries}, "villages: complete 3-city boundary ID set")
    totals = collections.Counter()
    statuses = collections.Counter()
    cities = collections.Counter()
    fields = ["population", "households", "population_age_0_14", "population_age_15_64", "population_age_65_plus"]
    for boundary in boundaries:
        fid = boundary["id"]
        p = features[fid]["properties"]
        bp = boundary["properties"]
        need(features[fid]["geometry"]["type"] == boundary["geometry"]["type"] and features[fid]["geometry"]["coordinates"] == round_geometry(boundary["geometry"]["coordinates"]), f"villages: all original vertices rounded only {fid}")
        need(p["city_code"] == bp.get("city_code", bp.get("county_code")) and p["name"] == bp.get("name", bp.get("village_name")) and p["district_name"] == bp["district_name"], f"villages: official identity {fid}")
        proof = crosswalk.get(fid)
        status = proof["status"] if proof else "boundary_only_without_population"
        statuses[status] += 1
        cities[p["city_name"]] += 1
        need(p["join_status"] == status and p["period"] == "2026-09" and p["boundary_period"] == "2026-08-17", f"villages: join/period {fid}")
        if status == "matched_code_and_names":
            source = pop[proof["population_area_code"]]
            need(source["village_name"] == p["name"] and source["county_name"] == p["city_name"] and source["area_name"] == p["city_name"] + p["district_name"], f"villages: literal code plus names {fid}")
            need(all(p[field] == source[field] for field in fields), f"villages: source values {fid}")
            need(sum(p[field] for field in fields[2:]) == p["population"], f"villages: age sum {fid}")
            need(p["elderly_share"] == source["population_age_65_plus"] / source["population"], f"villages: elderly ratio {fid}")
            totals.update({field: source[field] for field in fields})
        else:
            need(all(p[field] is None for field in fields + ["elderly_share"]), f"villages: unmatched remains null {fid}")
    need(doc["metadata"]["join_status_counts"] == dict(statuses), "villages: status manifest")
    return {"file": "villages.geojson", "feature_count": len(features), "city_counts": dict(cities), "join_status_counts": dict(statuses), "known_value_sums": dict(totals), "null_population_count": sum(x["properties"]["population"] is None for x in features.values()), "population_period": "2026-09", "boundary_period": "2026-08-17", "checks": "All boundary geometries, retained vertices, codes, literal names, source values, age sums, ratios and withheld nulls checked; population-month geometry equivalence remains unverified."}


def local_day(value):
    parsed = dt.datetime.fromisoformat(value)
    return parsed.astimezone(ZoneInfo("Asia/Taipei")).date().isoformat() if parsed.tzinfo else None


def review_traffic():
    doc, features = check_geojson("traffic_sections")
    definition = read("data/tdx_road_traffic/observations/current_section.json")
    observation = read("data/tdx_road_traffic/observations/current_section_live.json")
    raw_definition = read(definition["metadata"]["sources"][0]["raw_file"])
    raw_observation = read(observation["metadata"]["sources"][0]["raw_file"])
    a = {x["SectionID"]: x for x in raw_definition["Sections"]}
    b = {x["SectionID"]: x for x in raw_observation["LiveTraffics"]}
    need(len(a) == len(raw_definition["Sections"]) and len(b) == len(raw_observation["LiveTraffics"]), "traffic: raw unique SectionIDs")
    need(raw_definition["AuthorityCode"] == raw_observation["AuthorityCode"] == "TPE" and set(a) == set(b), "traffic: authority and bidirectional IDs")
    need(all(a[k].get("LinkIDs") and a[k]["LinkIDs"] == b[k].get("LinkIDs") for k in a), "traffic: exact nonempty raw LinkIDs for every SectionID")
    source_days = {local_day(raw[field]) for raw in (raw_definition, raw_observation) for field in ("UpdateTime", "SrcUpdateTime")}
    need(len(source_days) == 1 and None not in source_days and all(local_day(x["DataCollectTime"]) in source_days for x in b.values()), "traffic: all source and collection times share an offset-known local day")
    need("LinkVersion" not in raw_definition and "LinkVersion" not in raw_observation, "traffic: absence of raw LinkVersion must be reconsidered when source changes")
    need(doc["metadata"]["snapshot_only"] is True and doc["metadata"]["version_equivalence_verified"] is False, "traffic: bounded snapshot and non-equivalence flags")
    need(all(doc["metadata"]["identity_checks"].values()), "traffic: declared identity gates")
    expected = {k for k,x in a.items() if x.get("SectionStart") and x.get("SectionEnd") and all(x[edge].get(axis) is not None for edge in ("SectionStart", "SectionEnd") for axis in ("PositionLon", "PositionLat"))}
    need(set(features) == expected, "traffic: only complete native endpoint geometries")
    values = collections.Counter()
    for fid in expected:
        definition_row, observation_row = a[fid], b[fid]
        feature = features[fid]
        p = feature["properties"]
        coordinates = [[definition_row[edge]["PositionLon"], definition_row[edge]["PositionLat"]] for edge in ("SectionStart", "SectionEnd")]
        need(feature["geometry"] == {"type": "LineString", "coordinates": coordinates}, f"traffic: raw endpoint coordinates {fid}")
        speed, runtime = observation_row["TravelSpeed"], observation_row["TravelTime"]
        status = "source_negative_sentinel" if speed < 0 or runtime < 0 else "source_zero_not_interpreted" if speed == 0 or runtime == 0 else "positive_observed_value"
        values[status] += 1
        need(p["raw_travel_speed"] == speed and p["raw_travel_time"] == runtime and p["value_status"] == status, f"traffic: retained raw values/status {fid}")
        need(p["travel_speed"] == (speed if status == "positive_observed_value" else None) and p["travel_time"] == (runtime if status == "positive_observed_value" else None), f"traffic: withheld zero/sentinel display values {fid}")
        need(p["data_collect_time"] == observation_row["DataCollectTime"], f"traffic: collection time {fid}")
    need(doc["metadata"]["value_status_counts"] == dict(values) and doc["metadata"]["excluded_incomplete_endpoint_count"] == len(a) - len(expected), "traffic: population and exclusions")
    spec = read("raw_data/tdx_road_traffic/basic_spec.json")["components"]["schemas"]
    need("WGS84" in spec["PTX.Service.DTO.Traffic.Specification.V2.Commons.Point"]["properties"]["PositionLat"]["description"], "traffic: official coordinate CRS")
    return {"file": "traffic_sections.geojson", "source_definition_count": len(a), "source_observation_count": len(b), "exact_section_and_link_id_count": len(a), "feature_count": len(features), "excluded_incomplete_endpoint_count": len(a)-len(expected), "value_status_counts": dict(values), "source_local_day": sorted(source_days), "definition_source_updated_at": raw_definition["SrcUpdateTime"], "observation_source_updated_at": raw_observation["SrcUpdateTime"], "data_collect_times": sorted({x["DataCollectTime"] for x in b.values()}), "link_version_in_both_raw_responses": None, "version_equivalence_verified": False, "checks": "Raw responses and official field definitions checked; same-authority same-day exact native SectionID/LinkIDs association accepted only for this snapshot. No cross-date or version equivalence established; endpoint lines are schematic."}


def minute(text):
    try:
        hour, minutes = map(int, text.split(":"))
        return hour * 60 + minutes if 0 <= hour <= 24 and 0 <= minutes < 60 and (hour < 24 or minutes == 0) else None
    except (ValueError, AttributeError):
        return None


def review_travel():
    doc, features = check_geojson("bus_travel_times")
    pairs = read("data/official_cross_city/travel_time_coverage/pair_coverage.json.gz")["records"]
    premium_models = {x["premium_instance_id"]: x for x in read("data/official_cross_city/travel_time_coverage/premium_models.json.gz")["records"]}
    inventory = {x["source_id"]: x for x in read("data/official_cross_city/travel_time_coverage/source_inventory.json")["records"]}
    reference = read("data/official_cross_city/service_coverage/Taipei_StopOfRoute.json.gz")["records"]
    sources = {}
    expected = {}
    rows_checked = 0
    for pair in pairs:
        if pair["status"] != "matched" or pair["requires_source_review"]:
            continue
        fid = pair["pair_occurrence_id"]
        model_number = int(pair["reference_model_id"].split(":")[-1])
        model = reference[model_number - 1]
        first = model["stops"][pair["from_source_record_number"]-1]
        second = model["stops"][pair["to_source_record_number"]-1]
        need(pair["to_source_record_number"] == pair["from_source_record_number"]+1 and str(first["stop_id"]) == pair["from_stop_id"] and str(second["stop_id"]) == pair["to_stop_id"], f"travel: exact adjacent source occurrences {fid}")
        need((pair["route_uid"],pair["sub_route_uid"],str(pair["direction"])) == (model["route_uid"],model["sub_route_uid"],str(model["direction"])), f"travel: reference model key {fid}")
        entry = expected.setdefault(fid, {"hours": {"0": [set() for _ in range(24)], "1": [set() for _ in range(24)]}, "model": model, "first": first, "second": second, "effective": set(), "expire": set(), "update": set()})
        for evidence in pair["source_rows_by_premium_instance"]:
            proof = premium_models[evidence["premium_instance_id"]]
            source_id = proof["source_id"]
            if source_id not in sources:
                sources[source_id] = read(inventory[source_id]["output_file"])["records"]
            observation = sources[source_id][proof["source_model_number"] - 1]
            need((observation["route_uid"],observation["sub_route_uid"],str(observation["direction"])) == (model["route_uid"],model["sub_route_uid"],str(model["direction"])), f"travel: premium exact model key {fid}")
            usable = []
            for number in evidence["source_row_numbers"]:
                row = observation["travel_times"][number-1]
                rows_checked += 1
                need(str(row["from_stop_id"]) == pair["from_stop_id"] and str(row["to_stop_id"]) == pair["to_stop_id"] and str(row["service_tag"]) == pair["service_tag"], f"travel: premium exact row identity {fid}/{number}")
                start, end = minute(row["start_time"]), minute(row["end_time"])
                runtime = row["run_time"]
                if not (isinstance(runtime, (int,float)) and not isinstance(runtime,bool) and math.isfinite(runtime) and runtime >= 0 and start is not None and end is not None and start < end):
                    continue
                usable.append(number)
                for hour in range(24):
                    if start <= hour*60 < end:
                        entry["hours"][pair["service_tag"]][hour].add(runtime)
                entry["effective"].add(observation["effective_date"])
                entry["expire"].add(observation["expire_date"])
                entry["update"].add(observation["update_time"])
            need(usable == evidence["usable_numeric_window_row_numbers"], f"travel: independently valid runtime/window rows {fid}")
    eligible = {fid: value for fid,value in expected.items() if any(slot for slots in value["hours"].values() for slot in slots)}
    need(set(features) == set(eligible), "travel: exact eligible adjacent pair ID set")
    conflicts = 0
    nulls = 0
    values_checked = 0
    for fid, entry in eligible.items():
        feature = features[fid]
        p = feature["properties"]
        model, first, second = entry["model"],entry["first"],entry["second"]
        need((p["route_uid"],p["subroute_uid"],p["direction"],p["from_stop_id"],p["to_stop_id"]) == (model["route_uid"],model["sub_route_uid"],str(model["direction"]),str(first["stop_id"]),str(second["stop_id"])), f"travel: exported native IDs {fid}")
        coordinates = [[stop["stop_position"]["position_lon"],stop["stop_position"]["position_lat"]] for stop in (first,second)]
        need(feature["geometry"] == {"type":"LineString","coordinates":coordinates}, f"travel: source adjacent stop coordinates {fid}")
        need(p["from_name"] == first["stop_name"]["zh_tw"] and p["to_name"] == second["stop_name"]["zh_tw"] and p["route_name"] == model["route_name"]["zh_tw"], f"travel: labels {fid}")
        for tag, field in (("1","weekday"),("0","weekend")):
            expected_values = [next(iter(slot)) if len(slot)==1 else None for slot in entry["hours"][tag]]
            need(p[field] == expected_values, f"travel: 24 hourly exact-window seconds {fid}/{field}")
            conflicts += sum(len(slot)>1 for slot in entry["hours"][tag])
            nulls += sum(value is None for value in expected_values)
            values_checked += 24
        need(entry["effective"] == {p["effective_date"]} and entry["expire"] == {p["expire_date"]} and entry["update"] == {p["source_updated_at"]}, f"travel: retained date/update values {fid}")
    need(doc["metadata"]["conflicting_hour_value_count"] == conflicts, "travel: conflict count")
    return {"file":"bus_travel_times.geojson", "pair_service_evidence_count":len(pairs), "feature_count":len(features), "source_runtime_rows_independently_checked":rows_checked, "hour_values_independently_recomputed":values_checked, "null_hour_count":nulls, "conflicting_hour_count":conflicts, "effective_dates":sorted({p["properties"]["effective_date"] for p in features.values()}), "expire_dates":sorted({p["properties"]["expire_date"] for p in features.values()}), "checks":"All retained matched, non-review pair evidence rechecked against reference model, literal adjacent stop occurrences, premium model/row/service IDs and source half-open clock windows; hourly runtime values independently recomputed without importing producer."}


def main():
    records = [review_regional(), review_villages(), review_traffic(), review_travel()]
    manifest = read("web/public/data/supplemental_manifest.json")
    need(isinstance(manifest["schema_version"],(str,int)) and isinstance(manifest["dataset_id"],str) and isinstance(manifest.get("metadata"),dict), "manifest: standard JSON envelope")
    manifest_counts = {}
    for record in manifest["records"]:
        layer = read("web/public/data/" + record["file"])
        need(record["feature_count"] == len(layer["features"]), f"manifest: feature count {record['id']}")
        need(record["period"] == layer["metadata"]["period"] and record["source_updated_at"] == layer["metadata"]["source_updated_at"], f"manifest: source time/period {record['id']}")
        manifest_counts[record["id"]] = record["feature_count"]
    need(len(manifest_counts)==5, "manifest: all five layers exactly once")
    records.append({"file":"supplemental_manifest.json", "layer_feature_counts":manifest_counts})
    report = {"schema_version":1, "dataset_id":"independent_web_supplemental_review", "metadata":{
        "reviewer_agent":"/root/research_review", "producer_agent":"/root/data_pipeline", "independent_of_producer":True,
        "reviewed_at":dt.datetime.now(ZoneInfo("Asia/Taipei")).isoformat(timespec="seconds"), "source_updated_at":None,
        "scope":"Five supplemental display layers and their manifest; retained official source snapshots only.",
        "method":"Separate review implementation. No producer imports. Exhaustive source ID, geometry vertex, population, native road snapshot identity and premium runtime row reconciliation.",
        "status":"pass_with_limitations" if not FINDINGS else "fail", "blocking_findings":FINDINGS,
        "limitations":["This review confirms source-to-output semantics; it does not establish live operating service, current traffic, temporal boundary equivalence or browser visual acceptance.", "Road association is bounded to the retained same-authority, same-day exact native SectionID/LinkIDs snapshots and is not a LinkVersion equivalence proof.", "Village geometry is checked against the retained official layer after stated rounding; this does not test polygon topology or point-on-boundary assignment."]}, "records":records}
    REPORT.parent.mkdir(parents=True,exist_ok=True)
    REPORT.write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n")
    print(json.dumps({"status":report["metadata"]["status"],"blocking_finding_count":len(FINDINGS),"report":str(REPORT.relative_to(ROOT)),"findings":FINDINGS[:12]},ensure_ascii=False))
    raise SystemExit(1 if FINDINGS else 0)


if __name__ == "__main__":
    main()
