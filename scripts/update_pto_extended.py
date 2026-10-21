#!/usr/bin/env python3
"""Download and normalize selected PTO statistics and the 2026 term-1 assessment.

Writes only raw_data/pto_extended and data/pto_extended. Offline replay checks
source existence, format and parsed content without retaining file hashes.
"""
from __future__ import annotations

import argparse
import base64
from datetime import datetime
from decimal import Decimal
import json
from pathlib import Path
import re
import subprocess
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup
import pdfplumber
import pypdfium2

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "raw_data/pto_extended"
OUT = ROOT / "data/pto_extended"
TZ = ZoneInfo("Asia/Taipei")
PAGES = {
    "annual": "https://pto.gov.taipei/RelData.aspx?ParentSN=5D189624CC86562D&sms=2A073ABACACC5A2A",
    "company": "https://pto.gov.taipei/RelData.aspx?ParentSN=EECB6D0233958887&sms=2A073ABACACC5A2A",
    "assessment": "https://pto.gov.taipei/News_Content.aspx?n=D95BCCD0723AEAC5&s=C104D9489EB993F2&sms=CBE6FC1D209236AE",
}
HOSTS = {"pto.gov.taipei", "www.pto.gov.taipei", "www-ws.gov.taipei"}
NUMBER = r"(?:ⓡ\s*)?(?:\d[\d,]*(?:\.\d+)?|[-－—…]|\.\.\.)"
ROUTE_FIELDS = ["operated_sections", "distance_km", "passenger_trips", "passengers_per_section", "revenue_twd", "revenue_per_km_twd"]
OPERATOR_FIELDS = ["approved_routes", "operating_route_length_km", "approved_vehicles", "allocated_vehicle_days", "operated_vehicle_days", "operated_trips", "operated_sections", "distance_km", "passenger_trips", "passenger_km", "revenue_twd", "diesel_liters", "revenue_per_km_twd"]


def now():
    return datetime.now(TZ).isoformat(timespec="seconds")


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".part")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    temporary.replace(path)


class Sources:
    def __init__(self, offline=False):
        self.offline = offline
        self.path = RAW / "manifest.json"
        self.entries = json.loads(self.path.read_text()) if self.path.exists() else {}

    def fetch(self, url, name, page_url=None):
        if urlparse(url).scheme != "https" or urlparse(url).hostname not in HOSTS:
            raise ValueError(f"Unsupported source: {url}")
        if Path(name).name != name:
            raise ValueError("Source filename must not contain directories")
        path = RAW / name
        old = self.entries.get(name)
        if self.offline:
            if not old or not path.is_file() or not path.stat().st_size or old["url"] != url:
                raise ValueError(f"Offline source missing or changed: {name}")
            if name.endswith(".pdf") and not path.read_bytes().startswith(b"%PDF-"):
                raise ValueError(f"Offline source is not a PDF: {name}")
            return path
        part = path.with_suffix(path.suffix + ".part")
        headers = path.with_suffix(path.suffix + ".headers")
        result = subprocess.run(["curl", "--fail", "--location", "--silent", "--show-error", "--proto", "=https", "--proto-redir", "=https", "--connect-timeout", "20", "--max-time", "180", "--retry", "2", "--dump-header", str(headers), "--output", str(part), "--write-out", "%{json}", url], capture_output=True, text=True)
        if result.returncode:
            part.unlink(missing_ok=True)
            raise RuntimeError(result.stderr)
        info = json.loads(result.stdout)
        if info["http_code"] != 200 or urlparse(info["url_effective"]).hostname not in HOSTS:
            raise ValueError(f"Unexpected source response: {info['http_code']}")
        if name.endswith(".pdf") and not part.read_bytes().startswith(b"%PDF-"):
            raise ValueError(f"Downloaded payload is not a PDF: {name}")
        response_headers = {}
        for line in headers.read_text().splitlines():
            if line.startswith("HTTP/"):
                response_headers = {}
            elif ":" in line:
                key, value = line.split(":", 1)
                response_headers[key.lower()] = value.strip()
        part.replace(path)
        headers.unlink(missing_ok=True)
        self.entries[name] = {"page_url": page_url or url, "url": url, "resolved_url": info["url_effective"], "raw_file": str(path.relative_to(ROOT)), "downloaded_at": now(), "source_updated_at": None, "http_last_modified": response_headers.get("last-modified"), "bytes": path.stat().st_size}
        write_json(self.path, self.entries)
        print(f"downloaded {name}: {path.stat().st_size} bytes", flush=True)
        return path


def attachments(path):
    soup = BeautifulSoup(path.read_text(encoding="utf-8"), "html.parser")
    result = []
    for a in soup.select("a[data-ccms_hitcount_relfile][href]"):
        query = parse_qs(urlparse(a["href"]).query)
        name = base64.b64decode(query["n"][0]).decode("utf-8")
        if name.lower().endswith(".pdf"):
            result.append({"document_id": a["data-ccms_hitcount_relfile"], "filename": name, "title": name[:-4], "url": a["href"]})
    if not result:
        raise ValueError(f"No PDF attachments: {path}")
    return result


def download(sources):
    docs = []
    for group, url in PAGES.items():
        path = sources.fetch(url, f"{group}_index.html")
        for attachment in attachments(path):
            title = attachment["title"]
            selected = (group == "annual" and title.startswith(("20621-01-04", "20621-01-03", "20621-01-01"))) or (group == "company" and title.startswith("20621-01-01")) or (group == "assessment" and "115年第1期" in title)
            if selected:
                sources.fetch(attachment["url"], attachment["filename"], url)
                docs.append({**attachment, "group": group, "source": sources.entries[attachment["filename"]]})
    return docs


def number(token):
    token = token.replace("ⓡ", "").replace(",", "").strip()
    if token in {"", "-", "－", "—", "…", "...", "--", "N/A"}:
        return None
    value = Decimal(token)
    if not value.is_finite():
        raise ValueError("Non-finite source numeric value")
    return int(value) if value == value.to_integral_value() else float(value)


def numeric_rows(text, count=None):
    suffix = "{" + str(count - 1) + "}" if count else "+"
    pattern = re.compile(r"^(.*?)\s+(" + NUMBER + r"(?:\s+" + NUMBER + r")" + suffix + r")$")
    result = []
    for line_no, line in enumerate(text.splitlines(), 1):
        match = pattern.fullmatch(line)
        if match:
            tokens = re.findall(NUMBER, match[2])
            result.append({"source_line": line_no, "label": match[1], "values": [number(v) for v in tokens], "source_tokens": tokens})
    return result


def period(text):
    match = re.search(r"中華民國\s*(\d{2,3})\s*年(?:\s*(\d{1,2})\s*月)?", text)
    if not match:
        raise ValueError("Reporting period missing")
    year, month = int(match[1]) + 1911, int(match[2]) if match[2] else None
    return {"roc_year": int(match[1]), "year": year, "month": month, "label": f"{year}-{month:02}" if month else str(year)}


def text_pages(doc):
    path = RAW / doc["filename"]
    with pdfplumber.open(path) as pdf:
        texts = [p.extract_text() or "" for p in pdf.pages]
    if any(not text.strip() for text in texts):
        raise ValueError(f"Text extraction incomplete: {path}")
    path.with_suffix(".txt").write_text("\n\f\n".join(texts) + "\n", encoding="utf-8")
    return texts


def envelope(dataset_id, records, metadata):
    return {"schema_version": 1, "dataset_id": dataset_id, "metadata": {"generated_at": now(), **metadata, "record_count": len(records)}, "records": records}


def typed_rows(doc, texts, fields, label_key):
    result = []
    for page, text in enumerate(texts, 1):
        body = re.split(r"備註[：:]|填表\s+審核", text)[0]
        for row in numeric_rows(body, len(fields)):
            label = re.sub(r"\s+|[①②③④]", "", row["label"])
            result.append({"document_id": doc["document_id"], "period": doc["period"]["label"], label_key: label, "is_total": label == "總計", **dict(zip(fields, row["values"])), "revised_fields": [field for field, token in zip(fields, row["source_tokens"]) if "ⓡ" in token], "missing_tokens": {field: token for field, token, value in zip(fields, row["source_tokens"], row["values"]) if value is None}, "source_page": page, "source_line": row["source_line"]})
    if not result or sum(r["is_total"] for r in result) != 1 or len({r[label_key] for r in result}) != len(result):
        raise ValueError(f"Unexpected row structure: {doc['filename']}")
    return result


def reconcile(rows, fields):
    total = next(r for r in rows if r["is_total"])
    detail = [r for r in rows if not r["is_total"]]
    checks = {}
    for field in fields:
        if any(r[field] is None for r in rows):
            checks[field] = {"status": "not_checkable_missing_values", "detail_minus_total": None}
            continue
        delta = sum(Decimal(str(r[field])) for r in detail) - Decimal(str(total[field]))
        checks[field] = {"status": "matches" if delta == 0 else "source_discrepancy_retained", "detail_minus_total": float(delta)}
        if delta and field not in {"distance_km", "operating_route_length_km", "approved_routes", "revenue_per_km_twd", "passengers_per_section"}:
            raise ValueError(f"Failed additive reconciliation: {field}, difference {delta}")
    return checks


def parse_statistics(docs):
    routes, operators, corridors, route_docs, operator_docs, corridor_docs = [], [], [], [], [], []
    route_checks, operator_checks, corridor_checks = [], [], []
    for doc in docs:
        if doc["group"] == "assessment":
            continue
        texts = text_pages(doc)
        doc = {**doc, "period": period(texts[0]), "page_count": len(texts), "source_updated_at": None}
        revision = re.search(r"\((\d{3})(\d{2})修\)", doc["title"])
        doc["revision_period"] = f"{int(revision[1]) + 1911}-{revision[2]}" if revision else None
        if doc["title"].startswith("20621-01-04"):
            values = typed_rows(doc, texts, ROUTE_FIELDS, "route_name")
            routes.extend(values)
            route_checks.append({"document_id": doc["document_id"], "checks": reconcile(values, ["operated_sections", "distance_km", "passenger_trips", "revenue_twd"])})
            route_docs.append(doc)
        elif doc["title"].startswith("20621-01-01"):
            values = typed_rows(doc, texts, OPERATOR_FIELDS, "operator_name")
            operators.extend(values)
            operator_checks.append({"document_id": doc["document_id"], "checks": reconcile(values, ["approved_vehicles", "allocated_vehicle_days", "operated_vehicle_days", "operated_trips", "operated_sections", "distance_km", "passenger_trips", "passenger_km", "revenue_twd", "diesel_liters"])})
            operator_docs.append(doc)
        elif doc["title"].startswith("20621-01-03"):
            values, checks = parse_corridors(doc, texts)
            corridors.extend(values)
            corridor_checks.extend(checks)
            corridor_docs.append(doc)
    if not routes or not operators or not corridors:
        raise ValueError("Expected statistics missing")
    write_json(OUT / "route_annual.json", envelope("pto_route_annual", routes, {"sources": route_docs, "grain": "published route name × calendar year, plus city total", "units": dict(zip(ROUTE_FIELDS, ["sections", "km", "passenger_trips", "passenger_trips/section", "TWD", "TWD/km"])), "reconciliation": route_checks, "warnings": ["Annual names have no verified correspondence to API route_id; combined routes and special services occur.", "Operated sections are not departures or vehicle trips.", "The published distance details differ from the total; original numbers are retained."]}))
    write_json(OUT / "operator_monthly.json", envelope("pto_operator_monthly", operators, {"sources": operator_docs, "grain": "operator × reporting month × document revision, plus city total", "coverage": sorted({r["period"] for r in operators}), "units": dict(zip(OPERATOR_FIELDS, ["routes", "km", "vehicles", "vehicle_days", "vehicle_days", "trips", "sections", "km", "passenger_trips", "passenger_km", "TWD", "liters_diesel", "TWD/km"])), "reconciliation": operator_checks, "warnings": ["Only documents on the two specified source pages are included; this is not an all-month time series.", "Company totals cannot be allocated to individual routes.", "Approved route counts cannot be summed because routes may be jointly operated."]}))
    write_json(OUT / "bus_lane_monthly.json", envelope("pto_bus_lane_monthly", corridors, {"sources": corridor_docs, "grain": "corridor × calendar month plus annual totals and all-corridor totals", "units": {"operated_sections": "sections", "passenger_trips": "passenger_trips"}, "reconciliation": corridor_checks, "warnings": ["Corridor passenger trips are not unique people; one trip may involve multiple corridors.", "Operated sections are not departures."]}))


def parse_corridors(doc, texts):
    columns = [["總計", "松江路", "新生南路", "信義路", "仁愛路"], ["南京東路", "民權東西路", "敦化南北路", "重慶北路", "中華路"], ["羅斯福路", "新光路", "玉門街", "忠孝西路"]]
    if len(texts) != len(columns):
        raise ValueError("Bus lane report page layout changed")
    rows = []
    for page, (text, names) in enumerate(zip(texts, columns), 1):
        compact = re.sub(r"\s+", "", text)
        if any(name not in compact for name in names):
            raise ValueError("Bus lane column heading changed")
        body = re.split(r"填表\s+審核", text)[0]
        parsed = numeric_rows(body, len(names) * 2)
        if len(parsed) != 13:
            raise ValueError("Expected 12 months plus annual total per corridor")
        for row in parsed:
            label = re.sub(r"\s+", "", row["label"])
            month = None if label in {"總計", "合計"} else int(label.removesuffix("月"))
            if month is not None and not 1 <= month <= 12:
                raise ValueError("Invalid corridor month")
            for i, name in enumerate(names):
                rows.append({"document_id": doc["document_id"], "year": doc["period"]["year"], "month": month, "period": f"{doc['period']['year']}-{month:02}" if month else str(doc["period"]["year"]), "is_annual_total": month is None, "is_all_corridors_total": name == "總計", "corridor_name": None if name == "總計" else name, "operated_sections": row["values"][2*i], "passenger_trips": row["values"][2*i+1], "source_page": page, "source_line": row["source_line"], "source_tokens": row["source_tokens"][2*i:2*i+2]})
    checks = []
    for name in [n for names in columns for n in names]:
        selected = [r for r in rows if (r["corridor_name"] or "總計") == name]
        annual = next(r for r in selected if r["is_annual_total"])
        months = [r for r in selected if not r["is_annual_total"]]
        for field in ["operated_sections", "passenger_trips"]:
            delta = sum(r[field] for r in months) - annual[field]
            if delta:
                raise ValueError(f"Corridor annual reconciliation failed: {name} {field}")
            checks.append({"corridor_name": None if name == "總計" else name, "field": field, "month_sum_minus_annual": delta})
    for month in [None, *range(1, 13)]:
        selected = [r for r in rows if r["month"] == month]
        total = next(r for r in selected if r["is_all_corridors_total"])
        for field in ["operated_sections", "passenger_trips"]:
            if sum(r[field] for r in selected if not r["is_all_corridors_total"]) != total[field]:
                raise ValueError(f"Corridor city-total reconciliation failed: {month} {field}")
    return rows, checks


OPERATORS = ["大都會", "欣欣", "大有", "大南", "光華", "臺北", "三重", "首都", "指南", "中興", "新店", "東南", "平均", "標準差", "淡水", "新北", "皇家"]
INDICATORS = {
    "A1": "場站檢查", "A2": "行車前確實開啟公車動態車機設備及設定正確路線", "A3": "站牌資訊正確性",
    "B1": "舒適與噪音", "B2": "公車資訊服務設施", "B3": "環保品質", "B4": "行車肇事率", "B5-1": "靜態安全設施及清潔檢查", "B5-2": "先進駕駛輔助系統有效性指標", "B6": "裝置監視錄影器妥善率", "B7": "車內外安全",
    "C1": "發車準點性", "C2": "過站不停比率", "C3": "駕駛長遵循路線", "C4": "駕駛長服務態度與儀容", "C5": "駕駛長健康管理",
    "D1": "低地板公車比例", "D2": "拒載老人及特殊需求乘客", "D3": "未依規定服務特殊需求乘客", "E1": "查核回覆", "E2": "重大違規", "E3": "違反道路交通管理處罰條例案件", "E4": "行車安全業務檢查", "E5": "加分項目-配合政府政策", "E6": "加分項目-採購電動公車", "E7": "扣分項目-改善行動", "E8": "扣分項目-違反勞基法及職業安全衛生法遭裁處",
}
STATION_METRICS = [
    ("dispatch_room_environment", "調度室環境", 10),
    ("rest_room_space_environment", "休憩室空間及環境", 10),
    ("rest_room_facilities", "休憩室設施", 6),
    ("toilet_environment", "盥洗室環境", 6),
    ("smoke_free_environment", "禁菸環境", 5),
    ("alcohol_tester", "酒測器", 10),
    ("vehicle_key_control", "車輛鑰匙管制", 5),
    ("landscaping", "綠美化", 3),
    ("parking_use", "停車場使用情形", 5),
    ("parking_pavement", "停車場鋪面", 4),
    ("parking_pavement_integrity", "停車場鋪面完整性", 5),
    ("parking_environment", "停車場環境", 10),
    ("washing_equipment", "洗車設備", 4),
    ("washing_area_cleanliness", "洗車場整潔", 6),
    ("recycling", "資源回收實施程度", 3),
    ("gender_friendly_toilets", "男女專用廁所或性別友善廁所", 2),
    ("gender_specific_rest_rooms", "男女性專用休息室", 2),
    ("sexual_harassment_prevention", "性騷擾防治", 4),
    ("station_farm", "場站農場", 2),
    ("maintenance_workshop_cleanliness", "維修保養廠所清潔", 2),
    ("entrance_warning_facilities", "出入警示設施", 5),
    ("adas_included_in_maintenance_records", "一級保養檢查紀錄有無將ADAS設備納入(無則扣分)", 2),
    ("total", "總分", 100),
]


def score_suffix(line, n=17):
    tokens = line.split()
    numeric = tokens[-n:]
    if len(numeric) != n or any(not re.fullmatch(r"-?\d[\d,]*(?:\.\d+)?|-", t) for t in numeric):
        raise ValueError(f"Unexpected score row: {line}")
    return [number(t) for t in numeric], numeric


def assessment_scores(page, doc):
    text = page.extract_text(x_tolerance=1, y_tolerance=3)
    (RAW / "assessment_scores_page_243.txt").write_text(text, encoding="utf-8")
    compact = re.sub(r"\s+", "", text)
    if "表5.1.1各公車業者各評鑑指標之得分" not in compact:
        raise ValueError("Assessment summary page changed")
    metrics = {}
    summary = {}
    current_grades, previous_grades = None, None
    for line_no, line in enumerate(text.splitlines(), 1):
        match = re.match(r"^([A-E]\d(?:-\d)?)\s", line)
        if match:
            code = match[1]
            values, tokens = score_suffix(line)
            weight = re.search(r"\s(\d+)%\s", line)
            metrics[code] = {"indicator_code": code, "indicator_name": INDICATORS[code], "weight_percent": int(weight[1]) if weight else None, "values": values, "source_tokens": tokens, "source_line": line_no}
        elif re.match(r"^總分\(未含", line):
            summary["base_score"] = score_suffix(line)[0]
        elif re.match(r"^總\s+分\s", line):
            summary["final_score"] = score_suffix(line)[0]
        elif line.startswith("(") and len(re.findall(r"\([\d.]+\)", line)) == 17:
            summary["previous_final_score"] = [number(x) for x in re.findall(r"\(([\d.]+)\)", line)]
        elif re.fullmatch(r"[優甲乙丙丁\s-]+", line.strip()) and len(line.split()) == 17:
            current_grades = line.split()
        elif line.startswith("(") and len(re.findall(r"\([優甲乙丙丁]\)|(?<!\()-(?!\))", line)) == 17:
            previous_grades = [x.strip("()") for x in re.findall(r"\([優甲乙丙丁]\)|(?<!\()-(?!\))", line)]
        elif re.match(r"^車\s+輛\s+數", line):
            summary["vehicles_2026_01"] = score_suffix(line)[0]
        elif re.match(r"^路\s+線\s+數", line):
            summary["route_count"] = score_suffix(line)[0]
    if set(metrics) != set(INDICATORS) or set(summary) != {"base_score", "final_score", "previous_final_score", "vehicles_2026_01", "route_count"} or not current_grades or not previous_grades:
        raise ValueError("Incomplete assessment summary extraction")
    result, checks = [], []
    for i, operator in enumerate(OPERATORS):
        if operator in {"平均", "標準差"}:
            continue
        row = {"document_id": doc["document_id"], "assessment_year": 2026, "assessment_term": 1, "operator_name": operator, "included_in_12_operator_comparison": i < 12, **{field: values[i] for field, values in summary.items()}, "grade": current_grades[i], "previous_grade": previous_grades[i], "indicator_scores": [{k: v for k, v in metric.items() if k not in {"values", "source_tokens"}} | {"weighted_score": metric["values"][i], "source_token": metric["source_tokens"][i]} for metric in metrics.values()], "source_page": 243, "source_table": "5.1.1", "printed_page": "5-2"}
        base_delta = sum(Decimal(str(metrics[code]["values"][i])) for code in metrics if code not in {"E5", "E6", "E7", "E8"}) - Decimal(str(row["base_score"]))
        final_delta = Decimal(str(row["base_score"])) + sum(Decimal(str(metrics[code]["values"][i])) for code in ["E5", "E6", "E7", "E8"]) - Decimal(str(row["final_score"]))
        checks.append({"operator_name": operator, "displayed_indicator_sum_minus_base_score": float(base_delta), "base_plus_adjustments_minus_final": float(final_delta)})
        if abs(base_delta) > Decimal("0.15") or abs(final_delta) > Decimal("0.01"):
            raise ValueError(f"Assessment score reconciliation failed: {operator}")
        result.append(row)
    return result, checks, {"source_page": 243, "source_table": "5.1.1", "column_labels": OPERATORS, "rows": [{k: v for k, v in metric.items() if k != "source_tokens"} for metric in metrics.values()], "summary_rows": summary, "current_grades": current_grades, "previous_grades": previous_grades}


def station_checks(pdfium, doc):
    result, grids, issues = [], [], []
    for page_no in range(313, 328):
        page = pdfium[page_no - 1]
        tp = page.get_textpage()
        text = tp.get_text_range()
        source_chars = []
        for ci in range(tp.count_chars()):
            character = tp.get_text_range(ci, 1)
            if character.strip():
                box = tp.get_charbox(ci)
                source_chars.append((character, (box[0]+box[2])/2, (box[1]+box[3])/2))
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        if lines[0] != f"A-{page_no-310}":
            raise ValueError("Station appendix page changed")
        operators, stations, dates = (lines[i].split() for i in [1, 2, 3])
        if len(operators) != len(stations) or len(stations) != len(dates) or len(set(operators)) != 1:
            raise ValueError(f"Unrecognized station headers on page {page_no}")
        header_line_start = text.index(lines[1])
        operator_matches = list(re.finditer(re.escape(operators[0]), lines[1]))
        centers = []
        for match in operator_matches:
            boxes = [tp.get_charbox(header_line_start + i) for i in range(match.start(), match.end())]
            centers.append((min(b[0] for b in boxes) + max(b[2] for b in boxes)) / 2)
        if len(centers) != len(operators):
            raise ValueError("Station source columns not identified")
        bounds = [centers[0] - (centers[1]-centers[0])/2] + [(a+b)/2 for a,b in zip(centers, centers[1:])] + [centers[-1]+(centers[-1]-centers[-2])/2]
        weight_matches = list(re.finditer(r"\d+%", text))
        if len(weight_matches) != len(STATION_METRICS):
            raise ValueError(f"Station score rows changed on page {page_no}")
        score_ys = []
        for match in weight_matches:
            box = tp.get_charbox(match.start())
            score_ys.append((box[1]+box[3])/2)
        grid = []
        for row_no, ((metric, name, weight), cy) in enumerate(zip(STATION_METRICS, score_ys), 1):
            actual_weight = int(weight_matches[row_no-1][0][:-1])
            if actual_weight != weight:
                issues.append({"source_page": page_no, "metric_id": metric, "printed_weight_percent": actual_weight, "usual_printed_weight_percent": weight, "status": "source_weight_differs_from_other_station_tables_retained"})
            weight = actual_weight
            top = (score_ys[row_no-2]+cy)/2 if row_no>1 else cy+5
            bottom = (cy+score_ys[row_no])/2 if row_no<len(score_ys) else cy-5
            cells = []
            for column_no in range(len(operators)):
                # Select glyph centers rather than intersecting glyph boxes: the
                # printed 100% weight can overhang into the first score column.
                cell = "".join(char for char, cx, char_y in source_chars if bounds[column_no] < cx < bounds[column_no+1] and bottom < char_y < top)
                if metric == "adas_included_in_maintenance_records":
                    value = None
                    if cell not in {"有", "無", ""}:
                        raise ValueError(f"Unexpected ADAS source marker: {cell}")
                else:
                    try:
                        value = number(cell)
                    except Exception as exc:
                        raise ValueError(f"Unrecognized station cell p{page_no} r{row_no} c{column_no+1}: {cell!r}") from exc
                cells.append({"source_token": cell or None, "value": value})
                if value is not None and value > weight:
                    issues.append({"source_page": page_no, "source_column": column_no+1, "metric_id": metric, "source_value": value, "printed_weight_percent": weight, "status": "source_value_exceeds_printed_weight_retained"})
            grid.append({"metric_id": metric, "metric_name": name, "printed_weight_percent": weight, "source_row": row_no, "cells": cells})
        for i, (operator, station, date) in enumerate(zip(operators, stations, dates)):
            date_match = re.fullmatch(r"(\d{1,2})月(\d{1,2})日", date)
            if not date_match:
                raise ValueError(f"Unrecognized partial inspection date: {date}")
            result.append({"inspection_record_id": f"{doc['document_id']}:{page_no}:{i+1}", "document_id": doc["document_id"], "assessment_year": 2026, "assessment_term": 1, "operator_name": operator, "station_name_source": station, "inspection_date": None, "inspection_month": int(date_match[1]), "inspection_day": int(date_match[2]), "inspection_date_source": date, "inspector_id": None, "source_column": i+1, "source_page": page_no, "printed_page": lines[0], "checks": [{k: v for k,v in metric.items() if k != "cells"} | {"score": metric["cells"][i]["value"], "source_token": metric["cells"][i]["source_token"]} for metric in grid]})
        grids.append({"source_page": page_no, "printed_page": lines[0], "operator_headers": operators, "station_headers": stations, "date_headers": dates, "rows": grid})
    if len({r["inspection_record_id"] for r in result}) != len(result):
        raise ValueError("Duplicate station observation identity")
    return result, grids, issues


def assessment_punctuality(pdf, doc):
    groups = {185: ["大都會", "欣欣", "大有", "大南"], 186: ["大南", "光華", "臺北", "三重"], 187: ["首都", "指南", "中興", "新店"], 188: ["東南", "淡水", "新北", "皇家"]}
    result, grids = [], []
    for page_no, companies in groups.items():
        page = pdf.pages[page_no-1]
        tables = page.find_tables()
        if len(tables) != 1:
            raise ValueError("Punctuality summary layout changed")
        cells = tables[0].extract()
        if len(cells[0]) != 9 or cells[0][0] != "調查路線":
            raise ValueError("Punctuality columns changed")
        header_geometry = tables[0].rows[0].cells
        current, rate, group_no = None, None, 0
        for row_no, row in enumerate(cells[2:], 3):
            if row[8]:
                current = companies[group_no]
                rate = number(row[8])
                group_no += 1
            if not current:
                raise ValueError("Punctuality row lacks company")
            fields = ["valid_peak_samples", "valid_off_peak_samples", "punctual_peak_samples", "punctual_off_peak_samples", "punctual_peak_ratio", "punctual_off_peak_ratio", "route_punctuality_ratio"]
            values = {key: number(cell) if cell is not None else None for key, cell in zip(fields, row[1:8])}
            row_geometry = tables[0].rows[row_no-1].cells
            merged_flags = [row_geometry[i+1] is None and row_geometry[i] is not None and abs(row_geometry[i][0]-header_geometry[i][0])<0.01 and abs(row_geometry[i][2]-header_geometry[i][2])<0.01 for i in [1,3,5]]
            if any(merged_flags) and not all(merged_flags):
                raise ValueError("Punctuality row has a partially merged sample layout")
            merged = all(merged_flags)
            if merged:
                # A source cell spanning peak + off-peak is an unspecified
                # stratum, not a peak observation followed by missing data.
                values.update({"unstratified_valid_samples": values["valid_peak_samples"], "unstratified_punctual_samples": values["punctual_peak_samples"], "unstratified_punctuality_ratio": values["punctual_peak_ratio"]})
                for key in fields[:-1]:
                    values[key] = None
                values["stratification"] = "merged_peak_off_peak_columns_unspecified"
                checks = [(values["unstratified_valid_samples"], values["unstratified_punctual_samples"], values["unstratified_punctuality_ratio"])]
            else:
                values.update({"unstratified_valid_samples": None, "unstratified_punctual_samples": None, "unstratified_punctuality_ratio": None, "stratification": "separate_peak_off_peak_columns"})
                checks = [tuple(values[f"{prefix}_{band}_{suffix}"] for prefix,suffix in [("valid","samples"),("punctual","samples"),("punctual","ratio")]) for band in ["peak", "off_peak"]]
            for valid, punctual, ratio in checks:
                if (valid is None) != (punctual is None) or (valid is None) != (ratio is None):
                    raise ValueError("Mismatched missing punctuality fields")
                if valid is not None and (punctual > valid or not 0<=ratio<=1 or abs(punctual/valid-ratio)>.001):
                    raise ValueError("Punctuality source values inconsistent")
            result.append({"observation_id": f"{doc['document_id']}:{page_no}:{row_no}", "document_id": doc["document_id"], "assessment_year": 2026, "assessment_term": 1, "operator_name": current, "route_name_source": row[0].replace("\n", " "), "route_id": None, **values, "company_punctuality_ratio": rate, "source_page": page_no, "source_table": "4.3.1", "source_row": row_no})
        if group_no != len(companies):
            raise ValueError("Punctuality company blocks changed")
        grids.append({"source_page": page_no, "source_table": "4.3.1", "bbox": list(tables[0].bbox), "cells": cells, "cell_bboxes": [list(row.cells) for row in tables[0].rows], "company_block_order": companies})
    return result, grids


def parse_assessment(docs):
    doc = next(d for d in docs if d["group"] == "assessment")
    path = RAW / doc["filename"]
    pdfium = pypdfium2.PdfDocument(path)
    texts = [page.get_textpage().get_text_range() for page in pdfium]
    path.with_suffix(".txt").write_text("\n\f\n".join(texts), encoding="utf-8")
    if len(texts) != 607 or "115年" not in texts[0] or "第1期" not in texts[0]:
        raise ValueError("Assessment report identity changed")
    doc = {**doc, "page_count": len(texts), "report_period": {"year": 2026, "term": 1}, "report_publication_month": "2026-06", "source_page_updated_at": "2026-07-21T16:21:00+08:00", "source_updated_at": None}
    common = {"sources": [doc], "assessment_period": "2026 term 1", "observation_periods": {"secondary_records": "2025-07/2026-01 (indicator-specific exceptions in report)", "c1_departure_punctuality": "2025-08/2026-01", "b2_c2_field_surveys": "2026-01/2026-03", "station_checks": "month/day only on individual inspection tables; year not supplied per cell"}, "period_evidence": {"source_page": 5, "printed_page": "0-1"}}
    with pdfplumber.open(path) as pdf:
        scores, score_checks, score_grid = assessment_scores(pdf.pages[242], doc)
        punctuality, punctuality_grids = assessment_punctuality(pdf, doc)
    stations, station_grids, station_issues = station_checks(pdfium, doc)
    write_json(OUT / "assessment_operator_scores.json", envelope("pto_assessment_operator_scores", scores, {**common, "grain": "operator × assessment term", "units": {"weighted_score": "score_points", "weight_percent": "percent", "vehicles_2026_01": "vehicles", "route_count": "routes"}, "reconciliation": score_checks, "warnings": ["Scores are historical assessment results, not real-time performance.", "The 12-operator comparison excludes Tamsui, New Taipei and Royal due to small fleets.", "Published rounded component scores may differ slightly from published totals; both are retained.", "E3/E4 aggregate results are not arithmetic means of company scores."]}))
    write_json(OUT / "assessment_station_checks.json", envelope("pto_assessment_station_checks", stations, {**common, "grain": "published inspection column × station × partial date; duplicate station/date columns retained", "units": {"score": "points", "printed_weight_percent": "percent"}, "quality_issues": station_issues, "warnings": ["The PDF does not identify inspectors; adjacent duplicate station/date columns are separate source observations and must not be deduplicated blindly.", "Inspection dates are month/day only; inspection_date remains null and no year is invented.", "Blank score cells remain null; the source contains a few scores above the printed weights and these are retained as source anomalies.", "These are historical inspection scores, not beds, usable parking capacities, or driver duty records."]}))
    write_json(OUT / "assessment_punctuality.json", envelope("pto_assessment_punctuality", punctuality, {**common, "grain": "source sampled route row × operator × survey stratum; repeated route names retained", "units": {"samples": "observations", "ratio": "fraction_0_1"}, "stratification_counts": {label: sum(r["stratification"] == label for r in punctuality) for label in ["separate_peak_off_peak_columns", "merged_peak_off_peak_columns_unspecified"]}, "warnings": ["Sample survey results do not cover every trip or route.", "When the source merges peak and off-peak columns, peak/off_peak values are null and original numbers are stored in unstratified fields; they are not classified as peak samples.", "Blank cells in genuinely separate columns remain null, never zero.", "Repeated names are distinct source rows; no API route_id has been assigned.", "Company/route ratios follow the report method and need not equal ratios of pooled counts."]}))
    table_records = [{"table_id": "5.1.1", "extraction_status": "normalized_with_separate_typed_export", **score_grid}, *[{"table_id": f"4.3.1:{g['source_page']}", "extraction_status": "normalized_with_separate_typed_export", **g} for g in punctuality_grids], *[{"table_id": f"station:{g['source_page']}", "extraction_status": "normalized_with_source_anomalies", **g} for g in station_grids]]
    write_json(OUT / "assessment_tables.json", envelope("pto_assessment_tables", table_records, {**common, "grain": "source table and cell grid", "coverage": "Summary table 5.1.1, punctuality table 4.3.1, and station inspection appendix A-3 to A-17; full 607-page PDF and extracted text in raw_data.", "units": "Column-specific; see typed exports. This export preserves table cell layout."}))


def delivery():
    items = []
    specifications = [
        ("annual_route_efficiency", ["route_annual.json"], "2025", "complete"),
        ("bus_lane_monthly_statistics", ["bus_lane_monthly.json"], "2025-01/2025-12 with annual totals", "complete"),
        ("operator_monthly_statistics", ["operator_monthly.json"], "2025-10, 2026-02, 2026-05, 2026-07; revisions retained", "complete_for_specified_pages"),
        ("bus_assessment_station_facilities", ["assessment_operator_scores.json", "assessment_station_checks.json", "assessment_punctuality.json", "assessment_tables.json"], "2026 term 1; observation windows differ by indicator", "complete_for_requested_topics"),
    ]
    for dataset_id, names, period_label, status in specifications:
        outputs, limits = [], []
        for name in names:
            path = OUT / name
            obj = json.loads(path.read_text())
            outputs.append({"path": str(path.relative_to(ROOT)), "record_count": len(obj["records"])})
            limits.extend(obj["metadata"].get("warnings", []))
        items.append({"dataset_id": dataset_id, "status": status, "output_files": outputs, "record_count": sum(x["record_count"] for x in outputs), "period": period_label, "limitations": list(dict.fromkeys(limits))})
    write_json(RAW / "delivery.json", {"generated_at": now(), "producer_agent": "pto", "source_manifest": "raw_data/pto_extended/manifest.json", "update_command": ".venv/bin/python scripts/update_pto_extended.py", "offline_command": ".venv/bin/python scripts/update_pto_extended.py --offline", "datasets": items})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--download-only", action="store_true")
    args = parser.parse_args()
    RAW.mkdir(parents=True, exist_ok=True)
    OUT.mkdir(parents=True, exist_ok=True)
    sources = Sources(args.offline)
    docs = download(sources)
    if args.download_only:
        return
    parse_statistics(docs)
    parse_assessment(docs)
    delivery()


if __name__ == "__main__":
    main()
