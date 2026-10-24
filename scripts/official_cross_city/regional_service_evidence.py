"""Geographic and published-calendar support for retained TDX service models.

Uses retained sources only. Keeps nationwide models and source identifiers;
geographic stop membership is independent of route names and authority scope.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import gzip
import io
import json
import math
from pathlib import Path
import re
import zipfile

from .common import ROOT, now, write_json

OUT = Path("data/official_cross_city/regional_service_evidence")
INPUT = Path("data/official_cross_city/service_coverage")
SCOPES = ("Taipei", "NewTaipei", "Keelung", "InterCity")
CITIES = {"63000": "臺北市", "65000": "新北市", "10017": "基隆市"}
WEEK = ("sunday", "monday", "tuesday", "wednesday", "thursday", "friday", "saturday")


def read(path):
    with (gzip.open(path, "rt", encoding="utf-8") if path.suffix == ".gz" else path.open(encoding="utf-8")) as stream:
        return json.load(stream)


def envelope(dataset_id, records, metadata):
    return {"schema_version": 1, "dataset_id": dataset_id,
            "metadata": {"record_count": len(records), "source_updated_at": None, **metadata}, "records": records}


def write_gzip(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".part")
    try:
        temp.write_bytes(gzip.compress(json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode(), mtime=0))
        temp.replace(path)
    finally:
        temp.unlink(missing_ok=True)


def position_status(position):
    if not isinstance(position, dict) or position.get("position_lon") is None or position.get("position_lat") is None:
        return "missing_coordinates", None
    lon, lat = position["position_lon"], position["position_lat"]
    if any(isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x) for x in (lon, lat)):
        return "invalid_coordinates", None
    if not -180 <= lon <= 180 or not -90 <= lat <= 90:
        return "invalid_coordinates", None
    if lon == 0 or lat == 0:
        return "zero_coordinate_requires_source_review", None
    return "valid_coordinates", (lon, lat)


def published_status(count, endpoint_complete, missing_schedule_operator=False):
    if count:
        return "published"
    if missing_schedule_operator:
        return "indeterminate_missing_schedule_operator"
    return "not_observed_in_complete_published_endpoint" if endpoint_complete else "indeterminate_partial_or_not_requested"


def covering_cities(coordinates, masks):
    from shapely.geometry import Point
    point = Point(*coordinates)
    return [code for code, polygon in masks.items() if polygon.covers(point)]


def load_city_masks(root):
    import shapefile
    from pyproj import CRS, Transformer
    from shapely import transform
    from shapely.geometry import shape
    from shapely.ops import unary_union
    from shapely.prepared import prep
    path = root / "map-data/raw/boundary_evidence/current_villages.zip"
    receipt = read(path.with_name(path.name + ".source.json"))
    if receipt["bytes"] != path.stat().st_size or receipt["http_status"] != 200:
        raise ValueError("NLSC source receipt mismatch")
    with zipfile.ZipFile(path) as archive:
        stems = [n[:-4] for n in archive.namelist() if re.fullmatch(r"VILLAGE_NLSC_\d{7}\.shp", n)]
        if len(stems) != 1:
            raise ValueError("Ambiguous NLSC boundary release")
        stem = stems[0]
        source_crs = CRS.from_wkt(archive.read(stem + ".prj").decode("utf-8-sig"))
        if source_crs.to_epsg() != 3824:
            raise ValueError("NLSC geographic TWD97 CRS needs explicit review")
        convert = Transformer.from_crs(source_crs, 4326, always_xy=True)
        reader = shapefile.Reader(**{ext: io.BytesIO(archive.read(stem + "." + ext)) for ext in ("shp", "shx", "dbf")}, encoding="utf-8")
        groups = defaultdict(list)
        for row in reader.iterShapeRecords():
            props = row.record.as_dict()
            if props["COUNTYCODE"] in CITIES:
                geom = transform(shape(row.shape.__geo_interface__), convert.transform, interleaved=False)
                if geom.is_empty or not geom.is_valid:
                    raise ValueError("Invalid NLSC village geometry; do not silently repair")
                groups[props["COUNTYCODE"]].append(geom)
    if set(groups) != set(CITIES):
        raise ValueError("A required regional boundary is absent")
    masks = {code: prep(unary_union(geometries)) for code, geometries in groups.items()}
    version = re.search(r"_(\d{3})(\d{2})(\d{2})$", stem)
    period = f"{int(version[1])+1911:04d}-{version[2]}-{version[3]}"
    try:
        used_operation = convert.get_last_used_operation()
        operation = used_operation.description
        accuracy = used_operation.accuracy if used_operation.accuracy >= 0 else None
        operation_status = "used_operation_recorded" if accuracy is not None else "unknown_accuracy_requires_review"
    except Exception:
        operation = None; accuracy = None; operation_status = "operation_unavailable_requires_review"
    return masks, {"source": receipt, "source_layer": stem, "source_period": period, "source_crs": "EPSG:3824", "target_crs": "EPSG:4326",
                   "source_feature_counts": {code: len(geoms) for code, geoms in groups.items()},
                   "operation": operation, "operation_accuracy_m": accuracy, "operation_status": operation_status,
                   "mask_method": "Exact COUNTYCODE selection; union of all official villages and unassigned areas in each city",
                   "point_membership_predicate": "covers; points on the source boundary are included; multiple covering cities are retained"}


def calendar_nodes(model):
    nodes = []
    for field, kind in (("timetables", "timetable"), ("frequencys", "frequency")):
        for ordinal, item in enumerate(model.get(field) or [], 1):
            day = item.get("service_day")
            valid = isinstance(day, dict) and all(type(day.get(key)) is int and day[key] in (0, 1) for key in WEEK)
            nodes.append({"source_kind": kind, "source_ordinal": ordinal,
                          "trip_id": item.get("trip_id"), "service_day": day,
                          "weekly_calendar_content_status": "valid_published_flags" if valid else "not_supplied" if day is None else "requires_source_review",
                          "special_days": item.get("special_days")})
    return nodes


def input_summary(path, doc):
    meta = doc["metadata"]
    return {"path": str(path), "record_count": len(doc["records"]), "scope_status": meta["scope_status"],
            "source_updated_at": meta.get("source_updated_at"), "source_record_update_times": meta.get("source_record_update_times", []),
            "source_ids": [r["source_id"] for r in meta["sources"]],
            "source_pages": sorted({r["source_page"] for r in meta["sources"]}),
            "download_urls": [r["download_url"] for r in meta["sources"]],
            "raw_files": [r["raw_file"] for r in meta["sources"]],
            "downloaded_at": [r["downloaded_at"] for r in meta["sources"]]}


def build(root=ROOT):
    spec_path = root / "raw_data/official_cross_city/service_coverage/specifications/bus_v2.json"
    spec = read(spec_path)
    schemas = spec["components"]["schemas"]
    point_spec = schemas["PTX.Service.DTO.Shared.Specification.V2.Base.PointType"]
    stop_spec = schemas["PTX.Service.DTO.Bus.Specification.V2.Embedded.Stop"]
    if not all("WGS84" in point_spec["properties"][key]["description"] for key in ("PositionLon", "PositionLat")):
        raise ValueError("TDX stop-position CRS contract changed")
    if "StationUID" in stop_spec["properties"] or "站位ID" not in stop_spec["properties"]["StationID"]["description"]:
        raise ValueError("Station identifier semantics require review")
    masks, boundary_meta = load_city_masks(root)
    inputs = []; support = []; schedules = []; schedule_index = defaultdict(list); schedule_complete = {}
    stop_inputs = {}
    for scope in SCOPES:
        for service in ("StopOfRoute", "Schedule"):
            path = INPUT / f"{scope}_{service}.json.gz"
            if not (root / path).is_file():
                support.append({"source_scope": scope, "service": service, "publication_support": "not_acquired", "record_count": None, "input_file": None})
                continue
            doc = read(root / path)
            inputs.append(input_summary(path, doc))
            complete = doc["metadata"]["scope_status"] == "complete_published_endpoint_by_exhausted_page"
            support.append({"source_scope": scope, "service": service, "publication_support": "complete_published_endpoint" if complete else "partial_published_endpoint",
                            "record_count": len(doc["records"]), "input_file": str(path)})
            if service == "StopOfRoute":
                if not complete:
                    raise ValueError("Geographic route population requires a complete published StopOfRoute endpoint")
                stop_inputs[scope] = doc
            else:
                schedule_complete[scope] = complete
                for index, model in enumerate(doc["records"], 1):
                    operator = model.get("operator_id")
                    if operator is not None and not isinstance(operator, str):
                        raise ValueError("A published operator ID is not a string")
                    nodes = calendar_nodes(model)
                    row = {"schedule_evidence_id": f"{scope}_schedule_{index}", "source_scope": scope, "source_record_number": index,
                           "route_uid": model["route_uid"], "route_id": model["route_id"], "subroute_uid": model["sub_route_uid"],
                           "subroute_id": model["sub_route_id"], "direction": model.get("direction"), "operator_id": operator,
                           "update_time": model.get("update_time"), "version_id": model.get("version_id"),
                           "timetable_count": len(model.get("timetables") or []), "frequency_count": len(model.get("frequencys") or []),
                           "service_day_object_count": sum(n["service_day"] is not None for n in nodes),
                           "special_day_object_count": sum(len(n["special_days"] or []) for n in nodes),
                           "invalid_weekly_calendar_count": sum(n["weekly_calendar_content_status"] == "requires_source_review" for n in nodes),
                           "calendar_publications": nodes}
                    schedules.append(row)
                    schedule_index[(scope, model["route_uid"], model["sub_route_uid"], model.get("direction"), operator)].append(row)
    geographies = []; geographic_index = {}; geo_cache = {}; models = []
    stop_uid_variants = defaultdict(set)
    for scope, doc in stop_inputs.items():
        for model_index, model in enumerate(doc["records"], 1):
            variants = []; cities = set(); city_members = Counter(); uncertain = Counter()
            for stop in model.get("stops") or []:
                position = stop.get("stop_position")
                status, coordinates = position_status(position)
                raw_lon = position.get("position_lon") if isinstance(position, dict) else None
                raw_lat = position.get("position_lat") if isinstance(position, dict) else None
                # Preserve separate position/station variants of the same UID.
                variant_key = (stop["stop_uid"], stop["stop_id"], stop.get("station_id"), raw_lon, raw_lat)
                if variant_key not in geographic_index:
                    covered = []
                    if coordinates is not None:
                        if coordinates not in geo_cache:
                            geo_cache[coordinates] = covering_cities(coordinates, masks)
                        covered = geo_cache[coordinates]
                    geographic_status = ("regional_member" if covered else "outside_region") if status == "valid_coordinates" else status
                    row = {"stop_geography_id": f"stop_geography_{len(geographies)+1}", "stop_uid": stop["stop_uid"], "stop_id": stop["stop_id"],
                           "station_id": stop.get("station_id"), "station_uid": None, "station_uid_status": "not_published_by_stopofroute",
                           "source_position": {"longitude": raw_lon, "latitude": raw_lat}, "coordinate_status": status,
                           "geographic_status": geographic_status, "covering_city_codes": covered,
                           "source_scopes": [], "source_stop_names": [], "source_membership_count": 0}
                    geographic_index[variant_key] = row; geographies.append(row)
                    stop_uid_variants[stop["stop_uid"]].add(row["stop_geography_id"])
                geo = geographic_index[variant_key]
                if scope not in geo["source_scopes"]: geo["source_scopes"].append(scope)
                name = stop.get("stop_name", {}).get("zh_tw")
                if name not in geo["source_stop_names"]: geo["source_stop_names"].append(name)
                geo["source_membership_count"] += 1
                variants.append(geo["stop_geography_id"])
                cities.update(geo["covering_city_codes"]); city_members.update(geo["covering_city_codes"])
                if status != "valid_coordinates": uncertain[status] += 1
            operators = sorted({r["operator_id"] for r in model.get("operators") or []})
            if not operators: operators = [None]
            for operator in operators:
                matches = schedule_index[(scope, model["route_uid"], model["sub_route_uid"], model.get("direction"), operator)] if operator is not None else []
                unassigned = schedule_index[(scope, model["route_uid"], model["sub_route_uid"], model.get("direction"), None)]
                missing_schedule_operator = bool(unassigned) and not matches
                endpoint_complete = schedule_complete.get(scope, False) and operator is not None
                service_days = sum(r["service_day_object_count"] for r in matches)
                special_days = sum(r["special_day_object_count"] for r in matches)
                models.append({"source_scope": scope, "stop_model_source_record_number": model_index,
                    "route_uid": model["route_uid"], "route_id": model["route_id"], "subroute_uid": model["sub_route_uid"], "subroute_id": model["sub_route_id"],
                    "direction": model.get("direction"), "operator_id": operator, "route_name": model.get("route_name"),
                    "geographic_status": "has_regional_stop" if cities else "indeterminate_due_to_coordinates" if uncertain else "no_regional_stop_observed",
                    "covering_city_codes": sorted(cities), "city_stop_membership_counts": [{"city_code": code, "stop_membership_count": city_members[code]} for code in sorted(city_members)],
                    "stop_membership_count": len(variants), "stop_geography_ids_in_source_order": variants,
                    "source_stop_sequence_values": [s.get("stop_sequence") for s in model.get("stops") or []],
                    "coordinate_gap_counts": dict(uncertain),
                    "schedule_publication_status": published_status(len(matches), endpoint_complete, missing_schedule_operator),
                    "schedule_evidence_ids": [r["schedule_evidence_id"] for r in matches],
                    "unassigned_operator_schedule_evidence_ids": [r["schedule_evidence_id"] for r in unassigned],
                    "unassigned_operator_service_day_object_count": sum(r["service_day_object_count"] for r in unassigned),
                    "unassigned_operator_special_day_object_count": sum(r["special_day_object_count"] for r in unassigned),
                    "service_day_publication_status": published_status(service_days, endpoint_complete, missing_schedule_operator),
                    "special_day_publication_status": published_status(special_days, endpoint_complete, missing_schedule_operator),
                    "service_day_object_count": service_days, "special_day_object_count": special_days,
                    "requires_source_review": missing_schedule_operator,
                    "source_review_flags": ["schedule_operator_not_published_for_three_key_candidate"] if missing_schedule_operator else [],
                    "invalid_weekly_calendar_count": sum(r["invalid_weekly_calendar_count"] for r in matches)})
    for row in geographies:
        row["same_stop_uid_variant_count"] = len(stop_uid_variants[row["stop_uid"]])
        row["requires_source_review"] = row["coordinate_status"] != "valid_coordinates" or row["same_stop_uid_variant_count"] > 1
    meta = {"generated_at": now(), "source_inputs": inputs, "boundary_membership": boundary_meta, "statistical_period": None,
            "coordinate_reference_system": "EPSG:4326", "coordinate_order": ["longitude", "latitude"],
            "units": {"source_position": "longitude/latitude degrees", "*_id": "source identifier string", "source_record_number": "one-based source ordinal"},
            "tdx_specification": str(spec_path.relative_to(root)),
            "identifier_semantics": {"stop_uid": stop_spec["properties"]["StopUID"]["description"], "stop_id": stop_spec["properties"]["StopID"]["description"],
                                     "station_id": stop_spec["properties"]["StationID"]["description"], "station_uid": "Not supplied by this model; null, never constructed from StationID"},
            "join_key": ["source_scope", "route_uid", "subroute_uid", "direction", "operator_id"],
            "independent_review_status": "pending", "license": {"name": "TDX資料使用條款", "url": "https://tdx.transportdata.tw/term"},
            "boundary_license": {"name": "政府資料開放授權條款-第1版", "url": "https://data.gov.tw/license", "attribution": "內政部國土測繪中心"},
            "limitations": ["以完整發布StopOfRoute的站點核地理會員，保留全國模型；沒有區域內停靠點不表示路線軌跡不經過該區。",
                            "行政遮罩為同一NLSC版本全部里及未編定區塊的聯集，非另版法定市界；座標轉換精度見boundary_membership，不推定人口月份幾何相同。",
                            "站牌UID明示；StationID是站位ID，不補造StationUID，也不連接地方feed或以同名去重。",
                            "not_observed只描述完整發布端點未見對應模型或欄位，不代表停駛；未取得或部分班表為indeterminate。",
                            "Schedule未發布OperatorID的三碼候選另列，不向下套用StopOfRoute業者；無明示五維匹配時為indeterminate_missing_schedule_operator，候選日曆不計入已核業者發布數。",
                            "ServiceDay/SpecialDays只統計來源發布物件並保留原碼與日期，不推定某班實際行駛或將不同業者班表直接相加。",
                            "涵蓋的主管機關、端點與期間僅限support.json所列；不同來源UpdateTime分開保留。"]}
    write_gzip(root / OUT / "stop_geography.json.gz", envelope("regional_stop_geography_evidence", geographies, {**meta, "grain": "one exact StopUID/StopID/StationID/source-position variant"}))
    write_gzip(root / OUT / "service_models.json.gz", envelope("regional_published_service_model_coverage", models, {**meta, "grain": "one retained StopOfRoute model times each explicitly published operator relation"}))
    write_gzip(root / OUT / "schedule_calendars.json.gz", envelope("regional_published_schedule_calendars", schedules, {**meta, "grain": "one retained published Schedule model; every timetable/frequency calendar node retained, source StopTimes remain in source input"}))
    summaries = []
    for scope in stop_inputs:
        for code, name in CITIES.items():
            selected = [r for r in models if r["source_scope"] == scope and code in r["covering_city_codes"]]
            summaries.append({"source_scope": scope, "city_code": code, "city_name": name,
                "route_uid_count": len({r["route_uid"] for r in selected}), "subroute_uid_count": len({r["subroute_uid"] for r in selected}),
                "operator_relation_model_count": len(selected), "operator_count": len({r["operator_id"] for r in selected if r["operator_id"] is not None}),
                "schedule_publication_status_counts": dict(Counter(r["schedule_publication_status"] for r in selected)),
                "service_day_publication_status_counts": dict(Counter(r["service_day_publication_status"] for r in selected)),
                "special_day_publication_status_counts": dict(Counter(r["special_day_publication_status"] for r in selected))})
    write_json(root / OUT / "coverage_summary.json", envelope("regional_geographic_published_service_summary", summaries, {**meta,
               "grain": "one retained source authority scope times one covered city; a model can contribute to multiple cities", "source_model_count": sum(len(x["records"]) for x in stop_inputs.values()),
               "operator_relation_model_count": len(models), "stop_geography_variant_count": len(geographies),
               "stop_geography_status_counts": dict(Counter(r["geographic_status"] for r in geographies)),
               "source_stop_membership_count": sum(r["source_membership_count"] for r in geographies)}))
    write_json(root / OUT / "support.json", envelope("regional_service_source_support", support, {**meta, "grain": "one configured source scope and endpoint", "configured_scopes": list(SCOPES)}))
    return {"source_model_count": sum(len(x["records"]) for x in stop_inputs.values()), "operator_relation_models": len(models),
            "stop_geography_variants": len(geographies), "geographic_status_counts": dict(Counter(r["geographic_status"] for r in geographies)),
            "regional_summary": summaries}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--offline", action="store_true")
    args = parser.parse_args()
    if not args.offline:
        parser.error("--offline is required; this script never requests API data")
    print(json.dumps(build(), ensure_ascii=False, indent=2))
