"""Audit additional walking context against retained official HTML and PDF cells."""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import date, datetime
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import subprocess
import unicodedata
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = Path("map-data/walking/additional_evidence.json")
REPORT = Path("map-data/quality/walking_additional_evidence_review.json")
RAW = Path("raw_data/walking_additional")
ORIGINALS = ("data/official_cross_city/walking_transfers/transfers.json",
             "data/official_cross_city/walking_transfers/graphs.json.gz", "map-data/walking/paths.geojson",
             "map-data/walking/facilities.geojson", "map-data/walking/candidate_paths.geojson")
PROJECT = "新北市新莊區中華路一段(新莊捷運站至運動場段)人行道改善工程"
PDF_NAME = "ntpc_arcade_motorcycle_exclusion_20261001.pdf"
VISUALLY_REVIEWED = {
    PDF_NAME: (1546462, "2026-10-24T12:38:59+08:00"),
    "xinzhuang_award_image1.jpg": (30178, "2026-10-24T12:38:12+08:00"),
    "xinzhuang_award_image2.jpg": (44183, "2026-10-24T12:38:13+08:00"),
}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def strict_equal(left, right):
    if type(left) is not type(right):
        return False
    if isinstance(right, dict):
        return left.keys() == right.keys() and all(strict_equal(left[key], value) for key, value in right.items())
    if isinstance(right, list):
        return len(left) == len(right) and all(strict_equal(a, b) for a, b in zip(left, right))
    return left == right


def compact(value):
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", value))


def roc_date(year, month, day):
    return date(int(year) + 1911, int(month), int(day)).isoformat()


class VisibleHTML(HTMLParser):
    """Parse source table cells independently of the producer's HTML library."""
    def __init__(self, body):
        super().__init__(convert_charrefs=True)
        self.text_parts, self.rows, self.links = [], [], []
        self.skipped, self.row, self.cell = False, None, None
        self.feed(body.decode("utf-8-sig"))

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style", "noscript"}:
            self.skipped = True
        if tag == "tr":
            self.row = []
        if tag in {"td", "th"}:
            self.cell = []
        self.links.extend(value for key, value in attrs if key in {"href", "src"} and value)

    def handle_endtag(self, tag):
        if tag in {"script", "style", "noscript"}:
            self.skipped = False
        if tag in {"td", "th"} and self.cell is not None:
            if self.row is not None:
                self.row.append(" ".join(self.cell).strip())
            self.cell = None
        if tag == "tr" and self.row is not None:
            self.rows.append(self.row)
            self.row = None

    def handle_data(self, text):
        if self.skipped:
            return
        value = " ".join(text.split())
        if value:
            self.text_parts.append(value)
            if self.cell is not None:
                self.cell.append(value)

    @property
    def text(self):
        return " ".join(self.text_parts)


def pdf_lines(path):
    from pdfminer.high_level import extract_pages
    from pdfminer.layout import LTTextLine
    from pdfminer.pdfdocument import PDFDocument
    from pdfminer.pdfpage import PDFPage
    from pdfminer.pdfparser import PDFParser

    def descend(node):
        if isinstance(node, LTTextLine):
            yield {"text": node.get_text().strip(), "x": node.x0, "y": node.y0}
        elif hasattr(node, "__iter__"):
            for child in node:
                yield from descend(child)

    with path.open("rb") as stream:
        count = sum(1 for _ in PDFPage.create_pages(PDFDocument(PDFParser(stream))))
    pages = {number: list(descend(page)) for number, page in zip((1, 14, 15), extract_pages(path, page_numbers=[0, 13, 14]))}
    require(set(pages) == {1, 14, 15}, "PDF reviewed pages are missing")
    return pages, count


def restriction_cells(lines, code):
    anchors = [line for line in lines if line["text"] == code]
    require(len(anchors) == 1, "PDF restriction code is not unique")
    anchor = anchors[0]
    cells = [line["text"] for line in sorted(lines, key=lambda value: value["x"]) if abs(line["y"] - anchor["y"]) < 1]
    require(len(cells) == 5 and cells[0] == code, "PDF restriction columns cannot be independently aligned")
    require(re.fullmatch(r"\d+(?:\.\d+)?", cells[3]) and re.fullmatch(r"\d{2,3}\.\d{2}\.\d{2}", cells[4]), "PDF length/date column semantics differ")
    return cells


def context_only(row):
    require(row.get("legal_transfer_verified") is False and row.get("usable_transfer_cost_minutes") is None,
            "Context must not become a legal transfer or transfer-time cost")
    require(row.get("requires_source_review") is True and row.get("evidence_status") == "official_context_only_requires_independent_review",
            "Unresolved context marker was removed")
    for link in row.get("case_links", []):
        require(link.get("verified_stop_id_identity") is False and link.get("verified_exit_to_stop_connector") is False,
                "Address/name context must not certify stop identity or a connector")


def run(root):
    document = json.loads((root / OUTPUT).read_bytes())
    manifest = json.loads((root / RAW / "manifest.json").read_bytes())
    require(document["schema_version"] == "1.0" and document["dataset_id"] == "walking_additional_official_context"
            and manifest["schema_version"] == "1.0" and manifest["dataset_id"] == "walking_additional_source_manifest", "Dataset/manifest identity differs")
    generated = document["metadata"]["generated_at"]
    sources, parsed, checks = {}, {}, []
    receipts = sorted((root / RAW).glob("*.source.json"))
    require(len(receipts) == 8, "Expected eight independently retained source receipts")
    for path in receipts:
        receipt = json.loads(path.read_bytes())
        raw = root / receipt["raw_file"]
        name = raw.name
        require(raw.is_relative_to(root / RAW) and receipt["http_status"] == 200 and len(raw.read_bytes()) == receipt["bytes"], "Raw source path/status/byte count differs")
        require(all(urlsplit(receipt[key]).scheme == "https" and urlsplit(receipt[key]).hostname.endswith(".ntpc.gov.tw") for key in ("source_url", "effective_url")), "Source is not the recorded official HTTPS domain")
        sources[name] = {"source_id": name, "source_page": receipt["source_url"], "download_url": receipt["effective_url"],
                         "source_publication_date": receipt.get("source_publication_date"), "source_updated_at": receipt.get("source_updated_at"),
                         "source_period": receipt.get("source_period"), "downloaded_at": receipt["downloaded_at"], "raw_file": receipt["raw_file"],
                         "receipt_file": path.relative_to(root).as_posix(), "http_status": 200, "bytes": receipt["bytes"],
                         "reuse_terms": "Official public source; source-specific open-data reuse permission not inferred."}
        if name.endswith(".html"):
            parsed[name] = VisibleHTML(raw.read_bytes())
        if name in VISUALLY_REVIEWED:
            require((receipt["bytes"], receipt["downloaded_at"]) == VISUALLY_REVIEWED[name], "Visual source snapshot changed; inspect the replacement before certifying")
        checks.append({"source_id": name, "status": "pass", "bytes": receipt["bytes"], "downloaded_at": receipt["downloaded_at"]})
    require(strict_equal(manifest["records"], list(sources.values())) and manifest["metadata"]["record_count"] == 8 and manifest["metadata"]["generated_at"] == generated,
            "Manifest source receipts/count/generation differ")
    meta = document["metadata"]
    require(strict_equal(meta["sources"], manifest["records"]) and meta["data_kind"] == "official_context_evidence" and meta["record_count"] == 6
            and meta["source_updated_at"] is None and meta["statistical_period"] is None and meta["accepted_new_transfer_count"] == 0
            and meta["tdx_api_requests"] == 0 and meta["independent_review_report"] == str(REPORT), "Dataset provenance/acceptance metadata differs")
    require(meta["units"] == {"*_m": "metres", "*_m2": "square metres", "source_stated_walk_minutes": "approximate minutes stated for the source destination; not the case transfer cost"}, "Units were changed")
    html = parsed["ntpc_2025_sidewalk_results.html"]
    header = ["項次", "轄區", "案件名稱", "人行道改善長度(m)", "人行道改善面積(平方公尺)", "路口改善數量(個)", "總經費"]
    require(header in html.rows and "114年1~12月人行道改善" in html.text, "Official annual project period/column units differ")
    selected = [dict(zip(header, cells)) for cells in html.rows if len(cells) == 7 and cells[2] == PROJECT]
    require(len(selected) == 1, "Project table row is ambiguous")
    project = selected[0]
    publication = parsed["xinzhuang_award_20260902.html"].text
    pub = re.search(r"發佈日期\s*(\d{4}-\d{2}-\d{2})", publication).group(1)
    updated = re.search(r"更新日期[:：]?\s*(\d{4}-\d{2}-\d{2})", publication).group(1)
    event = re.search(r"於(\d{3})年(\d+)月(\d+)日", publication)
    require(PROJECT in publication and "2026馬路好行評選工程案件-佳作" in publication, "Award project/result differs")
    require("新莊區公所辦理「新莊區中華路一段人行道改善工程」，亦獲得佳作" in parsed["ntpc_award_20260805.html"].text, "Supplemental city award notice does not corroborate the project award")
    for name in ("xinzhuang_award_image1.jpg", "xinzhuang_award_image2.jpg"):
        asset = parse_qs(urlsplit(sources[name]["download_url"]).query)["file"][0]
        require(any(asset in link for link in parsed["xinzhuang_award_20260902.html"].links), "Award image is not linked by the retained official page")
    pages, page_count = pdf_lines(root / RAW / PDF_NAME)
    title = compact(" ".join(line["text"] for line in pages[1]))
    as_of = re.search(r"截至(\d+)年(\d+)月(\d+)日止", title)
    require(as_of and "實施長度(m)" in title and "現場標誌" in title and "牌面指示範圍" in title, "PDF title/date/unit/on-site scope warning differs")
    list_date = roc_date(*as_of.groups())
    expected_dates = {
        "ntpc_2025_sidewalk_results.html": (None, re.search(r"更新日期[:：]\s*(\d{4}-\d{2}-\d{2})", html.text).group(1), "2025-01/2025-12"),
        "xinzhuang_award_20260902.html": (pub, updated, None), "ntpc_award_20260805.html": (re.search(r"發佈日期[:：]\s*(\d{4}-\d{2}-\d{2})", parsed["ntpc_award_20260805.html"].text).group(1), None, None),
        PDF_NAME: (None, None, list_date), "xinzhuang_award_image1.jpg": (None, None, None), "xinzhuang_award_image2.jpg": (None, None, None),
        "xinzhuang_ris_traffic.html": (None, None, None)}
    land_date = re.search(r"最後更新日期[:：](\d{3})-(\d{2})-(\d{2})", parsed["xinzhuang_land_traffic.html"].text)
    expected_dates["xinzhuang_land_traffic.html"] = (None, roc_date(*land_date.groups()), None)
    for name, dates in expected_dates.items():
        require(tuple(sources[name][field] for field in ("source_publication_date", "source_updated_at", "source_period")) == dates, "Publication/update/period conflated for " + name)

    def links(stop, relation, exits=(1, 2)):
        return [{"case_id": f"xinzhuang:exit:{number}:stop_location:{stop}", "relation": relation,
                 "verified_stop_id_identity": False, "verified_exit_to_stop_connector": False} for number in exits]

    expected = [{"evidence_id": "xinzhuang_zhonghua_sidewalk_project_2025", "evidence_type": "official_project_results", "project_name": project["案件名稱"],
                 "district_name": project["轄區"], "source_record_number": int(project["項次"]), "statistical_period": "2025-01/2025-12",
                 "improved_sidewalk_length_m": int(project[header[3]].replace(",", "")), "improved_sidewalk_area_m2": int(project[header[4]].replace(",", "")),
                 "improved_intersection_count": int(project[header[5]]), "case_links": links("1000605", "same_road_segment_context_only"), "sources": [sources["ntpc_2025_sidewalk_results.html"]]},
                {"evidence_id": "xinzhuang_zhonghua_sidewalk_award_2026", "evidence_type": "official_project_award", "project_name": PROJECT,
                 "award_name": "2026馬路好行評選工程案件", "award_result": "佳作", "awarding_date": roc_date(*event.groups()), "publication_date": pub, "page_updated_at": updated,
                 "case_links": links("1000605", "same_road_segment_context_only"), "sources": [sources[name] for name in ("xinzhuang_award_20260902.html", "ntpc_award_20260805.html", "xinzhuang_award_image1.jpg", "xinzhuang_award_image2.jpg")]}]
    for code, page, stop in (("莊-3-11", 14, "1165604194"), ("莊-8-1", 15, "1000605")):
        cells = restriction_cells(pages[page], code)
        expected.append({"evidence_id": "motorcycle_exclusion:" + code, "evidence_type": "official_motorcycle_parking_exclusion", "restriction_id": code,
                         "district_name": "新莊區", "source_category": cells[1], "source_address_or_segment": cells[2], "implementation_length_m": float(cells[3]),
                         "implementation_date": roc_date(*cells[4].split(".")), "source_roc_implementation_date": cells[4], "list_as_of_date": list_date,
                         "pdf_page": page, "extraction_status": "layout_extracted_requires_review", "case_links": links(stop, "address_or_road_context_only"), "sources": [sources[PDF_NAME]]})
    for name, destination, address in (("xinzhuang_land_traffic.html", "新莊地政事務所", "新莊區中華路一段3號"),
                                       ("xinzhuang_ris_traffic.html", "新莊戶政事務所", "新北市新莊區中華路1段1號2樓")):
        text = parsed[name].text
        require(compact(address) in compact(text), "Destination address is not explicit in the official source")
        walk = re.search(r"出口(\d+).*?往中華路步行約(\d+)分鐘", text)
        require(walk and "捷運新莊站(地政事務所)" in text, "Exit, approximate destination walk or named bus-stop context differs")
        expected.append({"evidence_id": "official_walking_directions:" + name.split(".")[0], "evidence_type": "official_destination_access_directions",
                         "station_name": "新莊站", "exit_number": walk[1], "destination_name": destination, "destination_address": address,
                         "source_stated_walk_minutes": int(walk[2]), "source_stated_walk_is_approximate": True, "bus_stop_name_in_guidance": "捷運新莊站(地政事務所)",
                         "case_links": links("1000605", "station_exit_and_named_stop_context_only", exits=(1,)), "sources": [sources[name]]})
    require(len(document["records"]) == len(expected) == 6, "Evidence row population differs")
    for row, facts in zip(document["records"], expected):
        context_only(row)
        facts.update({"evidence_status": "official_context_only_requires_independent_review", "requires_source_review": True, "legal_transfer_verified": False, "usable_transfer_cost_minutes": None})
        require(strict_equal({key: value for key, value in row.items() if key != "limitations"}, facts), "Raw-source field mismatch: " + row["evidence_id"])
        require(isinstance(row["limitations"], list) and bool(row["limitations"]), "Context limitations were dropped")
    transfers = json.loads((root / ORIGINALS[0]).read_bytes())
    require(len(transfers["records"]) == 18 and all(row["status"] == "unverified" and row["walking_distance_m"] is None and row["path_edge_ids"] == [] for row in transfers["records"]), "Original unverified transfer boundary changed")
    cases = {row["transfer_id"]: row for row in transfers["records"]}
    require(all(link["case_id"] in cases for row in document["records"] for link in row["case_links"]), "Context refers to an invented original case")
    require(cases["xinzhuang:exit:1:stop_location:1000605"]["bus_stop_address"] == "中華路一段8號(向北)" and cases["xinzhuang:exit:1:stop_location:1165604194"]["bus_stop_address"] == "中正路265號(向東)", "Original street-side address context changed")
    for path in ORIGINALS:
        before = subprocess.run(["git", "-c", "core.fsmonitor=false", "show", "HEAD:" + path], cwd=root, check=True, capture_output=True).stdout
        require(before == (root / path).read_bytes(), "An original walking file changed from the committed baseline: " + path)
    require(json.loads((root / OUTPUT).read_bytes()) == document, "Additional evidence changed during review")
    return {"schema_version": "1.0", "dataset_id": "walking_additional_evidence_independent_review", "metadata": {
        "reviewed_at": datetime.now().astimezone().isoformat(timespec="seconds"), "reviewer_agent": "/root/crosswalk", "independent_of_producer": True,
        "producer_imported": False, "status": "pass_with_unresolved_transfer_requirements", "reviewed_generated_at": generated, "record_count": 6,
        "method": "Standard-library HTML table cells/visible content; independent PDFMiner line-coordinate row reconstruction; actual visual inspection of PDF pages 1/14/15 and both JPEGs; full receipt/date/unit/ordinal checks and byte comparison of five original walking outputs against committed HEAD. No producer imports or network requests.",
        "source_count": 8, "source_checks": checks, "source_kind_counts": dict(Counter(Path(name).suffix for name in sources)), "pdf_page_count": page_count,
        "tdx_api_requests": 0,
        "visually_inspected_pdf_pages": [1, 14, 15], "visually_inspected_images": ["xinzhuang_award_image1.jpg", "xinzhuang_award_image2.jpg"],
        "visual_findings": ["Page 1 explicitly labels metres, the 115-10-01 list cutoff and on-site sign extent limitation.", "Page 14 row 莊-3-11: community, Zhongzheng Road 265, 13.7 metres, ROC 111.05.04.", "Page 15 row 莊-8-1: road section, Zhonghua Road Section 1, 200.0 metres, ROC 102.09.01.", "JPEGs show an award ceremony and certificate, with project name/commendation; neither depicts an exit-to-stop path or georeferenced crossing."],
        "original_transfer_case_count": 18, "original_unverified_case_count": 18, "accepted_new_transfer_count": 0, "original_outputs_equal_committed_baseline": list(ORIGINALS),
        "limitations": ["Project quantities, an award, parking restrictions and approximate access directions do not verify a legal pedestrian route, stop-side identity or boarding permission.", "The original 18 cases remain unverified with null walking costs; current on-site access has not been surveyed.", "The PDF source rows remain layout_extracted_requires_review in the producer; this separate source/visual check confirms selected cells only and does not convert them into legal route evidence."]}, "records": [{"output_path": str(OUTPUT), "dataset_id": document["dataset_id"], "record_count": 6,
            "reviewed_generated_at": generated, "sources": manifest["records"], "status": "pass_with_source_limitations"}]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args()
    report = run(args.root.resolve())
    target = args.root / REPORT
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps({"status": report["metadata"]["status"], "records": 6, "sources": 8, "accepted_new_transfers": 0}))


if __name__ == "__main__":
    main()
