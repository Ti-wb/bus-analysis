"""Official NTPC PathDetail relations; explicit IDs, never name-based joins."""
from __future__ import annotations

import argparse
import gzip
import json
import re
from pathlib import Path

from .common import ROOT, fetch, write_json
from .ntpc import urls

PAGE = "https://data.taipei/dataset/detail?id=2a8f5f42-942c-4974-8366-b44e90ad9701"
SPEC_PAGE = "https://pto.gov.taipei/News_Content.aspx?n=A1DF07A86105B6BB&s=C668AE404DCD2EFE"
URL = "https://tcgbusfs.blob.core.windows.net/ntpcbus/GetPathDetail.gz"
HOSTS = {"tcgbusfs.blob.core.windows.net"}
MAX_DECODED_BYTES = 32 * 1024 * 1024
MAX_ROWS = 100_000
FIELDS = {"id", "pathAttributeId", "stopId", "sequenceNo", "type", "typeAttribute"}


def identifier(value):
    if isinstance(value, bool) or not isinstance(value, (str, int)) or not re.fullmatch(r"[0-9]+", str(value)):
        raise ValueError("Expected an explicit non-negative numeric source ID")
    return str(value)


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON field: {key}")
        result[key] = value
    return result


def reject_constant(value):
    raise ValueError(f"Non-finite JSON number: {value}")


def read_payload(path):
    try:
        with gzip.open(path, "rb") as stream:
            data = stream.read(MAX_DECODED_BYTES + 1)
    except EOFError as exc:
        raise ValueError("Truncated PathDetail gzip") from exc
    if len(data) > MAX_DECODED_BYTES:
        raise ValueError("Decompressed PathDetail exceeds byte limit")
    return json.loads(data.decode("utf-8-sig"), object_pairs_hook=unique_object, parse_constant=reject_constant)


def references(obj, kind):
    page, url = urls(kind)
    if not isinstance(obj, dict) or not isinstance(obj.get("metadata"), dict):
        raise ValueError("Invalid reference container")
    source = obj["metadata"].get("source", {})
    if not isinstance(source, dict):
        raise ValueError("Invalid reference provenance")
    if (obj.get("schema_version") != 1 or obj.get("dataset_id") != f"ntpc_{kind}"
            or source.get("requested_url") != url or source.get("page_url") != page
            or source.get("http_status") != 200 or not source.get("downloaded_at")):
        raise ValueError(f"Expected an official NTPC {kind} snapshot with provenance")
    records = obj.get("records")
    if not isinstance(records, list) or not records:
        raise ValueError(f"Empty NTPC {kind} reference")
    index = {}
    key = "subroute_id" if kind == "routes" else "stop_id"
    for row in records:
        if not isinstance(row, dict) or row.get("source_namespace") != "ntpc_open_data":
            raise ValueError("Reference namespace must remain NTPC")
        item, parent = identifier(row[key]), identifier(row["route_id"])
        if item in index and index[item] != parent:
            raise ValueError(f"Conflicting parent route for {key} {item}")
        index[item] = parent
    return index


def normalize(path, source, routes, stops):
    parents, stop_routes = references(routes, "routes"), references(stops, "stops")
    payload = read_payload(path)
    if not isinstance(payload, dict) or not isinstance(payload.get("EssentialInfo"), dict):
        raise ValueError("Missing EssentialInfo object")
    rows = payload.get("BusInfo")
    if not isinstance(rows, list) or not 0 < len(rows) <= MAX_ROWS:
        raise ValueError("Empty or oversized BusInfo array")
    records, seen, seen_cells, used_stops, used_paths = [], set(), set(), set(), set()
    for number, row in enumerate(rows, 1):
        if not isinstance(row, dict) or not FIELDS <= row.keys():
            raise ValueError(f"PathDetail schema drift at record {number}")
        item, subroute, stop = (identifier(row[k]) for k in ["id", "pathAttributeId", "stopId"])
        seq = int(identifier(row["sequenceNo"]))
        if any(not isinstance(row[k], str) for k in ["type", "typeAttribute"]):
            raise ValueError("PathDetail raw type fields must be strings")
        if subroute not in parents or stop not in stop_routes:
            raise ValueError(f"Unknown reference ID at record {number}; refresh/review snapshots")
        if parents[subroute] != stop_routes[stop]:
            raise ValueError(f"Conflicting parent route at record {number}")
        cell = (subroute, row["type"], seq)
        if item in seen or cell in seen_cells:
            raise ValueError(f"Duplicate PathDetail ID or sequence cell at record {number}")
        seen.add(item)
        seen_cells.add(cell)
        used_paths.add(subroute)
        used_stops.add(stop)
        records.append({"path_detail_id": item, "subroute_id": subroute, "stop_id": stop,
                        "route_id": parents[subroute], "sequence_no": seq,
                        "type_raw": row["type"], "type_attribute_raw": row["typeAttribute"],
                        "source_namespace": "ntpcbus", "reference_namespace": "ntpc_open_data",
                        "source_record_number": number,
                        "source_extra_fields": {k: v for k, v in row.items() if k not in FIELDS}})
    essential = payload["EssentialInfo"]
    return {"schema_version": 1, "dataset_id": "ntpc_pathdetail", "metadata": {
        "provider": "臺北市公共運輸處（官方文件列出的新北公車資料端點）",
        "source": source, "source_data_time_raw": essential.get("UpdateTime"),
        "source_essential_info": essential, "specification_page": SPEC_PAGE,
        "license": {"name": "政府資料開放授權條款-第1版", "url": "https://data.taipei/rule",
                    "evidence": "官方資料集標示公開；網站授權規範連結，非沿用另一個新北資料集授權"},
        "reference_sources": {"routes": routes["metadata"]["source"], "stops": stops["metadata"]["source"]},
        "grain": "官方附線×站牌×原始序號", "independent_review_status": "pending",
        "limitations": ["保留 EssentialInfo 原文；已觀測新北端點 Location 寫台北市，不據此改成臺北來源。",
                        "只以官方明示 ID 核對兩份 NTPC snapshot；不以名稱或數字相同推論其他來源 ID。",
                        "未出現在 PathDetail 的站牌保持未連結，不補造附線或序號。",
                        "type/typeAttribute 保留原碼，不套其他資料集方向或跳蛙分類。",
                        "是下載時點的靜態關聯，不代表班表、即時營運、全北北基覆蓋或可行步行轉乘。",
                        "三份 snapshot 時點不同；缺碼或主線矛盾即停止，不自動跨版本補碼。"]},
        "records": records, "quality": {"source_row_count": len(rows), "record_count": len(records),
            "reference_stop_count": len(stop_routes), "linked_stop_count": len(used_stops),
            "reference_subroute_count": len(parents), "linked_subroute_count": len(used_paths),
            "unlinked_stop_ids": sorted(set(stop_routes) - used_stops),
            "unlinked_subroute_ids": sorted(set(parents) - used_paths)}}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--fetch", action="store_true")
    mode.add_argument("--offline", action="store_true")
    args = parser.parse_args(argv)
    if not (args.fetch or args.offline):
        print(json.dumps({"page_url": PAGE, "download_url": URL, "status": "plan_only_no_download"}))
        return
    directory = args.root / "data/official_cross_city"
    # Require validated local NTPC snapshots before making any network request.
    routes = json.loads((directory / "ntpc_routes.json").read_text(encoding="utf-8"))
    stops = json.loads((directory / "ntpc_stops.json").read_text(encoding="utf-8"))
    references(routes, "routes")
    references(stops, "stops")
    path, source = fetch(args.root, "ntpc_pathdetail.gz", URL, PAGE, HOSTS, offline=args.offline, max_mb=8)
    result = normalize(path, source, routes, stops)
    output = directory / "ntpc_pathdetail.json"
    write_json(output, result)
    print(json.dumps({"output": str(output), **result["quality"]}))
