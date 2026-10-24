#!/usr/bin/env python3
"""Build the collection index from source deliveries and independent reviews."""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GROUPS = {
    "taipei_feeds": "現行公車路網、班表、車輛與快照",
    "pto_extended": "路線及公司營運、專用道與公車評鑑",
    "metro": "捷運各站分時進出量與出口公車資訊",
    "population": "臺北市人口、年齡與統計區界",
    "urban": "臺北市專用道、停車、通行限制與公廁",
    "background": "臺北市客運統計與運具使用調查",
    "tdx": "TDX 歷史與加值服務",
    "youbike": "臺北市 YouBike 2.0 站點",
}


def read(path):
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def review_index():
    result = {}
    for path in sorted((ROOT / "data/quality").glob("*_review.json")):
        report = read(path)
        for record in report.get("records", []):
            output = record.get("output_path") or record.get("file")
            if output:
                result[output] = {
                    "status": record.get("review_status", record.get("status", "pending")),
                    "report": path.relative_to(ROOT).as_posix(),
                    "record_count": record.get("record_count"),
                }
    return result


def build():
    reviews = review_index()
    records = []
    pending_groups = []
    for group, title in GROUPS.items():
        delivery_path = ROOT / f"raw_data/{group}/delivery.json"
        if not delivery_path.exists():
            pending_groups.append(group)
            continue
        delivery = read(delivery_path)
        entries = delivery.get("records", delivery.get("datasets", []))
        if isinstance(entries, dict):
            entries = [{"dataset_id": key, **entry} for key, entry in entries.items()]
        for entry in entries:
            outputs = []
            for file in entry.get("output_files", []):
                output = dict(file) if isinstance(file, dict) else {"path": file}
                path = output["path"]
                if not (ROOT / path).is_file():
                    raise ValueError(f"Delivery references a missing output: {path}")
                review = reviews.get(path)
                output["independent_review"] = review or {"status": "pending"}
                content = read(ROOT / path)
                rows = content.get("records", content.get("features"))
                output["record_count"] = len(rows) if isinstance(rows, list) else None
                if (review and review.get("record_count") is not None
                        and output["record_count"] != review["record_count"]):
                    output["independent_review"] = {**review, "status": "pending_changed_record_count"}
                outputs.append(output)
            if not outputs:
                review_status = "not_applicable_no_observations_delivered"
            elif all(o["independent_review"]["status"] == "unavailable_source_verified" for o in outputs):
                review_status = "verified_source_unavailable"
            elif all(o["independent_review"]["status"].startswith("pass") for o in outputs):
                review_status = "passed"
            elif any("fail" in o["independent_review"]["status"] for o in outputs):
                review_status = "needs_correction"
            else:
                review_status = "pending"
            record = {
                "dataset_id": entry["dataset_id"],
                "group": group,
                "group_title": title,
                "status": entry.get("status", "unknown"),
                "period": entry.get("period"),
                "outputs": outputs,
                "independent_review_status": review_status,
                "limitations": entry.get("limitations", []),
                "source_delivery": delivery_path.relative_to(ROOT).as_posix(),
            }
            references = list(dict.fromkeys(
                entry.get("reference_files", [])
                + entry.get("specification_output_files", [])
                + entry.get("availability_files", [])
            ))
            if references:
                for path in references:
                    if not (ROOT / path).is_file():
                        raise ValueError(f"Missing reference: {path}")
                record["reference_files"] = references
            for key in ("reference_url", "reference_file"):
                if entry.get(key):
                    record[key] = entry[key]
            if record.get("reference_file") and not (ROOT / record["reference_file"]).is_file():
                raise ValueError(f"Missing reference: {record['reference_file']}")
            records.append(record)
    return {
        "schema_version": 1,
        "dataset_id": "taipei_bus_analysis_collection",
        "metadata": {
            "scope": {
                "focus": "臺北市公車路網評估",
                "monthly_series": "2023-10 至 2026-09；以實際已發布期間為準",
                "metro_series": "2025-10 至 2026-09，僅各站分時進出量，非完整 OD",
                "annual_series": "2023 至 2025 完整年",
                "static_data": "採目前官方可取得版本，保留實際版本日期",
                "cross_boundary_services": "保留臺北主管來源的跨市路線與捷運旅次；未以站名猜測行政區裁切",
            },
            "record_count": len(records),
            "pending_source_groups": pending_groups,
            "preferred_current_bus_data": "data/taipei_feeds/",
            "legacy_stop_map": "data/bus_stops.geojson 是 2021 年舊圖資，現行站牌請用 data/taipei_feeds/stops.geojson",
            "missing_data": [
                "完整公車逐站分時上下車量與乘客 OD",
                "個別駕駛勤務",
                "每車核定載客容量",
                "可供公車使用的場站即時容量",
            ],
            "note": "各資料粒度不同，不可將本目錄的筆數相加當成同一統計量；限制及單位以各檔 metadata 為準。",
        },
        "records": records,
    }


if __name__ == "__main__":
    result = build()
    path = ROOT / "data/catalog.json"
    temporary = ROOT / "raw_data/collection_catalog.json.part"
    temporary.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    temporary.replace(path)
    statuses = {}
    for record in result["records"]:
        status = record["independent_review_status"]
        statuses[status] = statuses.get(status, 0) + 1
    print(json.dumps({"datasets": len(result["records"]), "reviews": statuses,
                      "pending_groups": result["metadata"]["pending_source_groups"]}, ensure_ascii=False))
