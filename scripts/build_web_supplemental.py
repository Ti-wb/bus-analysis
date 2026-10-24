#!/usr/bin/env python3
"""Build regional reference layers from reviewed local data; never fetch sources."""

import argparse
import gzip
import json
import math
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from shapely.geometry import mapping, shape

ROOT = Path(__file__).resolve().parents[1]
CROSS = "data/official_cross_city"
TDX_TERMS = {"name": "TDX 服務條款", "url": "https://tdx.transportdata.tw/term"}
SEQUENCE_STATUSES = {
    "verified_full_stop_id_sequence",
    "verified_order_with_explicit_non_boarding_virtual_points_omitted",
}


def read(root, path):
    target = root / path
    if target.suffix == ".gz":
        with gzip.open(target, "rt", encoding="utf-8") as stream:
            return json.load(stream)
    return json.loads(target.read_text(encoding="utf-8"))


def write(out, name, doc):
    out.mkdir(parents=True, exist_ok=True)
    target = out / name
    target.write_text(json.dumps(doc, ensure_ascii=False, separators=(",", ":"), allow_nan=False), encoding="utf-8")
    print(f"{name}: {target.stat().st_size / 1e6:.2f} MB")


def source_info(root, path, doc=None):
    """Keep source URLs and original times without mistaking a download for an update."""
    doc = doc if doc is not None else read(root, path)
    meta = doc["metadata"]
    sources = meta.get("sources", [])
    if isinstance(meta.get("source"), dict):
        sources = [*sources, meta["source"]]
    return {
        "path": path,
        "dataset_id": doc.get("dataset_id"),
        "source_updated_at": meta.get("source_updated_at", meta.get("source_data_time")),
        "period": meta.get("period", meta.get("source_period", meta.get("statistical_period"))),
        "sources": [{key: source.get(key) for key in (
            "source_page", "page_url", "download_url", "requested_url", "url", "raw_file",
            "source_updated_at", "source_data_time", "downloaded_at",
        ) if key in source} for source in sources],
    }


def feature(ident, properties, geometry):
    return {"type": "Feature", "id": ident, "properties": properties, "geometry": geometry}


def collection(features, metadata):
    return {"type": "FeatureCollection", "metadata": {"crs": "EPSG:4326", "coordinate_order": "[longitude, latitude]", **metadata}, "features": features}


def coordinate(stop):
    position = stop.get("stop_position") or {}
    lon, lat = position.get("position_lon"), position.get("position_lat")
    if (isinstance(lon, (int, float)) and not isinstance(lon, bool)
            and isinstance(lat, (int, float)) and not isinstance(lat, bool)
            and math.isfinite(lon) and math.isfinite(lat) and -180 <= lon <= 180 and -90 <= lat <= 90):
        return [lon, lat]
    return None


def regional_bus(root):
    paths = [f"{CROSS}/service_coverage/NewTaipei_StopOfRoute.json.gz",
             f"{CROSS}/id_evidence/ntpc_tdx_stop_sequences.json.gz",
             f"{CROSS}/ntpc_leapfrog_crosswalk.json"]
    reference, evidence, leapfrog = (read(root, path) for path in paths)
    evidence_keys = {(r["route_uid"], r["subroute_uid"], str(r["direction"])): r
                     for r in evidence["records"] if r["status"] in SEQUENCE_STATUSES}
    leapfrog_keys = {(r["route_id"], subroute) for r in leapfrog["records"]
                     if r["linkage_status"] == "verified_explicit_category_nid_and_getroute_ids"
                     for subroute in r["subroute_ids"]}
    routes, stop_variants, stop_routes, stop_leapfrog = [], {}, defaultdict(dict), defaultdict(bool)
    missing_coordinate_memberships = 0
    for model in reference["records"]:
        key = (model["route_uid"], model["sub_route_uid"], str(model["direction"]))
        verified = evidence_keys.get(key)
        if verified is None:
            continue
        # The evidence's literal TDX sequence must still match this retained snapshot.
        if verified["tdx_stop_ids_in_source_order"] != [s["stop_id"] for s in model["stops"]]:
            raise ValueError(f"New Taipei station sequence evidence drifted: {key}")
        is_leapfrog = (model["route_id"], model["sub_route_id"]) in leapfrog_keys
        name = model["route_name"].get("zh_tw") or model["route_uid"]
        ident = ":".join(("NWT", *key))
        parts, part = [], []
        for stop in model["stops"]:
            point = coordinate(stop)
            if point is None:
                missing_coordinate_memberships += 1
                if len(part) >= 2:
                    parts.append(part)
                part = []
                continue
            part.append(point)
            # Retain differing coordinates as separate variants rather than picking one.
            variant = (stop["stop_uid"], *point)
            stop_variants.setdefault(variant, stop)
            stop_routes[variant][model["route_uid"]] = name
            stop_leapfrog[variant] |= is_leapfrog
        if len(part) >= 2:
            parts.append(part)
        if parts:
            routes.append(feature(ident, {
                "id": ident, "route_id": model["route_id"], "route_uid": model["route_uid"],
                "subroute_id": model["sub_route_id"], "subroute_uid": model["sub_route_uid"],
                "name": name, "subroute_name": model["sub_route_name"].get("zh_tw"),
                "direction": str(model["direction"]), "is_leapfrog": is_leapfrog,
                "source_scope": "NewTaipei", "source_updated_at": model.get("update_time"),
                "geometry_kind": "stop_sequence_schematic", "stop_count": len(model["stops"]),
                "sequence_status": verified["status"],
            }, {"type": "MultiLineString", "coordinates": parts}))
    variant_counts = Counter(key[0] for key in stop_variants)
    stops = []
    for key, stop in stop_variants.items():
        uid, lon, lat = key
        ident = uid if variant_counts[uid] == 1 else f"{uid}:{lon}:{lat}"
        stops.append(feature(ident, {
            "id": ident, "stop_uid": uid, "stop_id": stop["stop_id"],
            "name": stop["stop_name"].get("zh_tw") or uid, "source_scope": "NewTaipei",
            "route_count": len(stop_routes[key]), "routes": sorted(stop_routes[key].values()),
            "route_ids": sorted(stop_routes[key]), "is_leapfrog": stop_leapfrog[key],
            "weekday_departures": None, "source_updated_at": reference["metadata"].get("source_updated_at"),
            "coordinate_variant_count": variant_counts[uid],
        }, {"type": "Point", "coordinates": [lon, lat]}))
    metadata = {
        "sources": [source_info(root, path, doc) for path, doc in zip(paths, (reference, evidence, leapfrog))],
        "source_updated_at": reference["metadata"].get("source_updated_at"),
        "period": None, "data_kind": "retained_published_station_sequence_snapshot",
        "license": TDX_TERMS,
        "identity_policy": "Exact route UID + subroute UID + direction and literal station sequence verified by retained ntpc_tdx_stop_sequences evidence; leapfrog category uses explicit IDs only.",
        "missing_coordinate_memberships": missing_coordinate_memberships,
        "limitations": [
            "依官方已核對站序連接的示意線，不是道路線形、實際車輛位置或當日營運證明。",
            "只增加新北官方發布模型；跳蛙依已核實的官方類別 ID，不依路線名稱猜測。",
            "站點保留新北 stop_uid 命名空間及座標變體，不以名稱或鄰近位置合併臺北站點。",
            "不將新北站點加入既有臺北市班次、覆蓋或需求模型；新北 weekday_departures 為 null。",
        ],
    }
    return collection(routes, metadata), collection(stops, metadata)


def rounded_geometry(geometry, digits=6):
    def walk(value):
        if value and isinstance(value[0], (int, float)):
            return [round(value[0], digits), round(value[1], digits)]
        return [walk(item) for item in value]
    geo = mapping(geometry)
    return {"type": geo["type"], "coordinates": walk(geo["coordinates"])}


def villages(root):
    pop_path = f"{CROSS}/population_2026-09.json"
    cross_path = f"{CROSS}/population_boundary_crosswalk.json"
    boundary_paths = ["map-data/admin/villages.geojson", "map-data/admin/keelung_villages.geojson"]
    population, crosswalk = read(root, pop_path), read(root, cross_path)
    counts = {r["source_area_code"]: r for r in population["records"]}
    links = {r["boundary_village_code"]: r for r in crosswalk["records"]}
    result = []
    for path in boundary_paths:
        for source in read(root, path)["features"]:
            props = source["properties"]
            code = props.get("admin_code", props.get("village_code"))
            link = links.get(code)
            status = link["status"] if link else "boundary_only_without_population"
            record = counts.get(link["population_area_code"]) if link and status == "matched_code_and_names" else None
            if record is not None and (record["population"] != link["population"]
                                       or record["period"] != link["population_period"]):
                raise ValueError(f"Population crosswalk drifted: {code}")
            people = record["population"] if record else None
            elderly = record["population_age_65_plus"] if record else None
            result.append(feature(code, {
                "id": code, "name": props.get("name", props.get("village_name")),
                "city_code": props.get("city_code", props.get("county_code")),
                "city_name": props.get("city_name", props.get("county_name")),
                "district_name": props["district_name"],
                **{field: record[field] if record else None for field in (
                    "population", "households", "population_age_0_14", "population_age_15_64", "population_age_65_plus")},
                "elderly_share": elderly / people if people and elderly is not None else None,
                "period": population["metadata"]["period"],
                "boundary_period": crosswalk["metadata"]["boundary_source_period"],
                "join_status": status,
            }, rounded_geometry(shape(source["geometry"]))))
    metadata = {
        "sources": [source_info(root, path) for path in (pop_path, cross_path, *boundary_paths)],
        "source_updated_at": population["metadata"].get("source_data_time"),
        "period": population["metadata"]["period"],
        "boundary_period": crosswalk["metadata"]["boundary_source_period"],
        "data_kind": "registered_village_population", "units": {"population": "人", "households": "戶", "elderly_share": "比例 0–1"},
        "join_status_counts": dict(Counter(f["properties"]["join_status"] for f in result)),
        "license": population["metadata"]["license"],
        "geometry_processing": "Original official ring vertices retained; rounded to six decimals for web display.",
        "limitations": [
            "戶籍人口不是日間活動人口或公車需求；里年齡不是站點 350 m 範圍的年齡。",
            "只連接已核對代碼及名稱的里；異體字待核對與未編定里區塊保留 null，不補零。",
            "人口月份與里界版本不同，尚未證明人口月份當時幾何完全相同。",
            "里界僅為顯示將原始頂點四捨五入至小數六位；位於邊界附近的點位歸屬需再核對。",
        ],
    }
    return collection(result, metadata)


def local_date(value):
    try:
        stamp = datetime.fromisoformat(value)
        return stamp.astimezone(ZoneInfo("Asia/Taipei")).date().isoformat() if stamp.tzinfo is not None else None
    except (ValueError, TypeError):
        return None


def traffic_snapshot_join(definition, observation):
    """Bound the native ID association to one same-authority same-day snapshot."""
    dmeta, ometa = (doc["metadata"]["response_metadata"] for doc in (definition, observation))
    drows, orows = definition["records"], observation["records"]
    dids, oids = [r.get("section_id") for r in drows], [r.get("section_id") for r in orows]
    didx, oidx = {r.get("section_id"): r for r in drows}, {r.get("section_id"): r for r in orows}
    dates = [local_date(meta.get(field)) for meta in (dmeta, ometa) for field in ("update_time", "src_update_time")]

    def link_ids(row):
        values = [item.get("link_id") for item in row.get("link_ids", [])]
        return sorted(values) if values and all(isinstance(v, str) and v for v in values) else None

    checks = {
        "same_nonempty_authority": bool(dmeta.get("authority_code")) and dmeta.get("authority_code") == ometa.get("authority_code"),
        "all_source_times_same_offset_known_local_day": None not in dates and len(set(dates)) == 1,
        "unique_nonempty_definition_ids": bool(dids) and all(isinstance(v, str) and v for v in dids) and len(dids) == len(didx),
        "unique_nonempty_observation_ids": bool(oids) and all(isinstance(v, str) and v for v in oids) and len(oids) == len(oidx),
        "bidirectional_exact_section_id_sets": set(dids) == set(oids),
        "nonempty_exact_link_ids_for_every_section": set(dids) == set(oids) and all(
            link_ids(row) is not None and link_ids(row) == link_ids(oidx[ident]) for ident, row in didx.items()),
        "all_collect_times_in_source_local_day": bool(dates[0]) and all(local_date(r.get("data_collect_time")) == dates[0] for r in orows),
    }
    return (oidx if all(checks.values()) else {}), checks


def traffic(root):
    definition_path = "data/tdx_road_traffic/observations/current_section.json"
    observation_path = "data/tdx_road_traffic/observations/current_section_live.json"
    diagnostic_path = "data/tdx_road_traffic/reference_diagnostics.json"
    definition, observation, diagnostic = (read(root, path) for path in (definition_path, observation_path, diagnostic_path))
    joined, checks = traffic_snapshot_join(definition, observation)
    join_status = "same_authority_same_day_exact_section_and_link_ids_snapshot" if joined else "snapshot_identity_gate_failed"
    features, excluded = [], 0
    for record in definition["records"]:
        ends = [coordinate({"stop_position": record.get(field)}) for field in ("section_start", "section_end")]
        if any(point is None for point in ends):
            excluded += 1
            continue
        ident = record["section_id"]
        observed = joined.get(ident, {})
        speed, seconds = observed.get("travel_speed"), observed.get("travel_time")
        valid_speed = isinstance(speed, (int, float)) and not isinstance(speed, bool) and math.isfinite(speed) and speed > 0
        valid_seconds = isinstance(seconds, (int, float)) and not isinstance(seconds, bool) and math.isfinite(seconds) and seconds > 0
        value_status = ("positive_observed_value" if valid_speed else "source_zero_not_interpreted" if speed == 0
                        else "source_negative_sentinel" if isinstance(speed, (int, float)) and speed < 0 else "missing_or_unjoined")
        features.append(feature(ident, {
            "id": ident, "name": record.get("section_name"), "road_name": record.get("road_name"),
            "direction": record.get("road_direction"), "travel_speed": speed if valid_speed else None,
            "travel_time": seconds if valid_speed and valid_seconds else None,
            "raw_travel_speed": speed, "raw_travel_time": seconds, "value_status": value_status,
            "congestion_level": observed.get("congestion_level"), "data_sources": observed.get("data_sources"),
            "data_collect_time": observed.get("data_collect_time"), "join_status": join_status,
            "definition_source_updated_at": definition["metadata"].get("source_updated_at"),
            "observation_source_updated_at": observation["metadata"].get("source_updated_at"),
            "geometry_kind": "section_endpoint_schematic",
        }, {"type": "LineString", "coordinates": ends}))
    metadata = {
        "sources": [source_info(root, path, doc) for path, doc in zip(
            (definition_path, observation_path, diagnostic_path), (definition, observation, diagnostic))],
        "source_updated_at": definition["metadata"].get("source_updated_at"),
        "period": observation["metadata"]["statistical_period"], "data_kind": "retained_road_speed_snapshot",
        "units": {"travel_speed": "km/h", "travel_time": "秒"}, "license": TDX_TERMS,
        "observation_record_count": len(observation["records"]),
        "observation_source_updated_at": observation["metadata"].get("source_updated_at"),
        "reference_diagnostics": diagnostic["records"],
        "join_status": join_status, "identity_checks": checks, "snapshot_only": True,
        "version_equivalence_verified": False,
        "definition_response_metadata": definition["metadata"]["response_metadata"],
        "observation_response_metadata": observation["metadata"]["response_metadata"],
        "excluded_incomplete_endpoint_count": excluded,
        "value_status_counts": dict(Counter(f["properties"]["value_status"] for f in features)),
        "limitations": [
            "圖上為發布路段起訖點連線，不是實際道路線形。",
            "只在同機關、同日、雙向唯一 SectionID 與每筆非空 LinkIDs 完全相同時連接；未證明版本等同，不套用其他日期。",
            "來源 0 與負數原樣保留為 raw 值；意義未確認，不當作一般速率著色，顯示值為 null。",
            "已取得的速率快照時間見 period；本站不自動更新，不是即時路況。",
        ],
    }
    return collection(features, metadata)


def minutes(value):
    try:
        h, m = (int(v) for v in value.split(":"))
    except (AttributeError, TypeError, ValueError):
        return None
    return h * 60 + m if 0 <= h <= 24 and 0 <= m < 60 and (h < 24 or m == 0) else None


def hourly_windows(rows):
    """Use windows containing each whole hour; conflicting values stay unknown."""
    values = [set() for _ in range(24)]
    for row in rows:
        start, end = minutes(row.get("start_time")), minutes(row.get("end_time"))
        runtime = row.get("run_time")
        if (start is None or end is None or start >= end or isinstance(runtime, bool)
                or not isinstance(runtime, (int, float)) or not math.isfinite(runtime) or runtime < 0):
            continue
        for hour in range(24):
            if start <= hour * 60 < end:
                values[hour].add(runtime)
    return [next(iter(v)) if len(v) == 1 else None for v in values], sum(len(v) > 1 for v in values)


def travel_times(root):
    base = f"{CROSS}/travel_time_coverage"
    reference_path = f"{CROSS}/service_coverage/Taipei_StopOfRoute.json.gz"
    pairs_path, premium_path, inventory_path = (f"{base}/{name}" for name in (
        "pair_coverage.json.gz", "premium_models.json.gz", "source_inventory.json"))
    reference = read(root, reference_path)
    pairs, premium, inventory = (read(root, path) for path in (pairs_path, premium_path, inventory_path))
    instances = {row["premium_instance_id"]: row for row in premium["records"]}
    source_files = {row["source_id"]: row["output_file"] for row in inventory["records"]
                    if row.get("status") == "downloaded" and row.get("output_file")}
    # Cache only the million source rows during this one build, and discard them on return.
    documents = {}
    grouped = defaultdict(dict)
    for pair in pairs["records"]:
        if pair["status"] == "matched" and not pair.get("requires_source_review"):
            grouped[pair["pair_occurrence_id"]][pair["service_tag"]] = pair
    features, conflicts, no_coordinate, without_runtime = [], 0, 0, 0
    effective_dates, expire_dates, update_times = set(), set(), set()
    for ident, services in grouped.items():
        pair = next(iter(services.values()))
        model_number = int(pair["reference_model_id"].split(":")[-1])
        model = reference["records"][model_number - 1]
        if (model["route_uid"], model["sub_route_uid"], model["direction"]) != (
                pair["route_uid"], pair["sub_route_uid"], pair["direction"]):
            raise ValueError(f"Travel time reference model changed: {ident}")
        from_stop, to_stop = (model["stops"][pair[field] - 1] for field in (
            "from_source_record_number", "to_source_record_number"))
        if (from_stop["stop_id"], to_stop["stop_id"]) != (pair["from_stop_id"], pair["to_stop_id"]):
            raise ValueError(f"Travel time pair identity changed: {ident}")
        coords = [coordinate(from_stop), coordinate(to_stop)]
        if any(point is None for point in coords):
            no_coordinate += 1
            continue
        fields, dates, expires, updates = {}, set(), set(), set()
        for tag, field in (("1", "weekday"), ("0", "weekend")):
            source_rows = []
            service_pair = services.get(tag)
            if service_pair:
                for group in service_pair["source_rows_by_premium_instance"]:
                    instance = instances[group["premium_instance_id"]]
                    source_id = instance["source_id"]
                    if source_id not in documents:
                        documents[source_id] = read(root, source_files[source_id])
                    source_model = documents[source_id]["records"][instance["source_model_number"] - 1]
                    if (source_model["route_uid"], source_model["sub_route_uid"], source_model["direction"]) != (
                            pair["route_uid"], pair["sub_route_uid"], pair["direction"]):
                        raise ValueError(f"Premium model evidence changed: {ident}")
                    for row_number in group["usable_numeric_window_row_numbers"]:
                        row = source_model["travel_times"][row_number - 1]
                        if (row["from_stop_id"], row["to_stop_id"], row["service_tag"]) != (
                                pair["from_stop_id"], pair["to_stop_id"], tag):
                            raise ValueError(f"Premium row evidence changed: {ident}, {row_number}")
                        source_rows.append(row)
                    for value, target in ((source_model.get("effective_date"), dates),
                                          (source_model.get("expire_date"), expires),
                                          (source_model.get("update_time"), updates)):
                        if value is not None:
                            target.add(value)
            fields[field], conflict_count = hourly_windows(source_rows)
            conflicts += conflict_count
        if not any(v is not None for values in fields.values() for v in values):
            without_runtime += 1
            continue
        effective_dates.update(dates)
        expire_dates.update(expires)
        update_times.update(updates)
        features.append(feature(ident, {
            "id": ident, "route_uid": pair["route_uid"], "subroute_uid": pair["sub_route_uid"],
            "route_name": model["route_name"].get("zh_tw"), "direction": str(pair["direction"]),
            "from_stop_id": from_stop["stop_id"], "to_stop_id": to_stop["stop_id"],
            "from_name": from_stop["stop_name"].get("zh_tw"), "to_name": to_stop["stop_name"].get("zh_tw"),
            **fields, "effective_date": next(iter(dates)) if len(dates) == 1 else None,
            "expire_date": next(iter(expires)) if len(expires) == 1 else None,
            "source_updated_at": next(iter(updates)) if len(updates) == 1 else None,
            "geometry_kind": "adjacent_stop_schematic",
        }, {"type": "LineString", "coordinates": coords}))
    metadata = {
        "sources": [source_info(root, path, doc) for path, doc in zip(
            (reference_path, pairs_path, premium_path, inventory_path), (reference, pairs, premium, inventory))],
        "premium_sources": [{"source_id": row["source_id"], "download_url": row.get("download_url"),
                             "downloaded_at": row.get("downloaded_at"), "statistical_period": row.get("statistical_period"),
                             "output_file": row.get("output_file")} for row in inventory["records"]],
        "source_updated_at": None, "source_update_times": sorted(update_times),
        "period": {"effective_dates": sorted(effective_dates), "expire_dates": sorted(expire_dates),
                   "historical_input_period": "近3個月；精確起訖未由來源給定"},
        "data_kind": "historically_estimated_adjacent_station_time", "units": {"weekday": "秒", "weekend": "秒"},
        "license": TDX_TERMS, "source_pair_service_count": len(pairs["records"]),
        "conflicting_hour_value_count": conflicts, "pair_without_coordinate_count": no_coordinate,
        "pair_without_hour_value_count": without_runtime,
        "hour_policy": "Array index h uses a verified source window with start <= h:00 < end. Different numeric values at one hour produce null; no averaging or summing. service_tag 1 = weekday, 0 = weekend.",
        "identity_policy": "Only matched pair evidence without requires_source_review; exact premium_instance_id/source_model_number/source_row_number and reference model/stop occurrence are rechecked.",
        "limitations": [
            "以近三個月資料推估的當期平日／週末站間旅時，不是當日實測、即時路況或三年歷史。",
            "只顯示已核對相鄰站對及可用時窗；缺值、未覆蓋與衝突值為 null，不代表零秒或沒有服務。",
            "整點取其所在來源時窗；週末不可自行擴充成國定假日，24:00 僅作來源時窗終點。",
            "站間直線是示意線；站序快照與旅時推估版本不同，不代表已驗證當日實際行駛。",
            "來源更新時間未帶 UTC offset 時原樣保留；不將下載時間當作來源更新時間。",
        ],
    }
    return collection(features, metadata)


def build_all(root=ROOT, out=None, built_at=None):
    out = out if out is not None else root / "web/public/data"
    built_at = built_at or datetime.now(timezone.utc).isoformat(timespec="seconds")
    routes, stops = regional_bus(root)
    outputs = [
        ("regional_bus_routes", "新北／跳蛙路線", routes),
        ("regional_bus_stops", "新北／跳蛙站點", stops),
        ("villages", "北北基里人口與年齡", villages(root)),
        ("traffic_sections", "道路速率快照", traffic(root)),
        ("bus_travel_times", "已核對站間旅時", travel_times(root)),
    ]
    records = []
    for ident, title, doc in outputs:
        name = f"{ident}.geojson"
        doc["metadata"]["built_at"] = built_at
        doc["metadata"]["independent_review_report"] = "data/quality/web_supplemental_review.json"
        write(out, name, doc)
        records.append({"id": ident, "title": title, "file": name, "feature_count": len(doc["features"]),
                        "source_updated_at": doc["metadata"].get("source_updated_at"),
                        "period": doc["metadata"].get("period"),
                        "status": ("bounded_snapshot_join_version_unverified" if doc["metadata"]["join_status"].startswith("same_authority")
                                   else "definition_only_identity_gate_failed") if ident == "traffic_sections" else "available_snapshot",
                        "limitations": doc["metadata"]["limitations"], "sources": doc["metadata"]["sources"]})
    manifest = {"schema_version": "1.0", "dataset_id": "web_supplemental_layers", "metadata": {
        "built_at": built_at, "source_updated_at": None, "grain": "one derived web layer",
        "independent_review_report": "data/quality/web_supplemental_review.json",
        "limitations": ["補充圖層為已保存的來源快照，網站不自動輪詢即時資料。",
                        "既有班次、覆蓋及需求模型仍以臺北市為分析範圍，補充顯示不等於已納入模型。"],
    }, "records": records}
    write(out, "supplemental_manifest.json", manifest)
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "web/public/data")
    args = parser.parse_args()
    build_all(out=args.output)


if __name__ == "__main__":
    main()
