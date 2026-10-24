"""Rebuild additional official walking context from retained HTML/PDF only.

Run with --offline in the project environment (BeautifulSoup and pdfplumber).
This never requests data or changes any existing transfer acceptance result.
"""
from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path
import re
import unicodedata
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[2]
RAW = Path("raw_data/walking_additional")
OUT = Path("map-data/walking/additional_evidence.json")


def date_roc(value):
    year, month, day = map(int, value.split("."))
    return f"{year + 1911:04d}-{month:02d}-{day:02d}"


def require_address_in_source(text, address):
    def normalize(value):
        return re.sub(r"\s+", "", unicodedata.normalize("NFKC", value))
    if normalize(address) not in normalize(text):
        raise ValueError("Destination address is no longer explicit in official HTML: " + address)


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2) + "\n"
    temp = path.with_name(path.name + ".part")
    temp.write_text(body, encoding="utf-8")
    temp.replace(path)


def build(root=ROOT):
    from bs4 import BeautifulSoup
    import pdfplumber

    generated = datetime.now(ZoneInfo("Asia/Taipei")).isoformat(timespec="seconds")
    sources = {}
    for path in sorted((root / RAW).glob("*.source.json")):
        receipt = json.loads(path.read_text())
        raw_file = receipt["raw_file"]
        if receipt["http_status"] != 200 or (root / raw_file).stat().st_size != receipt["bytes"]:
            raise ValueError("Official raw file/receipt differs: " + raw_file)
        source_id = Path(raw_file).name
        sources[source_id] = {"source_id": source_id, "source_page": receipt["source_url"],
            "download_url": receipt["effective_url"], "source_publication_date": receipt.get("source_publication_date"),
            "source_updated_at": receipt.get("source_updated_at"), "source_period": receipt.get("source_period"),
            "downloaded_at": receipt["downloaded_at"], "raw_file": raw_file, "receipt_file": str(path.relative_to(root)),
            "http_status": receipt["http_status"], "bytes": receipt["bytes"],
            "reuse_terms": "Official public source; source-specific open-data reuse permission not inferred."}

    def html(name):
        soup = BeautifulSoup((root / sources[name]["raw_file"]).read_bytes(), "html.parser")
        return soup, soup.get_text(" ", strip=True)

    def context(case_ids, kind):
        return [{"case_id": case, "relation": kind, "verified_stop_id_identity": False,
                 "verified_exit_to_stop_connector": False} for case in case_ids]

    land_cases = [f"xinzhuang:exit:{exit_no}:stop_location:1000605" for exit_no in (1, 2)]
    post_cases = [f"xinzhuang:exit:{exit_no}:stop_location:1165604194" for exit_no in (1, 2)]
    project_name = "新北市新莊區中華路一段(新莊捷運站至運動場段)人行道改善工程"
    table, table_text = html("ntpc_2025_sidewalk_results.html")
    rows = [[c.get_text(" ", strip=True) for c in tr.find_all(["th", "td"], recursive=False)] for tr in table.find_all("tr")]
    expected_header = ["項次", "轄區", "案件名稱", "人行道改善長度(m)", "人行道改善面積(平方公尺)", "路口改善數量(個)", "總經費"]
    if expected_header not in rows:
        raise ValueError("Official project table column semantics changed")
    matches = [r for r in rows if len(r) == 7 and r[2] == project_name]
    if len(matches) != 1 or "114年1~12月人行道改善" not in table_text:
        raise ValueError("Project row/annual period is ambiguous")
    r = matches[0]
    records = [{"evidence_id": "xinzhuang_zhonghua_sidewalk_project_2025", "evidence_type": "official_project_results",
        "project_name": r[2], "district_name": r[1], "source_record_number": int(r[0]),
        "statistical_period": "2025-01/2025-12", "improved_sidewalk_length_m": int(r[3].replace(",", "")),
        "improved_sidewalk_area_m2": int(r[4].replace(",", "")), "improved_intersection_count": int(r[5]),
        "case_links": context(land_cases, "same_road_segment_context_only"),
        "sources": [sources["ntpc_2025_sidewalk_results.html"]],
        "limitations": ["工程總量未逐一定位到出口、乘車側站牌或穿越位置；不作為當期開放與完整接線證明。"]}]

    _, award_text = html("xinzhuang_award_20260902.html")
    if project_name not in award_text or "2026馬路好行評選工程案件-佳作" not in award_text:
        raise ValueError("Official award identity changed")
    publication = re.search(r"發佈日期\s*(\d{4}-\d{2}-\d{2})", award_text)
    updated = re.search(r"更新日期[：:]?\s*(\d{4}-\d{2}-\d{2})", award_text)
    event = re.search(r"於(\d{3})年(\d{1,2})月(\d{1,2})日", award_text)
    if not publication or not updated or not event:
        raise ValueError("Award publication/update/event dates not explicit")
    event_date = date_roc(".".join(event.groups()))
    records.append({"evidence_id": "xinzhuang_zhonghua_sidewalk_award_2026", "evidence_type": "official_project_award",
        "project_name": project_name, "award_name": "2026馬路好行評選工程案件", "award_result": "佳作",
        "awarding_date": event_date, "publication_date": publication[1], "page_updated_at": updated[1],
        "case_links": context(land_cases, "same_road_segment_context_only"),
        "sources": [sources[name] for name in ("xinzhuang_award_20260902.html", "ntpc_award_20260805.html",
                                               "xinzhuang_award_image1.jpg", "xinzhuang_award_image2.jpg")],
        "limitations": ["獲獎不證明每個位置當期可通行；附圖為頒獎/獎狀，沒有站牌ID或可地理配準的接線。"]})

    pdf_name = "ntpc_arcade_motorcycle_exclusion_20261001.pdf"
    with pdfplumber.open(root / sources[pdf_name]["raw_file"]) as pdf:
        header = pdf.pages[0].extract_text() or ""
        if "截至115年10月1日止" not in header or "實施長度(m)" not in header or "實施日期" not in header:
            raise ValueError("Official restriction list period/units changed")
        for code, page_no, cases in (("莊-3-11", 14, post_cases), ("莊-8-1", 15, land_cases)):
            text = pdf.pages[page_no - 1].extract_text() or ""
            match = re.search(r"(?m)^" + code + r"\s+(\S+)\s+(.+?)\s+([\d.]+)\s+(\d{2,3}\.\d{2}\.\d{2})\s*$", text)
            if not match:
                raise ValueError("Official restriction record not uniquely extracted: " + code)
            records.append({"evidence_id": "motorcycle_exclusion:" + code, "evidence_type": "official_motorcycle_parking_exclusion",
                "restriction_id": code, "district_name": "新莊區", "source_category": match[1],
                "source_address_or_segment": match[2], "implementation_length_m": float(match[3]),
                "implementation_date": date_roc(match[4]), "source_roc_implementation_date": match[4],
                "list_as_of_date": "2026-10-01", "pdf_page": page_no, "extraction_status": "layout_extracted_requires_review",
                "case_links": context(cases, "address_or_road_context_only"), "sources": [sources[pdf_name]],
                "limitations": ["實際管制範圍依現場標誌；未進行現場查核。", "地址/路段相符不是StopID識別；機車禁停不證明特定穿越合法或出口至站牌接線。"]})

    for name, destination, address in (("xinzhuang_land_traffic.html", "新莊地政事務所", "新莊區中華路一段3號"),
                                        ("xinzhuang_ris_traffic.html", "新莊戶政事務所", "新北市新莊區中華路1段1號2樓")):
        _, text = html(name)
        require_address_in_source(text, address)
        if not re.search(r"出口1.*?往中華路步行約2分鐘", text) or "捷運新莊站(地政事務所)" not in text:
            raise ValueError("Official direction text changed: " + name)
        records.append({"evidence_id": "official_walking_directions:" + name.split(".")[0], "evidence_type": "official_destination_access_directions",
            "station_name": "新莊站", "exit_number": "1", "destination_name": destination, "destination_address": address,
            "source_stated_walk_minutes": 2, "source_stated_walk_is_approximate": True,
            "bus_stop_name_in_guidance": "捷運新莊站(地政事務所)",
            "case_links": context([land_cases[0]], "station_exit_and_named_stop_context_only"), "sources": [sources[name]],
            "limitations": ["來源目的地為中華路1號或3號；既有1000605來源地址為8號向北，未核實同側接線。", "文字步行約2分鐘不是此案例實測步行成本；未明示站牌ID、穿越位置或連續幾何。"]})

    transfers = json.loads((root / "data/official_cross_city/walking_transfers/transfers.json").read_text())
    case_ids = {r["transfer_id"] for r in transfers["records"]}
    for row in records:
        if any(link["case_id"] not in case_ids for link in row["case_links"]):
            raise ValueError("Context refers to a nonexistent original case")
        row.update({"evidence_status": "official_context_only_requires_independent_review", "requires_source_review": True,
                    "legal_transfer_verified": False, "usable_transfer_cost_minutes": None})
    manifest = {"schema_version": "1.0", "dataset_id": "walking_additional_source_manifest", "metadata": {
        "generated_at": generated, "source_updated_at": None, "record_count": len(sources),
        "grain": "one downloaded official source file and its receipt"}, "records": list(sources.values())}
    document = {"schema_version": "1.0", "dataset_id": "walking_additional_official_context", "metadata": {
        "generated_at": generated, "source_updated_at": None, "statistical_period": None, "record_count": len(records),
        "source_manifest": str(RAW / "manifest.json"), "original_transfer_file": "data/official_cross_city/walking_transfers/transfers.json",
        "grain": "one independently reviewable official project fact, restriction record or destination direction",
        "scope": "New official context for existing Xinzhuang cases; no new transfer connector or crossing accepted.",
        "data_kind": "official_context_evidence", "sources": list(sources.values()),
        "accepted_new_transfer_count": 0, "tdx_api_requests": 0, "independent_review_status": "pending_separate_agent",
        "independent_review_report": "map-data/quality/walking_additional_evidence_review.json",
        "units": {"*_m": "metres", "*_m2": "square metres", "source_stated_walk_minutes": "approximate minutes stated for the source destination; not the case transfer cost"},
        "limitations": ["候選案例只標示地點背景，沒有藉地址或同名連接StopID。", "停車管制、獲獎、工程總量及目的地交通說明均不足以證明現有案例的完整合法人行路徑。", "原18案例、原圖網及既有驗收未變更；原source文字保留於raw，不包為整理欄位。"]}, "records": records}
    write(root / RAW / "manifest.json", manifest)
    write(root / OUT, document)
    return {"records": len(records), "sources": len(sources), "accepted_new_transfers": 0, "generated_at": generated}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--offline", action="store_true", required=True)
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args()
    print(json.dumps(build(args.root)))


if __name__ == "__main__":
    main()
