#!/usr/bin/env python3
"""Bounded Taipei TDX road snapshots, historical availability and parsed records.

The Section history endpoint contains road definitions, not traffic measurements.
Public specifications/catalogs never imply that business observations were fetched.
"""
from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta
from http.client import IncompleteRead
import json
from pathlib import Path
import re
import time
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

try:
    from scripts.update_tdx import token
    from scripts.tdx_bus_travel_time import next_request_time
except ModuleNotFoundError:
    from update_tdx import token
    from tdx_bus_travel_time import next_request_time

HOST = "https://tdx.transportdata.tw"
SPEC_IDS = {"basic": "7f07d940-91a4-495d-9465-1c9df89d709c", "historical": "4b4708cc-ab49-4037-a896-e24544d51609"}
ENDPOINTS = {
    "section": ("Section", "發布路段", "road_definition"),
    "vd": ("VD", "車輛偵測器", "detector_definition"),
    "section_live": ("Live", "發布路段即時路況", "section_speed_observation"),
    "vd_live": ("Live/VD", "車輛偵測器即時路況", "detector_flow_observation"),
}
OBSERVED_COLLECTIONS = {"section": "Sections", "vd": "VDs", "section_live": "LiveTraffics", "vd_live": "VDLives"}


def now():
    return datetime.now(ZoneInfo("Asia/Taipei")).isoformat(timespec="seconds")


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    part = path.with_suffix(path.suffix + ".part")
    part.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    part.replace(path)


def snake(value):
    value = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", value)
    value = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", value).lower()
    return {"vdid": "vd_id", "lane_id": "lane_id", "link_i_ds": "link_ids", "link_ids": "link_ids"}.get(value, value)


def normalize(value, key=""):
    if isinstance(value, dict):
        names = [snake(k) for k in value if k not in {"DataHash", "GeoHash"}]
        if len(names) != len(set(names)):
            raise ValueError("Normalized field collision")
        return {snake(k): normalize(v, k) for k, v in value.items() if k not in {"DataHash", "GeoHash"}}
    if isinstance(value, list):
        return [normalize(v, key) for v in value]
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if key.endswith(("ID", "UID", "Code")):
        return str(value)
    return value


def envelope(dataset_id, records, metadata):
    return {"schema_version": 1, "dataset_id": dataset_id, "metadata": {**metadata,
        "license": {"name": "TDX 服務條款", "url": HOST + "/term", "note": "資料取用及再利用依 TDX 服務條款及原資料提供機關授權規定。"},
        "record_count": len(records)}, "records": records}


class Store:
    def __init__(self, root):
        self.root = root
        self.raw = root / "raw_data/tdx_road_traffic"
        self.out = root / "data/tdx_road_traffic"
        self.path = self.raw / "manifest.json"
        self.records = json.loads(self.path.read_text())["records"] if self.path.exists() else []

    def save(self):
        write(self.path, envelope("tdx_road_traffic_manifest", self.records, {"grain": "one source request", "source_updated_at": None}))

    def receipt(self, key):
        return next((r for r in self.records if r["source_id"] == key), None)

    def put(self, receipt):
        old = self.receipt(receipt["source_id"])
        if old is not None and old.get("status") == "downloaded" and receipt.get("status") != "downloaded":
            old["last_failed_attempt"] = receipt
            self.save()
            return
        if old is not None:
            self.records.remove(old)
        self.records.append(receipt)
        self.save()

    def saved_path(self, receipt, expected_url):
        if receipt.get("download_url") != expected_url:
            raise ValueError("Saved source URL does not match requested endpoint")
        relative = receipt.get("raw_file")
        if not isinstance(relative, str):
            raise ValueError("Missing saved raw path")
        path = (self.root / relative).resolve()
        if not path.is_relative_to(self.raw.resolve()):
            raise ValueError("Saved raw path outside owned source directory")
        if not path.is_file() or receipt.get("bytes") != path.stat().st_size:
            raise ValueError("Saved source byte count does not match raw file")
        return path

    def public(self, key, url, filename, source_page, offline):
        path = self.raw / filename
        old = self.receipt(key)
        if not offline:
            with urlopen(Request(url, headers={"Accept-Encoding": "identity"}), timeout=60) as response:
                payload = response.read(8 * 1024**2 + 1)
                if response.status != 200 or len(payload) > 8 * 1024**2:
                    raise ValueError("Invalid or oversized public catalog")
                check_length(payload, response.headers.get("Content-Length"))
                obj = json.loads(payload)
                if key.endswith("spec") and not obj.get("paths"):
                    raise ValueError("Missing OpenAPI paths")
                if key.endswith("supply") and not isinstance(obj.get("list"), list):
                    raise ValueError("Missing official supply catalog")
                json.dumps(obj, allow_nan=False)
                save_raw(path, payload)
                self.put({"source_id": key, "source_page": source_page, "download_url": url,
                    "downloaded_at": now(), "source_updated_at": None, "raw_file": str(path.relative_to(self.root)),
                    "bytes": len(payload), "http_status": response.status,
                    "evidence_type": "official_openapi" if key.endswith("spec") else "official_supply_catalog"})
        elif old is None:
            raise ValueError(f"Missing provenance for cached public source: {key}")
        else:
            path = self.saved_path(old, url)
        return json.loads(path.read_bytes())


def path_for(key, historical=False):
    return "/v2/" + ("Historical/" if historical else "") + "Road/Traffic/" + ENDPOINTS[key][0] + "/City/Taipei"


def catalog(store, offline=False):
    specs = {}
    for category, uid in SPEC_IDS.items():
        specs[category] = store.public(category + "_spec", HOST + "/webapi/File/Swagger/V3/" + uid,
            category + "_spec.json", HOST + "/api-service/swagger/" + category + "/" + uid, offline)
    supply = store.public("historical_supply", HOST + "/webapi/service?category=historical", "historical_supply.json", HOST + "/data-service/historical", offline)
    rows, fields = [], []
    for category, spec in specs.items():
        for key, (_, _, kind) in ENDPOINTS.items():
            path = path_for(key, category == "historical").replace("Taipei", "{City}")
            operation = spec["paths"][path]["get"]
            rows.append({"service_category": category, "key": key, "data_kind": kind,
                "summary": operation["summary"], "download_url": spec["servers"][0]["url"] + path.replace("{City}", "Taipei"),
                "path_template": path, "parameters": normalize(operation["parameters"]),
                "source_id": category + "_spec", "source_updated_at": None,
                "billing_policy": "1500 calls/point and 150 MB/point" if category == "basic" else "10 calls/point and 20 MB/point"})
        # Retain only models reachable from the eight selected response definitions.
        seen = set()
        def walk(obj):
            if isinstance(obj, dict):
                ref = obj.get("$ref")
                if ref and ref.startswith("#/components/schemas/"):
                    name = ref.split("/")[-1]
                    if name not in seen:
                        seen.add(name)
                        model = spec["components"]["schemas"][name]
                        for field, definition in model.get("properties", {}).items():
                            fields.append({"service_category": category, "model": name, "source_field": field,
                                "normalized_field": snake(field), "source_type": definition.get("type"),
                                "description": definition.get("description"), "nullable": definition.get("nullable", False)})
                        walk(model)
                for value in obj.values(): walk(value)
            elif isinstance(obj, list):
                for value in obj: walk(value)
        for key in ENDPOINTS:
            walk(spec["paths"][path_for(key, category == "historical").replace("Taipei", "{City}")]["get"]["responses"])
    common = {"geographic_scope": "Taipei", "source_updated_at": None, "statistical_period": None,
        "evidence_type": "official_specification_only", "business_data_downloaded": False,
        "sources": [store.receipt(k + "_spec") for k in SPEC_IDS]}
    write(store.out / "api_catalog.json", envelope("tdx_road_traffic_api_catalog", rows, {**common, "grain": "one API operation"}))
    write(store.out / "field_dictionary.json", envelope("tdx_road_traffic_field_dictionary", fields, {**common, "grain": "one field per official response model"}))
    candidates = [r for r in supply["list"] if r.get("domainL2Name") == "縣市即時路況" and "臺北" in r["name"]]
    records = []
    today = datetime.now(ZoneInfo("Asia/Taipei")).date()
    window = today.replace(year=today.year - 3, day=28) if today.month == 2 and today.day == 29 else today.replace(year=today.year - 3)
    for key, (_, label, kind) in ENDPOINTS.items():
        # The source spells historical Section as 發佈, whereas basic uses 發布.
        matching = [r for r in candidates if r["domainL3Name"] == label]
        if len(matching) != 1:
            raise ValueError(f"Ambiguous historical catalog mapping: {key}")
        row = matching[0]
        start, end = row.get("dataStartTime"), row.get("dataEndTime")
        selected_start = max(date.fromisoformat(start[:10]), window) if start else None
        selected_end = min(date.fromisoformat(end[:10]), today - timedelta(days=1)) if end else None
        records.append({"key": key, "name": row["name"], "service_id": row["pK_ServiceDetail"], "version": row["version"],
            "data_kind": kind, "source_data_start_time": start, "source_data_end_time": end,
            "requested_three_year_window_start": window.isoformat(), "available_window_start": selected_start.isoformat() if selected_start else None,
            "available_window_end": selected_end.isoformat() if selected_end else None,
            "coverage_status": "catalog_range_only_daily_completeness_not_verified",
            "download_url": HOST + "/api/historical" + path_for(key, True),
            "month_requests": month_requests(key, selected_start, selected_end) if selected_start and selected_end else [],
            "source_updated_at": None})
    write(store.out / "historical_catalog.json", envelope("tdx_road_traffic_historical_catalog", records, {
        "grain": "one historical API service with bounded monthly retrieval references", "source_updated_at": None,
        "statistical_period": {"window_start": window.isoformat(), "window_end": (today - timedelta(days=1)).isoformat()},
        "sources": [store.receipt("historical_supply")], "time_zone": "Asia/Taipei; source catalog dates have no UTC offset",
        "limitations": ["供應目錄起訖不是逐日資料完整性證明；month_requests 每筆不超過官方七日查詢上限。", "Section 為靜態路段定義歷史版本，不是路段速率或車流觀測。"]}))
    return specs, records


def month_requests(key, start, end):
    # Keep each reference within a month and the documented maximum seven days.
    records = []
    day = start
    while day <= end:
        last = min(day + timedelta(days=6), end)
        while last.month != day.month:
            last -= timedelta(days=1)
        dates = day.isoformat() if day == last else day.isoformat() + "~" + last.isoformat()
        records.append({"start_date": day.isoformat(), "end_date": last.isoformat(),
            "request_url": HOST + "/api/historical" + path_for(key, True) + "?" + urlencode({"Dates": dates, "$format": "JSONL"}),
            "top_note": "Default top is 30; explicitly set $top to the verified Meta count before requesting full data."})
        day = last + timedelta(days=1)
    return records


def plan(day, sample_top=30):
    date.fromisoformat(day)
    if day >= datetime.now(ZoneInfo("Asia/Taipei")).date().isoformat():
        raise ValueError("Historical date must precede today")
    rows = []
    def add(key, historical, mode):
        params = {"$format": "JSONL" if historical else "JSON"}
        if historical: params["Dates"] = day
        if mode == "meta": params["Meta"] = "true"
        else: params["$top"] = sample_top if mode == "sample" else 1000000
        if not historical: params["$count"] = "true"
        label = ("historical_" + day + "_" if historical else "current_") + key + ("_" + mode if mode in {"meta", "sample"} else "")
        rows.append({"source_id": label, "key": key, "category": "historical" if historical else "basic", "mode": mode,
            "date": day if historical else None, "data_kind": "availability_statistics" if mode == "meta" else ENDPOINTS[key][2],
            "query_parameters": params, "request_url": HOST + "/api/" + ("historical" if historical else "basic") + path_for(key, historical) + "?" + urlencode(params)})
    for key in ["section", "section_live", "vd_live"]: add(key, True, "meta")
    for key in ENDPOINTS: add(key, False, "full")
    add("section", True, "full")
    for key in ["section_live", "vd_live"]: add(key, True, "sample")
    return rows


def parse_payload(payload, request):
    if request["category"] == "historical" and request["mode"] != "meta":
        rows = [json.loads(line) for line in payload.decode("utf-8-sig").splitlines() if line.strip()]
        wrapper = {}
    else:
        obj = json.loads(payload)
        if not isinstance(obj, dict) or any(k in obj for k in ["error", "Message", "message"]):
            raise ValueError("Expected successful TDX object")
        if request["mode"] == "meta":
            if not isinstance(obj.get("Count"), int) or obj["Count"] < 0 or not isinstance(obj.get("SizeBytes"), (int, float)) or obj["SizeBytes"] < 0:
                raise ValueError("Missing historical availability Count/SizeBytes")
            if obj.get("Dates") != request["date"]:
                raise ValueError("Availability statistics outside requested date")
            rows, wrapper = [obj], {}
        else:
            collection = "Items"
            # Confirmed live Taipei responses (2026-10-24); the supplied OpenAPI
            # uses Items. Retain the observed collection name and schema mismatch.
            observed = OBSERVED_COLLECTIONS[request["key"]]
            if collection not in obj and isinstance(obj.get(observed), list):
                collection = observed
            if not isinstance(obj.get(collection), list):
                raise ValueError("Missing road response Items list")
            rows, wrapper = obj[collection], {k: v for k, v in obj.items() if k != collection}
            wrapper["__collection_field"] = collection
    if not all(isinstance(row, dict) for row in rows):
        raise ValueError("Non-object road record")
    expected = "VDID" if request["key"].startswith("vd") else "SectionID"
    if request["mode"] != "meta" and any(expected not in row for row in rows):
        raise ValueError("Unexpected road record schema")
    if request["category"] == "historical" and request["mode"] != "meta":
        if any(not isinstance(row.get("InfoDate"), str) for row in rows):
            raise ValueError("Missing historical InfoDate")
        for row in rows: date.fromisoformat(row["InfoDate"])
    # Validate original numbers before ID normalization can turn NaN/Infinity
    # into apparently valid strings such as "nan" or "inf".
    try:
        json.dumps([rows, wrapper], allow_nan=False)
    except ValueError:
        raise ValueError("Non-finite original JSON number") from None
    return rows, wrapper


def prepare_output(store, receipt, payload):
    """Normalize and construct an output before publishing any new snapshot."""
    req = receipt["request"]
    expected_url = HOST + "/api/" + req["category"] + path_for(req["key"], req["category"] == "historical") + "?" + urlencode(req["query_parameters"])
    if req.get("request_url") != expected_url or receipt.get("download_url") != expected_url or req["source_id"] != receipt["source_id"]:
        raise ValueError("Saved request does not match endpoint identity")
    rows, wrapper = parse_payload(payload, req)
    collection = wrapper.pop("__collection_field", None)
    records = [normalize(row) for row in rows]
    for row in records:
        if req["data_kind"] == "section_speed_observation":
            for field in ["travel_speed", "travel_time", "data_collect_time"]: row.setdefault(field, None)
        elif req["data_kind"] == "detector_flow_observation":
            for link in row.get("link_flows", []) or []:
                for lane in link.get("lanes", []) or []:
                    for field in ["speed", "occupancy"]: lane.setdefault(field, None)
                    for vehicle in lane.get("vehicles", []) or []:
                        for field in ["volume", "speed"]: vehicle.setdefault(field, None)
    source_dates = sorted({r.get("InfoDate") for r in rows if r.get("InfoDate")})
    collect = sorted({r.get("DataCollectTime") for r in rows if r.get("DataCollectTime")})
    collect_days, collect_mismatch_ids, invalid_collect_times = [], set(), 0
    source_id_field = "VDID" if req["key"].startswith("vd") else "SectionID"
    for row in rows:
        stamp = row.get("DataCollectTime")
        if not stamp: continue
        try:
            value = datetime.fromisoformat(stamp)
            if value.tzinfo is None: raise ValueError("Missing source time offset")
            local_day = value.astimezone(ZoneInfo("Asia/Taipei")).date().isoformat()
            collect_days.append(local_day)
            if req["date"] and local_day != req["date"] and row.get(source_id_field) is not None:
                collect_mismatch_ids.add(str(row[source_id_field]))
        except ValueError:
            invalid_collect_times += 1
    info_mismatch = sum(r.get("InfoDate") is not None and r["InfoDate"] != req["date"] for r in rows) if req["date"] else None
    collect_mismatch = sum(day != req["date"] for day in collect_days) if req["date"] else None
    period_status = "contains_observations_outside_requested_taipei_date" if collect_mismatch else (
        "source_info_date_differs_from_requested_date" if info_mismatch else "no_observed_date_mismatch")
    info_mismatch_ids = sorted({str(row[source_id_field]) for row in rows if row.get("InfoDate") and req["date"] and row["InfoDate"] != req["date"] and row.get(source_id_field) is not None})
    updates = sorted({r.get("UpdateTime") for r in rows if r.get("UpdateTime")})
    status = "sample_only" if req["mode"] == "sample" else "requested_scope_completeness_unverified"
    if req["mode"] == "meta": status = "availability_statistics_only"
    elif wrapper.get("Count") == len(rows): status = "complete_requested_endpoint_by_response_count"
    elif req["category"] == "historical" and req["mode"] == "full":
        meta = store.receipt("historical_" + req["date"] + "_" + req["key"] + "_meta")
        if meta and meta.get("status") == "downloaded":
            meta_obj = json.loads(store.saved_path(meta, meta["request"]["request_url"]).read_bytes())
            counts = meta_counts(meta_obj)
            if counts == len(rows): status = "complete_requested_date_by_meta_count"
    semantics = {"road_definition": "one static road definition in source snapshot/date", "detector_definition": "one detector definition",
        "section_speed_observation": "one source section and DataCollectTime", "detector_flow_observation": "one detector and DataCollectTime; nested link/lane/vehicle type values",
        "availability_statistics": "one requested service/date statistics response"}
    meta = {"grain": semantics[req["data_kind"]], "data_kind": req["data_kind"], "scope_status": status,
        "source_updated_at": wrapper.get("UpdateTime") or (updates[-1] if updates else None),
        "source_record_update_time_range": [updates[0], updates[-1]] if updates else None,
        "statistical_period": {"requested_date": req["date"], "source_info_dates": source_dates,
            "data_collect_time_start": collect[0] if collect else None, "data_collect_time_end": collect[-1] if collect else None,
            "data_collect_taipei_dates": sorted(set(collect_days)), "period_validation_status": period_status,
            "source_info_date_mismatch_count": info_mismatch, "data_collect_requested_taipei_day_mismatch_count": collect_mismatch,
            "data_collect_out_of_requested_date_ids": sorted(collect_mismatch_ids),
            "invalid_or_offset_missing_collect_time_count": invalid_collect_times},
        "time_zone": "Asia/Taipei; original source timestamp offsets retained", "response_metadata": normalize(wrapper),
        "response_collection_field": collection,
        "response_schema_deviation": "Observed named collection instead of OpenAPI Items; exact collection and records verified." if collection and collection != "Items" else None,
        "requires_source_review": bool(info_mismatch or collect_mismatch or invalid_collect_times),
        "out_of_requested_date_count": info_mismatch,
        "out_of_requested_date_ids": info_mismatch_ids,
        "out_of_requested_date_basis": "Literal source InfoDate; DataCollectTime is separately compared after converting its original offset to Asia/Taipei.",
        "sources": [{k: v for k, v in receipt.items() if k != "request"}], "request": req,
        "units": {"travel_speed": "km/h", "travel_time": "seconds", "speed": None, "volume": None, "occupancy": None},
        "unit_notes": {"speed": "VD average speed: official supplied OpenAPI omits its unit; not converted.",
            "volume": "VD flow detection value: official supplied OpenAPI omits counting interval/unit; not interpreted as hourly volume.",
            "occupancy": "Official supplied OpenAPI omits its scale/unit; not converted."},
        "field_dictionary": "data/tdx_road_traffic/field_dictionary.json",
        "limitations": ["Section 為路段定義；不得當作歷史速率或流量。", "-99 等異常偵測值與設備狀態原值保留；缺值不補零。", "VD 流量維持 link／lane／vehicle type 粒度，未跨車道、車種或時間加總。", "外部來源 ID 不直接關聯；同一 TDX 服務也須按 AuthorityCode 及版本／日期核對。", "歷史樣本只是來源前 N 筆，不代表整日或三年的分布。", "歷史版與基礎版 OpenAPI 對 SrcUpdateTime/UpdateTime 描述相反，保留原欄位；DataCollectTime 才是動態觀測時間。"]}
    if collect_mismatch or info_mismatch:
        meta["limitations"].append("來源回應含不同於請求日期的資料代表日期／觀測日；保留原時間，須檢視實際統計期間，不可全數視為所請求臺北民用日內觀測。")
    if req["key"] in {"section", "vd"}:
        meta["coordinate_reference_system"] = "Source coordinate fields retained in JSON; no GeoJSON emitted and no CRS transformation inferred."
    target = store.out / ("availability" if req["mode"] == "meta" else "observations") / (req["source_id"] + ".json")
    document = envelope("tdx_road_traffic_" + req["source_id"], records, meta)
    result = {"source_id": req["source_id"], "output_file": str(target.relative_to(store.root)), "record_count": len(records),
        "scope_status": status, "data_kind": req["data_kind"], "statistical_period": meta["statistical_period"]}
    return target, document, result


def parse_saved(store, receipt):
    path = store.saved_path(receipt, receipt["request"]["request_url"])
    target, document, result = prepare_output(store, receipt, path.read_bytes())
    write(target, document)
    return result


def meta_counts(obj):
    if isinstance(obj, dict):
        if isinstance(obj.get("Count"), int): return obj["Count"]
        counts = [meta_counts(v) for v in obj.values() if isinstance(v, (dict, list))]
        return sum(v for v in counts if v is not None) if any(v is not None for v in counts) else None
    if isinstance(obj, list):
        counts = [meta_counts(v) for v in obj]
        return sum(v for v in counts if v is not None) if any(v is not None for v in counts) else None
    return None


def check_length(payload, declared):
    if declared is not None and len(payload) != int(declared):
        raise ValueError("Response Content-Length does not match bytes received")


def save_raw(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    part = path.with_suffix(path.suffix + ".part")
    try:
        part.write_bytes(payload)
        part.replace(path)
    finally:
        part.unlink(missing_ok=True)


def usage_headers(headers):
    return {k.lower(): v for k, v in headers.items() if k.lower() == "retry-after" or
        k.lower().startswith(("x-ratelimit", "x-rate-limit", "x-quota", "x-point"))}


def reference_diagnostics(store):
    checks = []
    for observation, definition, id_key in [("section_live", "section", "section_id"), ("vd_live", "vd", "vd_id")]:
        paths = [store.out / "observations" / ("current_" + key + ".json") for key in [observation, definition]]
        if not all(path.exists() for path in paths): continue
        measured, static = [json.loads(path.read_text()) for path in paths]
        observed_ids = {r[id_key] for r in measured["records"] if r.get(id_key) is not None}
        static_ids = {r[id_key] for r in static["records"] if r.get(id_key) is not None}
        authorities = [obj["metadata"]["response_metadata"].get("authority_code") for obj in [measured, static]]
        checks.append({"observation_file": str(paths[0].relative_to(store.root)), "definition_file": str(paths[1].relative_to(store.root)),
            "id_field": id_key, "observation_authority_code": authorities[0], "definition_authority_code": authorities[1],
            "authority_matches": authorities[0] is not None and authorities[0] == authorities[1],
            "observation_unique_id_count": len(observed_ids), "definition_unique_id_count": len(static_ids),
            "matched_exact_id_count": len(observed_ids & static_ids),
            "observation_only_ids": sorted(observed_ids - static_ids), "definition_only_ids": sorted(static_ids - observed_ids),
            "join_status": "coverage_diagnostic_only_not_a_versioned_join", "version_equivalence_verified": False})
    write(store.out / "reference_diagnostics.json", envelope("tdx_road_traffic_reference_diagnostics", checks, {
        "grain": "one same-provider current snapshot ID coverage comparison", "source_updated_at": None,
        "statistical_period": None, "limitations": ["只比對明確 ID 及機關，不以名稱或前綴推定關聯。", "同日不同時間快照的版本一致性未驗證；未產生跨資料表join，亦未用現況版本關聯歷史資料。"]}))


def fetch(store, requests, args):
    auth = token()
    total = used = 0
    response_bytes_unknown = False
    stop_reason = None
    attempts = store.records + [r["last_failed_attempt"] for r in store.records if r.get("last_failed_attempt")]
    allowed_at = time.time()
    for attempt in attempts:
        stamp = attempt.get("checked_at") or attempt.get("downloaded_at")
        if attempt.get("request") and stamp:
            allowed_at = max(allowed_at, next_request_time(attempt.get("usage_headers"),
                datetime.fromisoformat(stamp).timestamp(), args.min_request_interval))
    for index, request in enumerate(requests):
        if used >= args.max_requests:
            stop_reason = "request_budget_reached"
            break
        available = int(args.max_total_mb * 1024**2) - total
        response_limit = int(args.max_response_mb * 1024**2)
        # A local batch limit leaves the next endpoint untouched for resuming.
        if available <= 0 or (used and available < response_limit):
            stop_reason = "total_response_budget_reached"
            break
        remaining = min(response_limit, available)
        delay = max(0, allowed_at - time.time())
        while delay > 0:
            step = min(delay, 60)
            time.sleep(step)
            delay -= step
        suffix = ".jsonl" if request["category"] == "historical" and request["mode"] != "meta" else ".json"
        path = store.raw / "responses" / (request["source_id"] + suffix)
        receipt = {"source_id": request["source_id"], "source_page": HOST + "/api-service/swagger/" + request["category"] + "/" + SPEC_IDS[request["category"]],
            "download_url": request["request_url"], "downloaded_at": now(), "source_updated_at": None, "request": request,
            "evidence_type": "api_availability_statistics" if request["mode"] == "meta" else "business_api_response"}
        payload = None
        failure_stage = "transport"
        used += 1
        try:
            req = Request(request["request_url"], headers={"Authorization": "Bearer " + auth, "Accept-Encoding": "identity"})
            with urlopen(req, timeout=180) as response:
                receipt["http_status"] = response.status
                receipt["content_type"] = response.headers.get("Content-Type")
                receipt["usage_headers"] = usage_headers(response.headers)
                allowed_at = next_request_time(receipt["usage_headers"], time.time(), args.min_request_interval)
                if response.status != 200 and not (response.status == 249 and request["mode"] == "meta"):
                    raise ValueError("Unexpected HTTP status")
                if int(response.headers.get("Content-Length", 0)) > remaining: raise ValueError("Response exceeds byte budget")
                payload = response.read(remaining + 1)
                total += len(payload)
                if len(payload) > remaining: raise ValueError("Response exceeds byte budget")
                check_length(payload, response.headers.get("Content-Length"))
            receipt.update({"raw_file": str(path.relative_to(store.root)), "bytes": len(payload), "status": "downloaded", "downloaded_at": now()})
            failure_stage = "normalization"
            target, document, result = prepare_output(store, receipt, payload)
            # json.loads accepts NaN/Infinity; enforce the actual normalized
            # serialization contract before replacing successful raw/receipt.
            failure_stage = "serialization"
            json.dumps(document, ensure_ascii=False, allow_nan=False).encode("utf-8")
            failure_stage = "snapshot_persistence"
            save_raw(path, payload)
            store.put(receipt)
            write(target, document)
            print(request["source_id"] + ": " + str(result["record_count"]) + " records", flush=True)
        except HTTPError as exc:
            receipt.update({"http_status": exc.code, "status": "provider_http_error", "raw_file": None, "bytes": None,
                "checked_at": now(), "failure_stage": "transport", "rejection_reason": "provider_http_error",
                "content_type": exc.headers.get("Content-Type") if exc.headers else None,
                "usage_headers": usage_headers(exc.headers) if exc.headers else {}})
            try:
                payload = exc.read(remaining + 1)
            except IncompleteRead as partial:
                payload = partial.partial
                receipt["error_body_transport_complete"] = False
            except OSError as body_error:
                payload = None
                response_bytes_unknown = True
                receipt.update({"error_body_transport_complete": False, "error_type": type(body_error).__name__,
                    "rejection_reason": "http_error_body_read_failed"})
            if payload is not None:
                total += len(payload)
            receipt["response_bytes_received"] = len(payload) if payload is not None else None
            if payload is not None and len(payload) <= remaining:
                failed = store.raw / "failures" / (request["source_id"] + ".http_error")
                save_raw(failed, payload)
                receipt.update({"raw_file": str(failed.relative_to(store.root)), "bytes": len(payload)})
            store.put(receipt)
            print(request["source_id"] + ": HTTP " + str(exc.code), flush=True)
            if response_bytes_unknown:
                # The failed read may already have consumed unknown bytes.
                # Do not admit another response against an unknowable balance.
                stop_reason = "http_error_body_read_failed"
                break
            allowed_at = next_request_time(receipt["usage_headers"], time.time(), args.min_request_interval)
            if exc.code in {401, 403, 429}:
                stop_reason = "provider_http_" + str(exc.code)
                break
        except (ValueError, UnicodeError, OSError, IncompleteRead) as exc:
            if isinstance(exc, IncompleteRead):
                payload = exc.partial
                total += len(payload)
            reasons = {"Response Content-Length does not match bytes received": "response_length_mismatch",
                "Response exceeds byte budget": "response_exceeds_local_budget",
                "Unexpected HTTP status": "unexpected_http_status", "Normalized field collision": "normalized_field_collision",
                "Non-finite original JSON number": "non_finite_source_number"}
            reason = reasons.get(str(exc), "incomplete_http_body" if isinstance(exc, IncompleteRead)
                else "strict_json_serialization_failed" if failure_stage == "serialization"
                else "response_contract_mismatch" if failure_stage == "normalization"
                else "snapshot_persistence_failed" if failure_stage == "snapshot_persistence"
                else "invalid_transport_response")
            if isinstance(exc, OSError) and failure_stage == "transport" and payload is None and receipt.get("http_status") is not None:
                response_bytes_unknown = True
                reason = "response_body_read_failed"
                receipt["response_bytes_received"] = None
            receipt.update({"status": "response_exceeds_local_budget" if "byte budget" in str(exc) else "invalid_provider_response",
                "raw_file": None, "bytes": None, "error_type": type(exc).__name__, "checked_at": now(),
                "failure_stage": failure_stage, "rejection_reason": reason})
            if payload is not None and len(payload) <= remaining:
                failed = store.raw / "failures" / (request["source_id"] + ".invalid_response")
                save_raw(failed, payload)
                receipt.update({"raw_file": str(failed.relative_to(store.root)), "bytes": len(payload)})
            store.put(receipt)
            print(request["source_id"] + ": " + receipt["status"], flush=True)
            if response_bytes_unknown:
                stop_reason = "response_body_read_failed"
                break
    return {"requests_used": used, "response_bytes": None if response_bytes_unknown else total,
            "known_response_bytes": total, "stop_reason": stop_reason,
            "not_requested_source_ids": [r["source_id"] for r in requests[used:]]}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--plan", action="store_true", help="Build explicit request plan without authenticated calls")
    mode.add_argument("--fetch", action="store_true")
    mode.add_argument("--offline", action="store_true", help="Rebuild from own cached catalog and manifest only")
    parser.add_argument("--use-cached-catalog", action="store_true")
    parser.add_argument("--date", help="Historical civil date; defaults to the newest shared source catalog end")
    parser.add_argument("--request-id", action="append", help="Run only this source ID from the explicit plan; repeatable")
    parser.add_argument("--sample-top", type=int, default=30)
    parser.add_argument("--max-requests", type=int, default=10)
    parser.add_argument("--max-response-mb", type=float, default=8)
    parser.add_argument("--max-total-mb", type=float, default=32)
    parser.add_argument("--min-request-interval", type=float, default=26)
    args = parser.parse_args(argv)
    if any(x <= 0 for x in [args.sample_top, args.max_requests, args.max_response_mb, args.max_total_mb]) or args.min_request_interval < 12.1:
        parser.error("Budgets must be positive and request interval at least 12.1 seconds")
    store = Store(args.root.resolve())
    _, historical = catalog(store, args.offline or args.use_cached_catalog)
    day = args.date or min(r["available_window_end"] for r in historical)
    requests = plan(day, args.sample_top)
    if args.request_id:
        selected = set(args.request_id)
        if not selected.issubset({r["source_id"] for r in requests}): parser.error("Unknown --request-id")
        requests = [r for r in requests if r["source_id"] in selected]
    write(store.out / "request_plan.json", envelope("tdx_road_traffic_request_plan", requests, {
        "grain": "one explicitly bounded API request", "source_updated_at": None, "statistical_period": {"historical_date": day},
        "max_requests": args.max_requests, "max_response_bytes": int(args.max_response_mb * 1024**2),
        "max_total_bytes": int(args.max_total_mb * 1024**2), "min_request_interval_seconds": args.min_request_interval,
        "account_balance_verified": False, "purchases_performed": False}))
    acquisition = fetch(store, requests, args) if args.fetch else None
    results = []
    for receipt in list(store.records):
        if not receipt.get("request"): continue
        if receipt.get("status") == "downloaded":
            results.append(parse_saved(store, receipt))
        # Rejected bodies remain failure evidence. Being parseable offline
        # cannot establish that the original HTTP transport was complete.
    attempts = [r for r in store.records if r.get("request") and r.get("status") != "downloaded"]
    attempts += [r["last_failed_attempt"] for r in store.records if r.get("last_failed_attempt")]
    failures = [{"source_id": r["source_id"], "status": r["status"], "http_status": r.get("http_status"), "download_url": r["download_url"], "raw_file": r.get("raw_file")}
        for r in attempts]
    reference_diagnostics(store)
    write(store.out / "delivery.json", envelope("tdx_road_traffic_delivery", results, {
        "grain": "one parsed local output", "source_updated_at": None, "statistical_period": {"planned_historical_date": day},
        "acquisition_stop_reason": acquisition["stop_reason"] if acquisition else None,
        "new_business_request_count": acquisition["requests_used"] if acquisition else 0,
        "response_bytes_received": acquisition["response_bytes"] if acquisition else 0,
        "known_response_bytes_received": acquisition["known_response_bytes"] if acquisition else 0,
        "not_requested_source_ids": acquisition["not_requested_source_ids"] if acquisition else [],
        "failures": failures, "business_data_downloaded": bool(results), "independent_review_status": "pending",
        "limitations": ["現況只是下載時的單一快照，並非整日序列。", "全量歷史動態不落地；保留近三年可取得目錄及小量真實樣本。", "流量積分期間與 VD 速率單位尚未由所附官方規格定義。"]}))
    store.save()
    print("road traffic: " + str(len(results)) + " parsed outputs; " + str(len(failures)) + " source failures", flush=True)


if __name__ == "__main__":
    main()
