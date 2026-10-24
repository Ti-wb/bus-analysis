"""Independent regional service evidence audit using original TDX pages and NLSC ZIP."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime
import gzip
import io
import json
import math
from pathlib import Path
import re
import zipfile
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parents[1]
SCOPES = ("Taipei", "NewTaipei", "Keelung", "InterCity")
CITIES = {"63000": "臺北市", "65000": "新北市", "10017": "基隆市"}
BASE = Path("data/official_cross_city/regional_service_evidence")
WEEK = {key: key.lower() for key in ("Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday")}
FIELDS = {
    **WEEK, "RouteUID": "route_uid", "RouteID": "route_id", "RouteName": "route_name", "Operators": "operators",
    "SubRouteUID": "sub_route_uid", "SubRouteID": "sub_route_id", "SubRouteName": "sub_route_name", "Direction": "direction",
    "City": "city", "CityCode": "city_code", "Stops": "stops", "UpdateTime": "update_time", "VersionID": "version_id",
    "OperatorID": "operator_id", "OperatorName": "operator_name", "OperatorCode": "operator_code", "OperatorNo": "operator_no",
    "StopUID": "stop_uid", "StopID": "stop_id", "StopName": "stop_name", "StopBoarding": "stop_boarding", "StopSequence": "stop_sequence",
    "StopPosition": "stop_position", "PositionLon": "position_lon", "PositionLat": "position_lat", "StationID": "station_id",
    "StationGroupID": "station_group_id", "LocationCityCode": "location_city_code", "Zh_tw": "zh_tw", "En": "en",
    "Timetables": "timetables", "Frequencys": "frequencys", "TripID": "trip_id", "IsLowFloor": "is_low_floor",
    "ServiceDay": "service_day", "SpecialDays": "special_days", "StopTimes": "stop_times", "ArrivalTime": "arrival_time",
    "DepartureTime": "departure_time", "StartTime": "start_time", "EndTime": "end_time", "MinHeadwayMins": "min_headway_mins",
    "MaxHeadwayMins": "max_headway_mins", "Dates": "dates", "DatePeriod": "date_period", "StartDate": "start_date", "EndDate": "end_date",
    "ServiceStatus": "service_status", "Description": "description",
}
IDS = {"RouteUID", "RouteID", "SubRouteUID", "SubRouteID", "VersionID", "OperatorID", "StopUID", "StopID", "StationID", "StationGroupID", "TripID"}


def require(value, message):
    if not value:
        raise ValueError(message)


def strict_equal(actual, expected):
    if type(actual) is not type(expected):
        return False
    if isinstance(expected, dict):
        return actual.keys() == expected.keys() and all(strict_equal(actual[key], value) for key, value in expected.items())
    if isinstance(expected, list):
        return len(actual) == len(expected) and all(strict_equal(a, b) for a, b in zip(actual, expected))
    return actual == expected


def read(path):
    stored = path.read_bytes()
    return json.loads(gzip.decompress(stored) if path.suffix == ".gz" else stored)


def source_projection(value):
    """Explicit source-field projection; original geohash is intentionally omitted."""
    if isinstance(value, list):
        return [source_projection(item) for item in value]
    if not isinstance(value, dict):
        return None if value == "" else value
    result = {}
    for key, item in value.items():
        if key == "GeoHash":
            continue
        require(key in FIELDS, "New source field needs explicit audit: " + key)
        result[FIELDS[key]] = str(item) if key in IDS and item not in (None, "") else source_projection(item)
    return result


def point_state(position):
    if not isinstance(position, dict) or position.get("PositionLon") is None or position.get("PositionLat") is None:
        return "missing_coordinates", None
    lon, lat = position["PositionLon"], position["PositionLat"]
    if any(type(value) not in (int, float) or not math.isfinite(value) for value in (lon, lat)) or not (-180 <= lon <= 180 and -90 <= lat <= 90):
        return "invalid_coordinates", None
    if lon == 0 or lat == 0:
        return "zero_coordinate_requires_source_review", None
    return "valid_coordinates", (lon, lat)


def variant_key(stop):
    pos = stop.get("StopPosition") or {}
    return stop["StopUID"], stop["StopID"], stop.get("StationID"), pos.get("PositionLon"), pos.get("PositionLat")


def five_key(scope, row, operator=None):
    return scope, row["RouteUID"], row["SubRouteUID"], row.get("Direction"), row.get("OperatorID") if operator is None else operator


def publication_status(count, complete, operator_known=True, missing_operator_candidates=False):
    if count:
        return "published"
    if missing_operator_candidates:
        return "indeterminate_missing_schedule_operator"
    return "not_observed_in_complete_published_endpoint" if complete and operator_known else "indeterminate_partial_or_not_requested"


def resolve_schedule_relations(scope, stop_model, operator, schedule_index):
    prefix = (scope, stop_model["RouteUID"], stop_model["SubRouteUID"], stop_model.get("Direction"))
    assigned = schedule_index.get((*prefix, operator), []) if operator is not None else []
    unassigned = schedule_index.get((*prefix, None), [])
    return assigned, unassigned, bool(unassigned) and not assigned


def schedule_support_limitation(raw):
    missing = [scope for scope in SCOPES if raw.get((scope, "Schedule")) is None]
    if missing:
        return ("Schedule was not acquired for " + ", ".join(missing) +
                "; corresponding publication/day statuses remain indeterminate, never no-service claims.")
    return ("All four configured Schedule endpoints were acquired through their exhausted pages; same-three-key candidates without "
            "Schedule OperatorID remain indeterminate_missing_schedule_operator. Only absence of both exact and unassigned candidates "
            "permits not_observed_in_complete_published_endpoint; neither status proves no actual service.")


def calendars(row):
    result = []
    for field, kind in (("Timetables", "timetable"), ("Frequencys", "frequency")):
        for index, item in enumerate(row.get(field) or [], 1):
            raw_day = item.get("ServiceDay")
            valid = isinstance(raw_day, dict) and all(type(raw_day.get(key)) is int and raw_day[key] in (0, 1) for key in WEEK)
            result.append({"source_kind": kind, "source_ordinal": index, "trip_id": item.get("TripID"),
                           "service_day": source_projection(raw_day), "weekly_calendar_content_status": "valid_published_flags" if valid else
                           "not_supplied" if raw_day is None else "requires_source_review", "special_days": source_projection(item.get("SpecialDays"))})
    return result


def read_endpoint(root, scope, service, manifest):
    receipts = sorted([row for row in manifest["records"] if row.get("scope") == scope and row.get("service") == service], key=lambda row: row["skip"])
    path = Path(f"data/official_cross_city/service_coverage/{scope}_{service}.json.gz")
    if not receipts:
        require(not (root / path).exists(), "Normalized endpoint has no original receipt")
        return None, None
    rows, expected_skip = [], 0
    for number, receipt in enumerate(receipts):
        url = urlsplit(receipt["download_url"])
        expected_path = f"/api/basic/v2/Bus/{service}/InterCity" if scope == "InterCity" else f"/api/basic/v2/Bus/{service}/City/{scope}"
        require(url.scheme == "https" and url.hostname == "tdx.transportdata.tw" and url.path == expected_path
                and parse_qs(url.query) == {"$format": ["JSON"], "$top": [str(receipt["top"])], "$skip": [str(receipt["skip"])],
                                          "$orderby": ["RouteUID,SubRouteUID,Direction"]}, "Source scope/page URL")
        raw_path = root / receipt["raw_file"]
        stored = raw_path.read_bytes()
        require(raw_path.suffix == ".gz" and receipt.get("storage_encoding") == "gzip" and len(stored) == receipt["stored_bytes"]
                and receipt["http_status"] == 200 and receipt["status"] == "downloaded", "Stored TDX receipt")
        body = gzip.decompress(stored)
        require(len(body) == receipt["bytes"] and receipt["skip"] == expected_skip, "Original byte count/contiguous pages")
        page = json.loads(body)
        require(isinstance(page, list) and len(page) == receipt["record_count"] and len(page) <= receipt["top"], "Original TDX page count")
        require((len(page) == receipt["top"]) if number < len(receipts) - 1 else (len(page) < receipt["top"]), "Endpoint exhaustion proof")
        rows.extend(page)
        expected_skip += receipt["top"]
    document = read(root / path)
    require(strict_equal(document["records"], source_projection(rows)), "All normalized endpoint fields/types vs original: " + str(path))
    metadata = document["metadata"]
    times = sorted({row["UpdateTime"] for row in rows if row.get("UpdateTime") is not None})
    require(metadata["scope_status"] == "complete_published_endpoint_by_exhausted_page" and metadata["sources"] == receipts
            and metadata["source_record_update_times"] == times and metadata["source_updated_at"] == (times[0] if len(times) == 1 else None), "Source input metadata")
    info = {"path": str(path), "record_count": len(rows), "scope_status": metadata["scope_status"], "source_updated_at": metadata["source_updated_at"],
            "source_record_update_times": times, "source_ids": [row["source_id"] for row in receipts],
            "source_pages": sorted({row["source_page"] for row in receipts}), "download_urls": [row["download_url"] for row in receipts],
            "raw_files": [row["raw_file"] for row in receipts], "downloaded_at": [row["downloaded_at"] for row in receipts]}
    return rows, info


def native_masks(root):
    """Classify inverse-transformed WGS84 points in original EPSG:3824 geometry."""
    import shapefile
    from pyproj import CRS, Transformer
    from shapely.geometry import shape
    from shapely.ops import unary_union
    from shapely.prepared import prep
    path = root / "map-data/raw/boundary_evidence/current_villages.zip"
    receipt = read(path.with_name(path.name + ".source.json"))
    require(receipt["http_status"] == 200 and receipt["bytes"] == path.stat().st_size, "NLSC original receipt")
    with zipfile.ZipFile(path) as archive:
        stems = [name[:-4] for name in archive.namelist() if re.fullmatch(r"VILLAGE_NLSC_\d{7}\.shp", name)]
        require(len(stems) == 1, "One primary NLSC village release")
        stem = stems[0]
        source_crs = CRS.from_wkt(archive.read(stem + ".prj").decode("utf-8-sig"))
        require(source_crs.to_epsg() == 3824 and source_crs.is_geographic, "NLSC original CRS")
        reader = shapefile.Reader(**{ext: io.BytesIO(archive.read(stem + "." + ext)) for ext in ("shp", "shx", "dbf")}, encoding="utf-8")
        cities = defaultdict(list)
        for row in reader.iterShapeRecords():
            code = row.record.as_dict()["COUNTYCODE"]
            if code in CITIES:
                polygon = shape(row.shape.__geo_interface__)
                require(polygon.is_valid and not polygon.is_empty, "No silent NLSC geometry repair")
                cities[code].append(polygon)
    require(set(cities) == set(CITIES), "Three exact COUNTYCODE masks")
    masks = {code: prep(unary_union(cities[code])) for code in CITIES}
    forward = Transformer.from_crs(source_crs, 4326, always_xy=True)
    forward.transform(121.5, 25.0)
    used = forward.get_last_used_operation()
    inverse = Transformer.from_crs(4326, source_crs, always_xy=True, allow_ballpark=False)
    require(used.accuracy >= 0 and "unavailable" not in used.description and "ballpark" not in used.description.lower(), "Actually used CRS operation and precision")
    version = re.fullmatch(r"VILLAGE_NLSC_(\d{3})(\d{2})(\d{2})", stem)
    metadata = {"source": receipt, "source_layer": stem, "source_period": f"{int(version[1])+1911:04d}-{version[2]}-{version[3]}",
                "source_crs": "EPSG:3824", "target_crs": "EPSG:4326", "source_feature_counts": {code: len(rows) for code, rows in cities.items()},
                "operation": used.description, "operation_accuracy_m": used.accuracy, "operation_status": "used_operation_recorded"}
    return masks, inverse, metadata, used.definition


def classify(coordinates, masks, inverse):
    from shapely.geometry import Point
    point = Point(*inverse.transform(*coordinates))
    return [code for code, geometry in masks.items() if geometry.covers(point)]


def compare_records(actual, expected, label):
    require(len(actual) == len(expected), label + " record count")
    for index, (a, b) in enumerate(zip(actual, expected), 1):
        require(strict_equal(a, b), f"{label} original-source comparison at record {index}; fields=" + str([key for key in set(a) | set(b) if not strict_equal(a.get(key), b.get(key))]))


def verify_population_totals(metadata, source_models, relation_models, geographies, original_memberships):
    require(metadata["source_model_count"] == source_models and metadata["operator_relation_model_count"] == relation_models
            and metadata["stop_geography_variant_count"] == len(geographies) and metadata["source_stop_membership_count"] == original_memberships
            and metadata["stop_geography_status_counts"] == dict(Counter(row["geographic_status"] for row in geographies)), "Unduplicated source/model/member totals")
    require(original_memberships == sum(row["source_membership_count"] for row in geographies), "Variant membership sum equals original source memberships")


def run(root):
    manifest = read(root / "raw_data/official_cross_city/service_coverage/manifest.json")
    documents = {path.name: read(path) for path in (root / BASE).iterdir() if path.name in {
        "stop_geography.json.gz", "service_models.json.gz", "schedule_calendars.json.gz", "coverage_summary.json", "support.json"}}
    require(len(documents) == 5, "Five regional output documents")
    masks, inverse, boundary, operation_definition = native_masks(root)
    raw, inputs, support = {}, [], []
    for scope in SCOPES:
        for service in ("StopOfRoute", "Schedule"):
            rows, info = read_endpoint(root, scope, service, manifest)
            raw[scope, service] = rows
            if rows is None:
                support.append({"source_scope": scope, "service": service, "publication_support": "not_acquired", "record_count": None, "input_file": None})
            else:
                inputs.append(info)
                support.append({"source_scope": scope, "service": service, "publication_support": "complete_published_endpoint", "record_count": len(rows), "input_file": info["path"]})
    compare_records(documents["support.json"]["records"], support, "Configured endpoint support")
    spec = read(root / "raw_data/official_cross_city/service_coverage/specifications/bus_v2.json")["components"]["schemas"]
    stop_spec = spec["PTX.Service.DTO.Bus.Specification.V2.Embedded.Stop"]["properties"]
    point_spec = spec["PTX.Service.DTO.Shared.Specification.V2.Base.PointType"]["properties"]
    require("StationUID" not in stop_spec and "站位ID" in stop_spec["StationID"]["description"]
            and all("WGS84" in point_spec[key]["description"] for key in ("PositionLon", "PositionLat")), "Official station ID/coordinate semantics")
    generated = set()
    for filename, doc in documents.items():
        meta = doc["metadata"]
        require(meta["record_count"] == len(doc["records"]) and meta["source_inputs"] == inputs and meta["statistical_period"] is None
                and meta["source_updated_at"] is None and meta["coordinate_reference_system"] == "EPSG:4326"
                and meta["coordinate_order"] == ["longitude", "latitude"] and meta["join_key"] == ["source_scope", "route_uid", "subroute_uid", "direction", "operator_id"], "Shared input/time/grain metadata: " + filename)
        require(meta["license"]["url"] == "https://tdx.transportdata.tw/term" and meta["boundary_license"]["url"] == "https://data.gov.tw/license", "Licenses")
        require(all(meta["boundary_membership"].get(key) == value for key, value in boundary.items()), "NLSC version/CRS/precision metadata: " + filename)
        generated.add(meta["generated_at"])
    require(len(generated) == 1, "Mixed output reconstruction snapshots")

    schedule_expected, schedule_index = [], defaultdict(list)
    for scope in SCOPES:
        for index, source in enumerate(raw[scope, "Schedule"] or [], 1):
            nodes = calendars(source)
            row = {"schedule_evidence_id": f"{scope}_schedule_{index}", "source_scope": scope, "source_record_number": index,
                   "route_uid": source["RouteUID"], "route_id": source["RouteID"], "subroute_uid": source["SubRouteUID"], "subroute_id": source["SubRouteID"],
                   "direction": source.get("Direction"), "operator_id": source.get("OperatorID"), "update_time": source.get("UpdateTime"),
                   "version_id": str(source["VersionID"]) if source.get("VersionID") is not None else None,
                   "timetable_count": len(source.get("Timetables") or []), "frequency_count": len(source.get("Frequencys") or []),
                   "service_day_object_count": sum(node["service_day"] is not None for node in nodes),
                   "special_day_object_count": sum(len(node["special_days"] or []) for node in nodes),
                   "invalid_weekly_calendar_count": sum(node["weekly_calendar_content_status"] == "requires_source_review" for node in nodes), "calendar_publications": nodes}
            schedule_expected.append(row)
            schedule_index[five_key(scope, source)].append(row)
    compare_records(documents["schedule_calendars.json.gz"]["records"], schedule_expected, "Schedule calendars")

    actual_geographies = documents["stop_geography.json.gz"]["records"]
    geography_by_key, geography_by_id = {}, {}
    for row in actual_geographies:
        pos = row["source_position"]
        key = row["stop_uid"], row["stop_id"], row["station_id"], pos["longitude"], pos["latitude"]
        require(key not in geography_by_key and row["stop_geography_id"] not in geography_by_id, "Duplicate geography variant/key")
        geography_by_key[key] = row
        geography_by_id[row["stop_geography_id"]] = row
    variant_stats, uid_variants, coordinate_cache = {}, defaultdict(set), {}
    source_model_count = source_memberships = 0
    model_expected, original_models = [], {}
    for scope in SCOPES:
        require(raw[scope, "StopOfRoute"] is not None, "A configured geographic scope has no StopOfRoute source")
        for index, source in enumerate(raw[scope, "StopOfRoute"], 1):
            source_model_count += 1
            original_models[scope, index] = source
            stop_ids, city_counts, gaps = [], Counter(), Counter()
            for stop in source.get("Stops") or []:
                source_memberships += 1
                key = variant_key(stop)
                require(key in geography_by_key, "Original StopUID/station/position variant omitted")
                actual = geography_by_key[key]
                status, coordinates = point_state(stop.get("StopPosition"))
                if coordinates is not None and coordinates not in coordinate_cache:
                    coordinate_cache[coordinates] = classify(coordinates, masks, inverse)
                cities = coordinate_cache[coordinates] if coordinates is not None else []
                if key not in variant_stats:
                    variant_stats[key] = {"stop_geography_id": actual["stop_geography_id"], "stop_uid": stop["StopUID"], "stop_id": stop["StopID"],
                        "station_id": stop.get("StationID"), "station_uid": None, "station_uid_status": "not_published_by_stopofroute",
                        "source_position": {"longitude": key[3], "latitude": key[4]}, "coordinate_status": status,
                        "geographic_status": ("regional_member" if cities else "outside_region") if status == "valid_coordinates" else status,
                        "covering_city_codes": cities, "source_scopes": [], "source_stop_names": [], "source_membership_count": 0}
                    uid_variants[stop["StopUID"]].add(key)
                expected = variant_stats[key]
                if scope not in expected["source_scopes"]:
                    expected["source_scopes"].append(scope)
                name = stop.get("StopName", {}).get("Zh_tw")
                if name not in expected["source_stop_names"]:
                    expected["source_stop_names"].append(name)
                expected["source_membership_count"] += 1
                stop_ids.append(actual["stop_geography_id"])
                city_counts.update(cities)
                if status != "valid_coordinates":
                    gaps[status] += 1
            operators = sorted({operator["OperatorID"] for operator in source.get("Operators") or []}) or [None]
            for operator in operators:
                matches, unassigned, missing_operator = resolve_schedule_relations(scope, source, operator, schedule_index)
                complete = raw[scope, "Schedule"] is not None
                days = sum(row["service_day_object_count"] for row in matches)
                special = sum(row["special_day_object_count"] for row in matches)
                model_expected.append({"source_scope": scope, "stop_model_source_record_number": index, "route_uid": source["RouteUID"], "route_id": source["RouteID"],
                    "subroute_uid": source["SubRouteUID"], "subroute_id": source["SubRouteID"], "direction": source.get("Direction"), "operator_id": operator,
                    "route_name": source_projection(source.get("RouteName")), "geographic_status": "has_regional_stop" if city_counts else
                        "indeterminate_due_to_coordinates" if gaps else "no_regional_stop_observed",
                    "covering_city_codes": sorted(city_counts), "city_stop_membership_counts": [{"city_code": code, "stop_membership_count": city_counts[code]} for code in sorted(city_counts)],
                    "stop_membership_count": len(stop_ids), "stop_geography_ids_in_source_order": stop_ids,
                    "source_stop_sequence_values": [row.get("StopSequence") for row in source.get("Stops") or []], "coordinate_gap_counts": dict(gaps),
                    "schedule_publication_status": publication_status(len(matches), complete, operator is not None, missing_operator), "schedule_evidence_ids": [row["schedule_evidence_id"] for row in matches],
                    "unassigned_operator_schedule_evidence_ids": [row["schedule_evidence_id"] for row in unassigned],
                    "unassigned_operator_service_day_object_count": sum(row["service_day_object_count"] for row in unassigned),
                    "unassigned_operator_special_day_object_count": sum(row["special_day_object_count"] for row in unassigned),
                    "service_day_publication_status": publication_status(days, complete, operator is not None, missing_operator), "special_day_publication_status": publication_status(special, complete, operator is not None, missing_operator),
                    "service_day_object_count": days, "special_day_object_count": special,
                    "requires_source_review": missing_operator,
                    "source_review_flags": ["schedule_operator_not_published_for_three_key_candidate"] if missing_operator else [],
                    "invalid_weekly_calendar_count": sum(row["invalid_weekly_calendar_count"] for row in matches)})
    require(set(variant_stats) == set(geography_by_key), "No invented geography variants")
    for key, expected in variant_stats.items():
        expected["same_stop_uid_variant_count"] = len(uid_variants[key[0]])
        expected["requires_source_review"] = expected["coordinate_status"] != "valid_coordinates" or expected["same_stop_uid_variant_count"] > 1
        require(strict_equal(geography_by_key[key], expected), "Raw stop variant/covers classification: " + expected["stop_geography_id"])
    compare_records(documents["service_models.json.gz"]["records"], model_expected, "Operator relation service models")
    summary = []
    for scope in SCOPES:
        for code, name in CITIES.items():
            selected = [row for row in model_expected if row["source_scope"] == scope and code in row["covering_city_codes"]]
            summary.append({"source_scope": scope, "city_code": code, "city_name": name, "route_uid_count": len({row["route_uid"] for row in selected}),
                            "subroute_uid_count": len({row["subroute_uid"] for row in selected}), "operator_relation_model_count": len(selected),
                            "operator_count": len({row["operator_id"] for row in selected if row["operator_id"] is not None}),
                            **{field + "_counts": dict(Counter(row[field] for row in selected)) for field in
                               ("schedule_publication_status", "service_day_publication_status", "special_day_publication_status")}})
    compare_records(documents["coverage_summary.json"]["records"], summary, "Regional per-city summary")
    summary_meta = documents["coverage_summary.json"]["metadata"]
    verify_population_totals(summary_meta, source_model_count, len(model_expected), actual_geographies, source_memberships)
    relationship_member_sum = sum(row["stop_membership_count"] for row in model_expected)
    docs = (root / "docs/regional-service-evidence.md").read_text(encoding="utf-8")
    require(all(term in docs for term in ("covers", "StationUID", "indeterminate_partial_or_not_requested", "indeterminate_missing_schedule_operator", "not_observed_in_complete_published_endpoint", "不能把城市摘要相加", "不直接合計班表")), "Documented acceptance and aggregation boundaries")
    file_ids = {"stop_geography.json.gz": "regional_stop_geography_evidence", "service_models.json.gz": "regional_published_service_model_coverage",
                "schedule_calendars.json.gz": "regional_published_schedule_calendars", "coverage_summary.json": "regional_geographic_published_service_summary", "support.json": "regional_service_source_support"}
    reviewed = []
    for filename, dataset_id in file_ids.items():
        doc = documents[filename]
        require(doc["schema_version"] == 1 and doc["dataset_id"] == dataset_id, "Dataset identity")
        reviewed.append({"output_path": str(BASE / filename), "dataset_id": dataset_id, "record_count": len(doc["records"]),
                         "reviewed_generated_at": doc["metadata"]["generated_at"], "status": "pass_with_source_limitations"})
    return {"schema_version": 1, "dataset_id": "regional_service_evidence_independent_review", "metadata": {
        "reviewed_at": datetime.now().astimezone().isoformat(), "status": "pass_with_source_limitations", "reviewer_agent": "/root/crosswalk",
        "independent_of_producer": True, "method": "Original TDX gzip receipts/pages projected through explicit field map; NLSC original EPSG:3824 city unions with inverse-transformed WGS84 points and covers; full 5-dimension calendar join and independent totals. Producer is never imported.",
        "boundary_review": {**boundary, "operation_definition": operation_definition, "review_geographic_method": "inverse WGS84 points into native EPSG:3824 masks; no producer mask or classifier import"},
        "source_endpoint_counts": [{"source_scope": scope, "service": service, "models": len(rows) if rows is not None else None,
                                    "status": "exhausted_page_verified" if rows is not None else "not_acquired"} for (scope, service), rows in raw.items()],
        "totals": {"original_stopofroute_models": source_model_count, "operator_relation_models": len(model_expected),
                   "unique_stop_geography_variants": len(variant_stats), "original_stop_memberships": source_memberships,
                   "operator_expanded_membership_sum_not_original_population": relationship_member_sum,
                   "published_schedule_models": len(schedule_expected), "timetable_objects": sum(row["timetable_count"] for row in schedule_expected),
                   "frequency_objects": sum(row["frequency_count"] for row in schedule_expected), "service_day_objects": sum(row["service_day_object_count"] for row in schedule_expected),
                   "special_day_objects": sum(row["special_day_object_count"] for row in schedule_expected),
                   "invalid_weekly_calendar_objects": sum(row["invalid_weekly_calendar_count"] for row in schedule_expected)},
        "geography_status_counts": dict(Counter(row["geographic_status"] for row in actual_geographies)),
        "schedule_publication_status_counts": dict(Counter(row["schedule_publication_status"] for row in model_expected)),
        "schedule_operator_field_presence": [{"source_scope": scope, "schedule_models": len(raw[scope, "Schedule"] or []),
                                             "operator_id_absent_or_null_models": sum(row.get("OperatorID") is None for row in raw[scope, "Schedule"] or [])}
                                            for scope in SCOPES],
        "requires_source_review": True,
        "limitations": [schedule_support_limitation(raw),
            "Regional membership uses the 2026-08-17 NLSC village/unassigned-area union; not a separate legal city boundary version or route trajectory containment test.",
            "The used EPSG:3824 to EPSG:4326 operation reports 1 metre accuracy; source stop survey accuracy and near-boundary physical membership are not certified.",
            "Counts describe published source models, operator relations and calendar objects; neither source-model expansion nor multiple covered cities may be summed as distinct trips/routes.",
            "StationUID is absent in the official embedded model and remains null; no cross-authority station identity is constructed.",
            "ServiceDay and SpecialDays preserve exact flags/codes/dates; they do not establish actual trip operation or cause of missing source models."]}, "records": reviewed}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args()
    report = run(args.root.resolve())
    path = args.root / "data/quality/regional_service_evidence_review.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps({"status": report["metadata"]["status"], **report["metadata"]["totals"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
