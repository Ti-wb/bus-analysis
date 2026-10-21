#!/usr/bin/env python3
"""Fetch/normalize the 14 public Taipei bus feeds. Rebuild with --offline.

Source bytes and download metadata are kept in raw_data/taipei_feeds. Normalized records
and GeoJSON live in data/taipei_feeds. Every conversion is explicitly typed;
source identifiers are never inferred from route names or from another API.
"""
import argparse
import collections
import concurrent.futures
from datetime import datetime
import gzip
import json
import math
from pathlib import Path
import re
import subprocess
from urllib.parse import urlparse
import xml.etree.ElementTree as ET
from zoneinfo import ZoneInfo

from shapely import from_wkt
from shapely.geometry import mapping

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "raw_data/taipei_feeds"
OUT = ROOT / "data/taipei_feeds"
TZ = ZoneInfo("Asia/Taipei")
BASE = "https://tcgbusfs.blob.core.windows.net/blobbus/"
SPEC_PAGE = "https://pto.gov.taipei/News_Content.aspx?n=A1DF07A86105B6BB&s=55E8ADD164E4F579"
SPEC_URL = "https://www-ws.gov.taipei/001/Upload/458/relfile/22545/6554360/5daa0a37-8de7-4455-856b-de14a3b8a327.pdf"
NAMES = ["GetRoute", "GetPathDetail", "GetStop", "GetStopLocation", "GetBusShape", "GetTimeTable", "GetSemiTimeTable", "GetSpecTimeTable", "GetBusData", "GetBusEvent", "BusSeatEvent", "GetEstimateTime", "GetBusVehicleList", "GetOrgPathAttribute"]
DYNAMIC = {"GetBusData", "GetBusEvent", "BusSeatEvent", "GetEstimateTime"}
MISSING = {"", "null", "None", "N/A"}
DIRECTION = {"0": "outbound", "1": "inbound", "2": "unknown"}
CAR_TYPE = {"0": "regular", "1": "low_floor", "2": "accessible_bus", "3": "dog_friendly"}
BUS_STATUS = {"0": "normal", "1": "accident", "2": "breakdown", "3": "congestion", "4": "emergency", "5": "refueling", "99": "not_in_service"}
ETA_STATUS = {-1: "not_departed", -2: "not_stopping_traffic_control", -3: "service_ended", -4: "not_operating_today"}
BASE_LIMITATIONS = ["研究聚焦臺北市，保留臺北市主管機關來源完整範圍，包含跨市界服務；未依市界裁切，未額外下載新北市專屬 feed。", "不同 feed 並非交易式同步，跨表缺漏需保留；下載時間不代替資料時間。"]


def now():
    return datetime.now(TZ).isoformat(timespec="seconds")


def dump(p, obj):
    p.parent.mkdir(parents=True, exist_ok=True)
    temporary = p.with_suffix(p.suffix + ".part")
    temporary.write_text(json.dumps(obj, ensure_ascii=False, allow_nan=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(p)


def download(url, path, source_page):
    temporary = path.with_suffix(path.suffix + ".part")
    headers = path.with_suffix(path.suffix + ".headers")
    cmd = ["curl", "--fail", "--location", "--silent", "--show-error", "--proto", "=https", "--proto-redir", "=https", "--connect-timeout", "20", "--max-time", "120", "--retry", "2", "--dump-header", str(headers), "--output", str(temporary), "--write-out", "%{json}", url]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=360)
        if result.returncode:
            raise RuntimeError(path.name + ": " + result.stderr.strip())
        info = json.loads(result.stdout)
        if urlparse(info["url_effective"]).hostname not in {"tcgbusfs.blob.core.windows.net", "www-ws.gov.taipei"}:
            raise ValueError("Unexpected redirect")
        body = temporary.read_bytes()
        if path.suffix == ".gz":
            if not body.startswith(b"\x1f\x8b"):
                raise ValueError(path.name + ": expected gzip")
            decoded = gzip.decompress(body).decode("utf-8-sig")
            if decoded.lstrip().startswith(("{", "[")):
                json.loads(decoded)
            else:
                ET.fromstring(decoded)
        elif path.suffix == ".pdf" and not body.startswith(b"%PDF"):
            raise ValueError("Expected specification PDF")
        response_headers = {}
        for line in headers.read_text().splitlines():
            if line.startswith("HTTP/"):
                response_headers = {}
            elif ":" in line:
                key, value = line.split(":", 1)
                response_headers[key.lower()] = value.strip()
        temporary.replace(path)
        return {"source_page": source_page, "download_url": url, "resolved_url": info["url_effective"], "raw_file": str(path.relative_to(ROOT)), "downloaded_at": now(), "http_last_modified": response_headers.get("last-modified"), "bytes": path.stat().st_size}
    finally:
        temporary.unlink(missing_ok=True)
        headers.unlink(missing_ok=True)


def fetch(name):
    return name, download(BASE + name + ".gz", RAW / (name + ".gz"), SPEC_PAGE)


def timestamp(value):
    if value is None or str(value).strip() in MISSING:
        return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value / 1000, TZ).isoformat(timespec="milliseconds")
    dt = datetime.fromisoformat(str(value).strip().replace("/", "-").replace("Z", "+00:00"))
    return (dt.replace(tzinfo=TZ) if dt.tzinfo is None else dt).isoformat()


def read_raw(name):
    raw = gzip.decompress((RAW / (name + ".gz")).read_bytes()).decode("utf-8-sig")
    return json.loads(raw) if raw.lstrip().startswith(("{", "[")) else ET.fromstring(raw)


def snake(value):
    value = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", value)
    return re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", value).lower()


def convert(row, renames=None, numbers=(), booleans=(), times=(), timestamps=(), extra_keys=()):
    """Retain missing symbols and invalid values separately, without zero fill."""
    renames = renames or {}
    out, missing, invalid, flags = {}, {}, {}, []
    for key in dict.fromkeys([*row, *extra_keys]):
        name = renames.get(key, snake(key))
        value = row.get(key)
        if value is None or (isinstance(value, str) and value.strip() in MISSING):
            out[name] = None
            missing[name] = value if key in row else {"status": "field_absent"}
            continue
        text = str(value).strip()
        try:
            if key in numbers:
                number = float(text)
                if not math.isfinite(number):
                    raise ValueError("non-finite")
                out[name] = int(number) if number.is_integer() else number
            elif key in booleans:
                if text not in {"0", "1"}:
                    raise ValueError("invalid boolean")
                out[name] = text == "1"
            elif key in times:
                if re.fullmatch(r"\d{4}", text):
                    text = text[:2] + ":" + text[2:]
                if not re.fullmatch(r"\d{2}:\d{2}", text) or int(text[3:]) >= 60 or int(text[:2]) > 47:
                    raise ValueError("invalid service-day time")
                out[name] = text
            elif key in timestamps:
                out[name] = timestamp(value)
            elif isinstance(value, (dict, list)):
                raise ValueError("unexpected nested value")
            else:
                out[name] = text
        except (ValueError, TypeError, OverflowError):
            out[name] = None
            invalid[name] = value
            flags.append(name + ":invalid_source_value")
    if missing:
        out["source_missing_values"] = missing
    if invalid:
        out["source_invalid_values"] = invalid
    if flags:
        out["quality_flags"] = flags
    return out


def flag(record, reason):
    record.setdefault("quality_flags", []).append(reason)


def code_label(record, field, values, label=None):
    value = record.get(field)
    record[label or field.removesuffix("_code") + "_label"] = values.get(value)
    if value is not None and value not in values:
        flag(record, field + ":undocumented_code:" + str(value))


def metadata(name, manifest, source, grain, units=None, limitations=None):
    update = None
    if isinstance(source, dict) and "EssentialInfo" in source:
        update = timestamp(source["EssentialInfo"].get("UpdateTime"))
    elif isinstance(source, ET.Element):
        update = timestamp(source.findtext("{*}UpdateTime"))
    elif name == "GetSpecTimeTable":
        update = timestamp(source.get("updateTime"))
    manifest["sources"][name]["source_data_time"] = update
    return {"source": manifest["sources"][name], "source_data_time": update, "source_period": None, "timezone": "Asia/Taipei", "grain": grain, "units": units or {}, "geographic_scope": "Taipei authority feed; routes may extend beyond Taipei City", "research_scope": "Taipei City; current feeds support the recent-three-year study, but contain no three-year history", "snapshot_only": name in DYNAMIC, "specification": {"version": "6.3", "source_page": SPEC_PAGE, "raw_file": "raw_data/taipei_feeds/api_spec_v6_3.pdf"}, "missing_value_policy": "null; original missing tokens and absent-field status retained in source_missing_values; invalid values retained separately", "limitations": BASE_LIMITATIONS + (limitations or [])}


def table(dataset_id, meta, rows):
    return {"schema_version": 1, "dataset_id": dataset_id, "metadata": meta, "records": rows}


def points(dataset_id, meta, rows, id_key, lon_key="longitude", lat_key="latitude"):
    features = []
    meta["crs"] = "EPSG:4326"
    meta["coordinate_order"] = ["longitude", "latitude"]
    meta["crs_evidence"] = "EssentialInfo.CoordinateSystem = WGS84; coordinate range checked before output"
    for i, row in enumerate(rows):
        lon, lat = row.get(lon_key), row.get(lat_key)
        good = isinstance(lon, (int, float)) and isinstance(lat, (int, float)) and 119 <= lon <= 123 and 21 <= lat <= 27
        geometry = {"type": "Point", "coordinates": [lon, lat]} if good else None
        if not good:
            flag(row, "coordinates:outside_taiwan_or_missing")
        features.append({"type": "Feature", "id": row.get(id_key) or str(i), "geometry": geometry, "properties": row})
    return {"type": "FeatureCollection", "schema_version": 1, "dataset_id": dataset_id, "metadata": meta, "features": features}


def row_list(obj):
    return obj.get("records", [f["properties"] for f in obj.get("features", [])])


def profile(obj, keys=()):
    rows = row_list(obj)
    counts = collections.Counter(tuple(row.get(k) for k in keys) for row in rows) if keys else {}
    flags = collections.Counter(flag for row in rows for flag in row.get("quality_flags", []))
    fields = sorted(set().union(*(row.keys() for row in rows))) if rows else []
    return {"record_count": len(rows), "key_fields": list(keys), "duplicate_key_rows": sum(n - 1 for n in counts.values()), "null_counts": {field: sum(row.get(field) is None for row in rows) for field in fields if field not in {"quality_flags", "source_missing_values", "source_invalid_values"}}, "quality_flag_counts": dict(flags), "null_geometry_count": sum(f["geometry"] is None for f in obj.get("features", []))}


def normalize(manifest):
    feeds = {name: read_raw(name) for name in NAMES}
    outputs, keys = {}, {}
    def save(file, obj, key_fields=()):
        obj["metadata"]["quality"] = profile(obj, key_fields)
        outputs[file] = obj
        keys[file] = key_fields

    name = "GetRoute"
    renames = {"Id": "route_id", "NId": "route_nid", "providerId": "operator_id", "providerName": "operator_name", "nameZh": "route_name_zh", "nameEn": "route_name_en", "pathAttributeId": "subroute_id", "pathAttributeNId": "subroute_nid", "pathAttributeName": "subroute_name_zh", "pathAttributeEname": "subroute_name_en", "realSequence": "approved_total_trips", "distance": "round_trip_distance_km", "routeType": "route_type_code", "buildPeriod": "build_period_code", "genus": "route_category_codes"}
    rows = []
    seen_rows = {}
    timekeys = [k for k in feeds[name]["BusInfo"][0] if k.endswith("BusTime")]
    for source_index, row in enumerate(feeds[name]["BusInfo"]):
        r = convert(row, renames, numbers=("realSequence", "distance"), times=timekeys)
        r["route_category_codes"] = r["route_category_codes"].split(";") if r.get("route_category_codes") else []
        signature = json.dumps(r, sort_keys=True, ensure_ascii=False)
        if signature in seen_rows:
            seen_rows[signature]["source_row_indices"].append(source_index)
            continue
        r["source_row_indices"] = [source_index]
        seen_rows[signature] = r
        rows.append(r)
    m = metadata(name, manifest, feeds[name], "main route × subroute × operator", {"round_trip_distance_km": "km/round trip", "approved_total_trips": "approved trips; reference day not supplied"}, ["主路線 ID 會因分支與業者重複，不可把此表每列視為獨立主路線。", "尖離峰 Headway 顯示字串未解為最短/最長班距；詳用 headway_schedules 與文字註記。", "核定總班次不是實際行駛班次，沒有明示適用日，不能跨分支列加總。"])
    m["column_mapping"] = {k: renames.get(k, snake(k)) for k in feeds[name]["BusInfo"][0]}
    m["normalization_counts"] = {"source_rows": len(feeds[name]["BusInfo"]), "normalized_rows": len(rows), "exact_duplicate_rows_collapsed": len(feeds[name]["BusInfo"]) - len(rows)}
    m["deduplication_policy"] = "Only complete normalized rows with identical values are collapsed. All original zero-based source_row_indices are retained. Distinct values for the same candidate key are never collapsed."
    save("route_branches.json", table("taipei_route_branches", m, rows), ("route_id", "subroute_id", "operator_id"))
    main = []
    groups = collections.defaultdict(list)
    for r in rows:
        groups[r["route_id"]].append(r)
    for rid, group in groups.items():
        entry = {"route_id": rid, "route_names_zh": sorted({r["route_name_zh"] for r in group if r["route_name_zh"]}), "route_nids": sorted({r["route_nid"] for r in group if r["route_nid"]}), "operator_ids": sorted({r["operator_id"] for r in group if r["operator_id"]}), "subroute_ids": sorted({r["subroute_id"] for r in group if r["subroute_id"]}), "source_branch_rows": sum(len(r["source_row_indices"]) for r in group), "normalized_branch_rows": len(group)}
        main.append(entry)
    m = metadata(name, manifest, feeds[name], "one main route ID", limitations=["由 GetRoute 同一主路線 ID 彙整識別碼集合；未平均或加總各分支的里程與班次。"])
    save("routes.json", table("taipei_routes", m, main), ("route_id",))

    name = "GetStop"
    renames = {"Id": "stop_id", "routeId": "route_id", "seqNo": "stop_sequence", "pgp": "boarding_alighting_code", "goBack": "direction_code", "stopLocationId": "stop_location_id", "showLon": "display_longitude", "showLat": "display_latitude", "vector": "bearing_degrees"}
    rows = []
    for row in feeds[name]["BusInfo"]:
        r = convert(row, renames, numbers=("seqNo", "longitude", "latitude", "showLon", "showLat", "vector"))
        if r["bearing_degrees"] is not None and not 0 <= r["bearing_degrees"] < 360:
            r.setdefault("source_invalid_values", {})["bearing_degrees"] = row["vector"]
            r["bearing_degrees"] = None
            flag(r, "bearing_degrees:outside_0_359")
        code_label(r, "direction_code", DIRECTION)
        code_label(r, "boarding_alighting_code", {"-1": "alighting_only", "0": "boarding_and_alighting", "1": "boarding_only"})
        rows.append(r)
    m = metadata(name, manifest, feeds[name], "route-specific stop sign ID", {"longitude": "degrees", "latitude": "degrees", "bearing_degrees": "degrees clockwise from north"}, ["路線站牌筆數不等於實體站位數；用 stop_location_id 連結地理站位。", "來源 vector 常見 999 超出官方 0–359 定義；正規化為 null，保留來源值。"])
    m["column_mapping"] = {k: renames.get(k, snake(k)) for k in feeds[name]["BusInfo"][0]}
    save("stops.geojson", points("taipei_current_stops", m, rows, "stop_id"), ("stop_id",))
    stops = {r["stop_id"]: r for r in rows}

    name = "GetStopLocation"
    renames = {"id": "stop_location_id", "nid": "stop_location_nid", "lon": "longitude", "lat": "latitude"}
    rows = [convert(r, renames, numbers=("lon", "lat", "version"), extra_keys=("bearing",)) for r in feeds[name]["BusInfo"]]
    m = metadata(name, manifest, feeds[name], "geographic stop location ID", {"longitude": "degrees", "latitude": "degrees"}, ["對向或鄰近站位不因名稱相同而合併。"])
    save("stop_locations.geojson", points("taipei_stop_locations", m, rows, "stop_location_id"), ("stop_location_id",))
    locations = {r["stop_location_id"] for r in rows}

    name = "GetPathDetail"
    renames = {"id": "path_detail_id", "pathAttributeId": "subroute_id", "stopId": "stop_id", "sequenceNo": "stop_sequence", "type": "stop_type_code"}
    rows = []
    for row in feeds[name]["BusInfo"]:
        r = convert(row, renames, numbers=("sequenceNo",))
        stop = stops.get(r["stop_id"])
        r["route_id"] = stop["route_id"] if stop else None
        r["direction_code"] = stop["direction_code"] if stop else None
        r["stop_location_id"] = stop["stop_location_id"] if stop else None
        if stop is None:
            flag(r, "stop_id:missing_from_GetStop")
        code_label(r, "stop_type_code", {"0": "normal", "CPT": "control_point"})
        rows.append(r)
    m = metadata(name, manifest, feeds[name], "one subroute stop occurrence (PathDetail ID)", {"stop_sequence": "source sequence number; zero-based values present"}, ["分支站序用 PathAttributeId 與 SequenceNo；方向與主路線只透過 stop_id 精確連接 GetStop，不以名稱猜測。", "Stop Type 1 出現在資料但 V6.3 只定義 0/CPT；保留並標示未知。"])
    m["joined_sources"] = [manifest["sources"]["GetStop"]]
    m["derived_fields"] = {"route_id": "GetStop.routeId on exact stopId = Id", "direction_code": "GetStop.goBack on exact stopId = Id", "stop_location_id": "GetStop.stopLocationId on exact stopId = Id"}
    save("route_stop_sequences.json", table("taipei_subroute_stop_sequences", m, rows), ("path_detail_id",))

    name = "GetBusShape"
    features = []
    for i, row in enumerate(feeds[name]):
        renames = {"UniRouteId": "uni_route_id", "RouteID": "route_id", "SubRouteID": "subroute_id", "utime": "source_updated_at", "GoBack": "direction_code"}
        r = convert({k: v for k, v in row.items() if k != "wkt"}, renames, timestamps=("utime",))
        r["shape_scope"] = "main_route" if r["subroute_id"] == "-1" else "subroute"
        if r["subroute_id"] == "-1":
            r["subroute_id"] = None
            r["source_subroute_id"] = "-1"
        geometry = from_wkt(row["wkt"])
        bounds = geometry.bounds
        if geometry.geom_type not in {"LineString", "MultiLineString"} or geometry.is_empty or not geometry.is_valid or not (119 <= bounds[0] <= bounds[2] <= 123 and 21 <= bounds[1] <= bounds[3] <= 27):
            raise ValueError("Invalid bus shape geometry at source row " + str(i))
        code_label(r, "direction_code", {"0": "outbound", "1": "inbound", "2": "loop"})
        fid = ":".join([r["route_id"], r["subroute_id"] or "main", r["uni_route_id"], r["direction_code"]])
        features.append({"type": "Feature", "id": fid, "properties": r, "geometry": mapping(geometry)})
    m = metadata(name, manifest, feeds[name], "route/subroute × direction × UniRouteId geometry", {"coordinates": "decimal degrees"}, ["SubRouteID=-1 依官方規格表示主路線，轉為 subroute_id=null 與 shape_scope=main_route，保留 source_subroute_id。", "線形沒有運量；每筆 utime 為該線形更新時間，未用 HTTP Last-Modified 代替。"])
    m.update({"crs": "EPSG:4326", "coordinate_order": ["longitude", "latitude"], "crs_evidence": "Official API specification v6.3 page 13 explicitly defines WKT WGS84; all coordinate bounds checked"})
    utimes = [f["properties"]["source_updated_at"] for f in features if f["properties"]["source_updated_at"]]
    m["source_record_time_range"] = {"min": min(utimes), "max": max(utimes)}
    save("route_shapes.geojson", {"type": "FeatureCollection", "schema_version": 1, "dataset_id": "taipei_bus_shapes", "metadata": m, "features": features}, ("route_id", "subroute_id", "uni_route_id", "direction_code"))

    for name, file, headway in [("GetTimeTable", "fixed_schedules.json", False), ("GetSemiTimeTable", "headway_schedules.json", True)]:
        renames = {"Id": "schedule_id", "PathAttributeId": "subroute_id", "DateType": "date_type_code", "DateValue": "date_value", "LongHeadway": "long_headway_minutes", "LowHeadway": "low_headway_minutes"}
        rows = []
        for row in feeds[name]["BusInfo"]:
            r = convert(row, renames, numbers=("LongHeadway", "LowHeadway"), booleans=("IsLastBus",), times=("DepartureTime", "StartTime", "EndTime"))
            code_label(r, "date_type_code", {"0": "weekday", "1": "specific_date"})
            r["service_weekday_iso"] = None
            r["service_date"] = None
            if r["date_type_code"] == "0" and r["date_value"] in {str(i) for i in range(1, 8)}:
                r["service_weekday_iso"] = 7 if r["date_value"] == "1" else int(r["date_value"]) - 1
            elif r["date_type_code"] == "1":
                try:
                    r["service_date"] = datetime.strptime(r["date_value"], "%Y%m%d").date().isoformat()
                except (TypeError, ValueError):
                    flag(r, "date_value:invalid_date")
            else:
                flag(r, "date_value:unknown_calendar_encoding")
            rows.append(r)
        limitations = ["這是計畫供給，沒有個別駕駛勤務，亦非實際發車紀錄。", "TimeTable 規格定義 DateValue=1 為週日、2 為週一、7 為週六；ISO weekday 由此換算。", "缺少生效起迄日期，不得假設適用所有歷史日期。"]
        if headway:
            limitations += ["LongHeadway/LowHeadway 依原欄名保存分鐘數。文件分別標為高/低，但多數來源 LongHeadway 小於 LowHeadway；未擅自交換或定義最短/最長。", "SemiTimeTable 的 DateType/DateValue 與固定班表同一日曆代碼慣例；官方該節未重複列出星期順序，ISO weekday 採 TimeTable 定義。"]
        m = metadata(name, manifest, feeds[name], "subroute × calendar rule × departure or headway window (schedule ID)", {"departure_time": "HH:MM in Asia/Taipei service day", "start_time": "HH:MM", "end_time": "HH:MM", "long_headway_minutes": "minutes", "low_headway_minutes": "minutes"}, limitations)
        m["code_definitions"] = {"date_type_code": {"0": "weekday", "1": "specific_date"}, "date_value_weekday": {"1": "Sunday", "2": "Monday", "3": "Tuesday", "4": "Wednesday", "5": "Thursday", "6": "Friday", "7": "Saturday"}}
        if headway:
            m["long_less_than_low_rows"] = sum(r["long_headway_minutes"] is not None and r["low_headway_minutes"] is not None and r["long_headway_minutes"] < r["low_headway_minutes"] for r in rows)
        save(file, table("taipei_" + file.removesuffix(".json"), m, rows), ("schedule_id",))

    name = "GetSpecTimeTable"
    source = feeds[name]
    rows = []
    for route_index, route in enumerate(source["specificTimeTables"]["specificTimeTable"]):
        for trip_index, trip in enumerate(route["timeTables"]["timeTable"]):
            for special_day in trip["specialDays"]["specialDay"]:
                for date in special_day["dates"]["date"]:
                    for stop in trip["stopTimes"]["stopTime"]:
                        flat = {"RouteID": route["routeID"], "SubRouteID": route["subRouteID"], "OperatorID": route["operatorID"], "OperatorCode": route["operatorCode"], "DirectionCode": route.get("direction"), "RouteNameZh": route.get("routeName", {}).get("zhTw"), "RouteNameEn": route.get("routeName", {}).get("en"), "SubrouteNameZh": route.get("subRouteName", {}).get("zhTw"), "SubrouteNameEn": route.get("subRouteName", {}).get("en"), "ServiceDate": date, "ServiceStatusCode": special_day.get("serviceStatus"), "Description": special_day.get("description"), "TripID": trip.get("tripID"), "IsLowFloor": trip.get("isLowFloor"), **{k: v for k, v in stop.items() if k != "stopName"}, "StopNameZh": stop.get("stopName", {}).get("zhTw"), "StopNameEn": stop.get("stopName", {}).get("en")}
                        r = convert(flat, {"SubRouteID": "subroute_id"}, numbers=("stopSequence",), booleans=("IsLowFloor",), times=("arrivalTime", "departureTime"))
                        r["source_route_index"] = route_index
                        r["source_timetable_index"] = trip_index
                        code_label(r, "direction_code", {"0": "outbound", "1": "inbound", "2": "loop"})
                        code_label(r, "service_status_code", {"0": "suspended", "1": "normal", "2": "additional"})
                        rows.append(r)
    m = metadata(name, manifest, source, "source route entry × timetable entry × service date × listed stop", {"arrival_time": "HH:MM Asia/Taipei", "departure_time": "HH:MM Asia/Taipei", "source_route_index": "zero-based source array index", "source_timetable_index": "zero-based source array index"}, ["上游本次只有發車站等列出的停靠資料，不能假設為完整站站班表。", "direction 字串 null 正規化為真正 null，不猜測方向。", "班次沒有 TripID 時保留 null；source_timetable_index 只是原始陣列定位，不是官方班次代碼。"])
    dates = [r["service_date"] for r in rows]
    m["source_period"] = {"start": min(dates), "end": max(dates)}
    m["effective_at"] = timestamp(source.get("effectiveDate"))
    m["expire_at"] = timestamp(source.get("expireDate"))
    m["source_timetable_name"] = source.get("timeTableName")
    m["source_format"] = "JSON (the generic specification also describes XML)"
    save("special_schedules.json", table("taipei_special_schedules", m, rows), ("source_route_index", "source_timetable_index", "service_date", "stop_sequence"))

    for name, file in [("GetBusData", "vehicle_positions.geojson"), ("GetBusEvent", "stop_events.json"), ("BusSeatEvent", "occupancy_events.json")]:
        renames = {"ProviderID": "operator_id", "StationID": "depot_id", "BusID": "plate_number", "CarType": "car_type_code", "CarID": "vehicle_id", "DutyStatus": "duty_status_code", "BusStatus": "bus_status_code", "RouteID": "subroute_id", "GoBack": "direction_code", "StopID": "stop_id", "CarOnStop": "stop_event_code", "DataTime": "event_time", "Speed": "speed_kmh", "Azimuth": "azimuth_degrees", "RemainingNum": "onboard_people", "Level": "crowding_level_code"}
        rows = []
        for row in feeds[name]["BusInfo"]:
            r = convert(row, renames, numbers=("Longitude", "Latitude", "Speed", "Azimuth", "RemainingNum"), timestamps=("DataTime",), extra_keys=("RemainingNum", "Level") if name == "BusSeatEvent" else ())
            if r["subroute_id"] == "0":
                r["subroute_id"] = None
                r["source_subroute_id"] = "0"
                flag(r, "subroute_id:source_zero_unassigned")
            if name == "GetBusData" and r["azimuth_degrees"] is not None and not 0 <= r["azimuth_degrees"] < 360:
                r.setdefault("source_invalid_values", {})["azimuth_degrees"] = row["Azimuth"]
                r["azimuth_degrees"] = None
                flag(r, "azimuth_degrees:outside_0_359")
            for field, mapping_ in [("direction_code", DIRECTION), ("car_type_code", CAR_TYPE), ("duty_status_code", {"0": "normal", "1": "start", "2": "end"}), ("bus_status_code", BUS_STATUS)]:
                code_label(r, field, mapping_)
            if "CarOnStop" in row:
                code_label(r, "stop_event_code", {"0": "departure", "1": "arrival"})
            if name == "BusSeatEvent":
                code_label(r, "crowding_level_code", {"0": "comfortable", "1": "normal", "2": "crowded"})
                if r["onboard_people"] is not None and r["onboard_people"] < 0:
                    r.setdefault("source_invalid_values", {})["onboard_people"] = row["RemainingNum"]
                    r["onboard_people"] = None
                    flag(r, "onboard_people:negative")
            rows.append(r)
        limits = ["單次快照，不能用來聲稱已取得歷史 A1/A2 或全天班距。", "RouteID 在此 feed 是附屬路線 ID，已命名為 subroute_id；StationID 是車輛所屬場站，不是公車站牌/站位。", "來源 RouteID=0 不在路線主檔，正規化為未指派 null 並保留原碼；未知狀態碼保留原碼，label=null。"]
        if name == "BusSeatEvent":
            limits += ["RemainingNum 為車上人數（onboard_people），不是剩餘座位；無法直接分解上下車量。", "未提供 RemainingNum/Level 的列保留 null，不補成 0。"]
        m = metadata(name, manifest, feeds[name], "latest vehicle position/event per source snapshot", {"speed_kmh": "km/h", "azimuth_degrees": "degrees clockwise from north", "onboard_people": "persons"}, limits)
        m["source_record_time_range"] = {"min": min(r["event_time"] for r in rows if r["event_time"]), "max": max(r["event_time"] for r in rows if r["event_time"])}
        m["column_mapping"] = {k: renames.get(k, snake(k)) for k in set().union(*(r.keys() for r in feeds[name]["BusInfo"]))}
        obj = points("taipei_vehicle_positions", m, rows, "vehicle_id") if name == "GetBusData" else table("taipei_" + file.removesuffix(".json"), m, rows)
        save(file, obj, ("vehicle_id", "event_time"))

    name = "GetEstimateTime"
    rows = []
    for row in feeds[name]["BusInfo"]:
        r = convert(row, {"GoBack": "source_go_back", "EstimateTime": "source_estimate_time"}, numbers=("EstimateTime",))
        estimate = r["source_estimate_time"]
        r["estimate_seconds"] = estimate if estimate is not None and estimate >= 0 else None
        r["service_status"] = "estimated" if estimate is not None and estimate >= 0 else ETA_STATUS.get(estimate)
        r["direction_code"] = r["source_go_back"] if r["source_go_back"] in {"0", "1"} else None
        r["go_back_status"] = {"0": "outbound", "1": "inbound", "2": "not_departed", "3": "last_bus_departed"}.get(r["source_go_back"])
        if r["service_status"] is None:
            flag(r, "estimate_time:unknown_status")
        rows.append(r)
    m = metadata(name, manifest, feeds[name], "main route × stop sign × source GoBack", {"estimate_seconds": "seconds until predicted arrival"}, ["預測而非實際到站事件；負數是服務狀態，不是負等候秒數。", "GoBack=2/3 是尚未發車/末班已駛離，不能誤當地理方向；direction_code 留 null。"])
    m["code_definitions"] = {"source_estimate_time": ETA_STATUS, "source_go_back": {"0": "outbound", "1": "inbound", "2": "not_departed", "3": "last_bus_departed"}}
    save("estimated_arrivals.json", table("taipei_estimated_arrivals", m, rows), ("route_id", "stop_id", "source_go_back"))

    name = "GetBusVehicleList"
    source = feeds[name]
    raw_rows = [{e.tag.split("}")[-1]: e.text for e in v} for v in source.find("{*}Vehicles")]
    rows = []
    renames = {"PlateNumb": "plate_number", "VehicleClass": "vehicle_class_code", "VehicleType": "vehicle_type_code", "CardReaderLayout": "card_reader_layout_code"}
    for row in raw_rows:
        r = convert(row, renames, booleans=("IsElectric", "IsHybrid", "IsLowFloor", "HasLiftOrRamp", "HasWifi"))
        code_label(r, "vehicle_class_code", {"1": "large_bus", "2": "medium_bus", "3": "small_bus"})
        code_label(r, "vehicle_type_code", {"1": "regular", "2": "accessible_bus", "3": "special_bus"})
        code_label(r, "card_reader_layout_code", {"0": "none", "1": "front_door", "2": "front_and_rear_doors"})
        rows.append(r)
    m = metadata(name, manifest, source, "vehicle plate × operator", limitations=["來源無每車核定載客容量，無法由車型或低地板旗標推算容量。", "車輛種類代碼 4 未在 V6.3 定義，保留原碼並標示，未猜測車種。"])
    m["source_format"] = "XML inside gzip"
    m["update_interval_seconds"] = int(source.findtext("{*}UpdateInterval"))
    save("vehicles.json", table("taipei_bus_vehicles", m, rows), ("plate_number", "operator_id"))

    name = "GetOrgPathAttribute"
    rows = [convert(r, {"Id": "link_id", "OrganizationId": "depot_id", "PathAttributeId": "subroute_id"}) for r in feeds[name]["BusInfo"]]
    m = metadata(name, manifest, feeds[name], "subroute × operating depot link", limitations=["這只是營業站 ID 對照，無場站座標、休息設備或可用容量。", "不能直接把有停車格或公廁的位置視為業者可調度待班的場站。"])
    save("subroute_depots.json", table("taipei_subroute_depots", m, rows), ("link_id",))

    branch_rows = row_list(outputs["route_branches.json"])
    subroutes = {r["subroute_id"] for r in branch_rows}
    route_ids = {r["route_id"] for r in branch_rows}
    link_checks = []
    for file, field, domain in [("route_stop_sequences.json", "subroute_id", subroutes), ("fixed_schedules.json", "subroute_id", subroutes), ("headway_schedules.json", "subroute_id", subroutes), ("subroute_depots.json", "subroute_id", subroutes), ("stops.geojson", "route_id", route_ids), ("stops.geojson", "stop_location_id", locations), ("estimated_arrivals.json", "stop_id", set(stops)), ("estimated_arrivals.json", "route_id", route_ids), ("vehicle_positions.geojson", "subroute_id", subroutes), ("stop_events.json", "subroute_id", subroutes), ("occupancy_events.json", "subroute_id", subroutes)]:
        records = row_list(outputs[file])
        missing = [r for r in records if r.get(field) is not None and r[field] not in domain]
        link_checks.append({"output_file": "data/taipei_feeds/" + file, "field": field, "checked_rows": len(records), "null_rows": sum(r.get(field) is None for r in records), "unmatched_rows": len(missing), "unmatched_ids": sorted({r[field] for r in missing}), "policy": "preserved without guessed crosswalk; feed coverage/time may differ"})
    for file, obj in outputs.items():
        checks = [check for check in link_checks if check["output_file"].endswith("/" + file)]
        if checks:
            obj["metadata"]["referential_checks"] = checks
        if obj["metadata"]["quality"]["duplicate_key_rows"]:
            obj["metadata"]["limitations"].append("所列候選鍵存在重複，保留所有來源列；見 quality.duplicate_key_rows，匯入時不可無聲去重。")
    # Build every object before publishing. A parser failure leaves prior outputs intact.
    for file, obj in outputs.items():
        dump(OUT / file, obj)
    summary = {"schema_version": 1, "dataset_id": "taipei_bus_feed_quality", "metadata": {"generated_at": now(), "method": "gzip/JSON/XML format validation, explicit types, missing-token preservation, candidate-key profiling, exact-ID join coverage and WGS84 coordinate bounds", "independent_review_status": "pending_separate_agent"}, "records": [{"dataset_id": obj["dataset_id"], "file": str((OUT / file).relative_to(ROOT)), **obj["metadata"]["quality"]} for file, obj in outputs.items()], "referential_checks": link_checks}
    dump(OUT / "quality_summary.json", summary)
    delivery = {"schema_version": 1, "owner": "bus_feeds", "generated_at": now(), "review_status": "pending_separate_agent", "records": [{"dataset_id": obj["dataset_id"], "status": "normalized_with_documented_source_limitations", "output_files": [str((OUT / file).relative_to(ROOT))], "record_count": len(row_list(obj)), "period": obj["metadata"].get("source_period") or obj["metadata"].get("source_record_time_range") or obj["metadata"].get("source_data_time"), "limitations": obj["metadata"]["limitations"]} for file, obj in outputs.items()]}
    dump(RAW / "delivery.json", delivery)
    dump(RAW / "manifest.json", manifest)
    print(json.dumps({file: len(row_list(obj)) for file, obj in outputs.items()}, ensure_ascii=False, indent=2))
    print("Referential unmatched:", [(r["output_file"], r["field"], r["unmatched_rows"]) for r in link_checks if r["unmatched_rows"]])


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--offline", action="store_true", help="Parse saved source files and rebuild without networking")
    ap.add_argument("--download-only", action="store_true")
    args = ap.parse_args()
    RAW.mkdir(parents=True, exist_ok=True)
    OUT.mkdir(parents=True, exist_ok=True)
    manifest_path = RAW / "manifest.json"
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {"schema_version": 1, "sources": {}}
    # Older manifests may contain checksums; they are not part of this delivery.
    for entry in [*manifest["sources"].values(), manifest.get("specification", {})]:
        entry.pop("sha256", None)
    if not args.offline:
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            for name, item in pool.map(fetch, NAMES):
                manifest["sources"][name] = item
                dump(manifest_path, manifest)
                print("downloaded", name, flush=True)
        manifest["specification"] = download(SPEC_URL, RAW / "api_spec_v6_3.pdf", SPEC_PAGE)
        manifest["specification"]["source_data_time"] = "2025-09"
        dump(manifest_path, manifest)
    else:
        for name in NAMES:
            if name not in manifest["sources"]:
                raise ValueError("Missing source metadata: " + name)
            read_raw(name)  # gzip integrity and JSON/XML parsing
        if not (RAW / "api_spec_v6_3.pdf").read_bytes().startswith(b"%PDF"):
            raise ValueError("Invalid specification PDF")
    if not args.download_only:
        normalize(manifest)


if __name__ == "__main__":
    main()
