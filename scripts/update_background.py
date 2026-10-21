#!/usr/bin/env python3
"""Normalize recent Taipei background data. Cached sources are reused unless --refresh."""
from __future__ import annotations

import argparse
import csv
from datetime import datetime
from decimal import Decimal
import io
import json
from pathlib import Path
import re
import subprocess
import sys
from urllib.parse import urljoin, urlparse
import zipfile
import xml.etree.ElementTree as ET
from zoneinfo import ZoneInfo
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "raw_data/background"
OUT = ROOT / "data/background"
PAGES = {
    "taipei_annual": "https://data.taipei/dataset/detail?id=90dd063e-2aca-49aa-a7fb-73130610313c",
    "taipei_monthly": "https://data.taipei/dataset/detail?id=bd0c2b6b-7b73-4b08-9b5a-1fde6b65f8d3",
    "national_highway_monthly": "https://data.gov.tw/dataset/30394",
    "vehicle_cost": "https://data.gov.tw/dataset/135318",
    "mode_use_survey_2024": "https://www.motc.gov.tw/ch/app/data/view?id=56&module=survey&serno=202506260000",
    "operator_violations": "https://www.thb.gov.tw/BusinessCoach_Content.aspx?n=308&s=886&type=1",
}
MISSING = {"", "-", "--", "－", "—", "…", "...", "N/A", "NA", "(X)", "X", "x", "－ －"}
NATIONAL_CSV = "https://ws.thb.gov.tw/Download.ashx?u=LzAwMS9VcGxvYWQvNDM2L3JlbGZpbGUvMTM2NTgvNDY4LzBhOTI1MmQ2LWRiMTAtNGI4Mi1iZGQ5LTk4MzQwZDY2Mjg3ZS5jc3Y%3D&n=5LiA6Iis5YWs6Lev5rG96LuK5a6i6YGL5qaC5rOBLmNzdg%3D%3D&icon=.csv"

def now():
    return datetime.now(ZoneInfo("Asia/Taipei")).isoformat(timespec="seconds")

def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2)+"\n", encoding="utf-8")
    tmp.replace(path)

class Downloads:
    def __init__(self, offline=False, refresh=False):
        self.offline = offline
        self.refresh = refresh
        self.path = RAW / "manifest.json"
        self.entries = json.loads(self.path.read_text()) if self.path.exists() else {}

    def fetch(self, url, filename, page_url=None):
        if urlparse(url).scheme != "https":
            raise ValueError("Only HTTPS downloads are supported")
        path = RAW / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        entry = self.entries.get(filename)
        if not self.refresh and entry and entry.get("download_url") == url and path.exists() and path.stat().st_size > 0:
            return path
        if self.offline:
            raise ValueError(f"Offline raw file absent or empty: {filename}")
        part = path.with_suffix(path.suffix + ".part")
        result = subprocess.run(["curl", "--fail", "--location", "--silent", "--show-error", "--proto", "=https", "--proto-redir", "=https", "--connect-timeout", "20", "--max-time", "180", "--retry", "2", "--retry-delay", "2", "--user-agent", "bus-analysis/1.0", "--output", str(part), "--write-out", "%{json}", url], capture_output=True, text=True, timeout=600)
        if result.returncode:
            part.unlink(missing_ok=True)
            raise RuntimeError(f"{filename}: {result.stderr.strip()}")
        info = json.loads(result.stdout)
        if not part.stat().st_size:
            raise ValueError(f"Empty download: {url}")
        part.replace(path)
        self.entries[filename] = {"source_page": page_url or url, "download_url": url, "resolved_url": info["url_effective"], "raw_file": str(path.relative_to(ROOT)), "downloaded_at": now(), "source_updated_at": None, "bytes": path.stat().st_size}
        write_json(self.path, self.entries)
        print(f"downloaded {filename}: {path.stat().st_size:,} bytes", flush=True)
        return path

    def source(self, filename):
        return dict(self.entries[filename])

def soup(path):
    return BeautifulSoup(path.read_bytes(), "html.parser")

def discover(d, keys=None):
    for key in keys or PAGES:
        url=PAGES[key]
        path = d.fetch(url, key + "_page.html")
        doc = soup(path)
        links = []
        for a in doc.select("a[href]"):
            href = a["href"]
            text = a.get_text(" ", strip=True)
            if (key.startswith("taipei") and text == "下載") or (key == "national_highway_monthly" and "CSV" in text) or (key == "vehicle_cost" and "壓縮檔" in text) or (key == "mode_use_survey_2024" and "/ch/app/data/doc?" in href):
                links.append({"text": text, "url": urljoin(url, href), "attributes": dict(a.attrs)})
        write_json(RAW / (key + "_links.json"), links)
        print(f"discovered {key}: {len(links)} resources", flush=True)

def download_resources(d, keys=None):
    for key in keys or ("taipei_annual", "taipei_monthly", "national_highway_monthly", "vehicle_cost", "mode_use_survey_2024"):
        links = json.loads((RAW / (key + "_links.json")).read_text())
        for item in links:
            if key == "mode_use_survey_2024":
                title = item["attributes"]["title"]
                ext = re.search(r"\((ods|pdf|xlsx)\)", title).group(1)
                if ext == "ods":
                    # Same statistical workbook is retained in XLSX; no duplicate ODS.
                    continue
                stem = "mode_use_survey_2024_questionnaire" if "調查表(" in title else key
            else:
                ext = "zip" if key == "vehicle_cost" else "csv"
                stem = key
            url = NATIONAL_CSV if key == "national_highway_monthly" else item["url"]
            d.fetch(url, f"{stem}.{ext}", PAGES[key])

def number(value):
    if value is None:
        return None
    s = str(value).strip().replace(",", "")
    if s in MISSING or s.startswith("#"):
        return None
    n = Decimal(s)
    if not n.is_finite():
        raise ValueError(f"Nonfinite numeric value: {value}")
    return int(n) if n == n.to_integral_value() else float(n)

def envelope(dataset_id, records, sources, **metadata):
    return {"schema_version": 1, "dataset_id": dataset_id,
            "metadata": {"sources": sources, "record_count": len(records), **metadata}, "records": records}

def csv_rows(path):
    b = path.read_bytes()
    if b.lstrip().lower().startswith((b"<html", b"<!doctype")):
        raise ValueError(f"HTML response is not a CSV dataset: {path}")
    for encoding in ("utf-8-sig", "cp950"):
        try:
            return list(csv.reader(io.StringIO(b.decode(encoding)))), encoding
        except UnicodeDecodeError:
            pass
    raise ValueError(f"Unsupported CSV encoding: {path}")

SERIES_FIELDS = {
    "taipei_annual": [
        ("joint_route_count", "線"), ("reciprocal_route_count", "線"), ("minibus_route_count", "線"),
        ("operating_route_length_km", "公里"), ("average_operated_vehicle_days", "平均行駛延日車數"),
        ("operated_sections", "段次"), ("sections_per_vehicle_day", "段次/車日"),
        ("distance_km", "車公里"), ("distance_per_vehicle_day_km", "公里/車日"),
        ("distance_per_vehicle_section_km", "公里/車段次"), ("passenger_trips", "人次"),
        ("passenger_trips_per_day", "人次/日"), ("passenger_trips_per_vehicle_day", "人次/車日"),
        ("passenger_trips_per_vehicle_section", "人次/車段次"), ("revenue_twd", "元"),
        ("smart_stop_sign_count", "座"), ("accessible_minibus_count", "輛")],
    "taipei_monthly": [
        ("joint_route_count", "線"), ("minibus_route_count", "線"),
        ("average_operated_vehicle_days", "平均行駛延日車數"), ("trips_per_vehicle_day", "次/車日"),
        ("distance_per_vehicle_day_km", "公里/車日"), ("passenger_trips_per_day", "人次/日"),
        ("passenger_trips_per_vehicle_trip", "人次/車次"), ("revenue_thousand_twd", "千元")],
    "national_highway_monthly": [
        ("operator_count", "家"), ("route_count", "條"), ("operating_route_length_km", "公里"),
        ("operated_trips", "班次"), ("distance_km", "車公里"), ("passenger_trips", "人次"),
        ("electronic_ticket_passengers", "人"), ("passenger_km", "人公里"), ("revenue_twd", "元"),
        ("electronic_ticket_revenue_twd", "元"), ("seat_km", "座公里"), ("average_passenger_distance_km", "公里")],
}
SERIES_HEADERS = {
    "taipei_annual": ["統計期", "路線數/聯營[線]", "路線數/對開[線]", "路線數/小型[線]", "營業公里[公里]", "行駛車輛數[平均行駛延日車數]", "行駛次數[段次]", "平均每車每日行駛次數[段次]", "行駛車公里[公里]", "平均每車每日行駛車公里[公里]", "平均每車每次行駛車公里[公里]", "客運人次[人次]", "平均每日客運人次[人次]", "平均每車每日客運人次[人次]", "平均每車每次客運人次[人次]", "客運收入[元]", "智慧型公車站牌數[座]", "身心障礙者小型冷氣車輛數[輛]"],
    "taipei_monthly": ["統計期", "路線數/聯營[線]", "路線數/小型[線]", "行駛車輛數[平均行駛延日車數]", "每車每日平均行駛次數[次]", "每車每日平均行駛公里[公里]", "平均每日客運人次[人次]", "平均每車車次客運人次[人次]", "客運收入[千元]"],
}

def normalize_series(d, key, taipei_recent=False):
    filename = key + ".csv"
    rows, encoding = csv_rows(RAW / filename)
    header = rows[0]
    fields = SERIES_FIELDS[key]
    if len(header) != len(fields) + 1 or not (header[0] == "統計期" or header[0] == "期別/一般公路"):
        raise ValueError(f"Unexpected {key} columns: {header}")
    if key in SERIES_HEADERS and header!=SERIES_HEADERS[key]:
        raise ValueError(f"Source columns changed; review the mapping before updating {key}")
    records = []
    for line, row in enumerate(rows[1:], 2):
        if not any(x.strip() for x in row):
            continue
        if len(row) != len(header):
            raise ValueError(f"Unexpected CSV width: {key} line {line}")
        m = re.fullmatch(r"(\d+)年(?:\s*(\d+)月)?", row[0].strip())
        if not m:
            raise ValueError(f"Unexpected period: {row[0]}")
        year = int(m[1]) + 1911
        month = int(m[2]) if m[2] else None
        period = str(year) if month is None else f"{year}-{month:02}"
        r = {"period": period, "year": year, "month": month, "source_period": row[0], "source_row": line, "missing_values": {}}
        for (field, unit), value in zip(fields, row[1:]):
            r[field] = number(value)
            if r[field] is None:
                r["missing_values"][field] = value
        records.append(r)
    if len(records) != len({r["period"] for r in records}):
        raise ValueError(f"Duplicate periods in {key}")
    records.sort(key=lambda r: r["period"])
    source_record_count=len(records)
    source_period={"start":records[0]["period"],"end":records[-1]["period"]}
    if taipei_recent:
        records=[r for r in records if (2023<=r["year"]<=2025 if key=="taipei_annual" else "2023-10"<=r["period"]<="2026-09")]
    limitations = ["全市或全國彙總資料，不能直接分配至個別公車路線；原始明確數值0保留，空白及缺值符號轉null。"]
    if key == "national_highway_monthly":
        limitations += ["統計範圍為全國一般公路汽車客運，不能代替臺北市市區公車統計。", "電子票證客運人數的原始單位是人，未據此推論不重複乘客。", "官方入口曾回傳防護HTML，改用入口重新導向的ws.thb.gov.tw原始附件。"]
    else:
        limitations += ["年度來源行駛次數單位為段次，月度來源每車每日次數單位為次；兩者未視為相同指標。", "歷年路線定義或統計範圍可能改變，未以時間序列變化推斷服務變化原因。"]
    metadata = dict(title=soup(RAW / (key + "_page.html")).find("h2").get_text(" ", strip=True),
        source_updated_at=None, period={"start": records[0]["period"], "end": records[-1]["period"]},
        grain="calendar_year" if key.endswith("annual") else "calendar_month", geographic_scope="Taiwan" if key.startswith("national") else "Taipei City",
        encoding=encoding, fields=[{"field": name, "source_column": header[i+1], "unit": unit} for i,(name,unit) in enumerate(fields)], limitations=limitations)
    if taipei_recent:
        metadata.update({"source_record_count":source_record_count,"source_period":source_period,
            "requested_period":{"start":"2023-10","end":"2026-09"},
            "selection_rule":"2023–2025完整年度（未把年度拆成月）" if key=="taipei_annual" else "2023-10至2026-09內的來源可得月份",
            "missing_requested_months":None if key=="taipei_annual" else [f"{m//12}-{m%12+1:02}" for m in range(2023*12+9,2026*12+9) if f"{m//12}-{m%12+1:02}" not in {r["period"] for r in records}]})
        limitations.append("依本次使用者範圍保留臺北近三年；來源完整時間序列僅存raw_data。")
        limitations.append("年度資料保留2023–2025完整年，2023全年不可當作2023/10以後的三個月；2026全年尚未發布。" if key=="taipei_annual" else "來源最新至2026/07；2026/08–09未取得，不補零。")
    if key.startswith("national"):
        metadata["title"] = "一般公路汽車客運概況"
    data = envelope(key, records, [d.source(filename)], **metadata)
    return data

XNS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}

def column_number(col):
    n = 0
    for ch in col:
        n = n*26 + ord(ch)-64
    return n

def cell_position(ref):
    m = re.fullmatch(r"([A-Z]+)(\d+)", ref)
    return int(m[2]), column_number(m[1])

def column_letter(n):
    s = ""
    while n:
        n,rem = divmod(n-1,26)
        s = chr(rem+65)+s
    return s

def clean_label(value):
    return re.sub(r"\s+", "", str(value or ""))

def normalize_survey(d, taipei_only=False):
    tables, records, cover_notes, header_evidence = [], [], [], []
    with zipfile.ZipFile(RAW / "mode_use_survey_2024.xlsx") as z:
        ss = ["".join(si.itertext()) for si in ET.fromstring(z.read("xl/sharedStrings.xml"))]
        rels = {r.attrib["Id"]: r.attrib["Target"] for r in ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))}
        sheets = ET.fromstring(z.read("xl/workbook.xml")).find("m:sheets", XNS)
        for sheet in sheets:
            sheet_name = sheet.attrib["name"]
            rid = sheet.attrib["{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"]
            target = rels[rid].lstrip("/")
            if not target.startswith("xl/"):
                target = "xl/" + target
            xml = ET.fromstring(z.read(target))
            cells = {}
            for c in xml.findall(".//m:sheetData/m:row/m:c", XNS):
                val = c.find("m:v", XNS)
                value = val.text if val is not None else None
                if c.attrib.get("t") == "s" and value is not None:
                    value = ss[int(value)]
                elif c.attrib.get("t") == "inlineStr":
                    value = "".join(c.find("m:is",XNS).itertext())
                elif value is not None and c.attrib.get("t") not in ("str", "e"):
                    value = number(value)
                if value is not None:
                    cells[cell_position(c.attrib["r"])] = value
            if sheet_name == "封面_公布版":
                cover_notes = [str(v) for (r,c),v in cells.items() if r>=9]
            if not re.fullmatch(r"[A-E]\d{2}", sheet_name):
                continue
            table_id = sheet_name.lower()
            headers = {pos:v for pos,v in cells.items() if 5<=pos[0]<=8}
            merged = []
            for m in xml.findall(".//m:mergeCells/m:mergeCell", XNS):
                a,b = m.attrib["ref"].split(":")
                r1,c1=cell_position(a);r2,c2=cell_position(b)
                if 5<=r1<=8:
                    merged.append(m.attrib["ref"])
                    value = cells.get((r1,c1))
                    # Excel ignores non-anchor content within merged cells.
                    for r in range(r1,min(r2,8)+1):
                        for c in range(c1,c2+1):
                            headers[(r,c)] = value
            data_cols = sorted({c for (r,c),v in cells.items() if r>=9 and c>=4 and isinstance(v,(int,float))})
            unit_labels = list(dict.fromkeys(str(v).strip() for (r,c),v in cells.items() if r==4 and "單位" in str(v)))
            source_units = unit_labels[0].replace("單位：", "").split("；") if unit_labels else []
            definitions = []
            for c in data_cols:
                label_path=[]
                for r in range(5,9):
                    label=clean_label(headers.get((r,c)))
                    if label and label not in label_path:
                        label_path.append(label)
                if not label_path:
                    raise ValueError(f"Unmapped survey numeric column {sheet_name}:{c}")
                if "樣本數" in label_path:
                    unit = source_units[0]
                elif "平均數" in label_path:
                    unit = "分鐘" if sheet_name in ("E22","E23") else "種類/人" if sheet_name=="E20" else "次/人"
                else:
                    unit = "%"
                definitions.append({"field": "col_"+column_letter(c).lower(), "source_column": column_letter(c), "label_path": label_path, "unit": unit})
            title_parts = list(dict.fromkeys(str(v).strip() for (r,c),v in cells.items() if r==1 and str(v).strip()))
            title = " ".join(title_parts)
            grain_parts = list(dict.fromkeys(str(v).strip() for (r,c),v in cells.items() if r==3 and str(v).strip()))
            table = {"table_id": table_id, "sheet_name": sheet_name, "title": title,
                     "population_and_grain_source": grain_parts, "source_units": unit_labels, "fields":definitions,
                     "merged_header_ranges": merged, "record_count":0}
            group,significance = None,None
            for r in sorted({r for r,c in cells if r>=9}):
                a,b=cells.get((r,1)),cells.get((r,2))
                if a and a != "全體" and not any(isinstance(cells.get((r,c)),(int,float)) for c in data_cols):
                    group=clean_label(a);significance=cells.get((r,3));continue
                label = b if b is not None else a
                if not label:
                    continue
                values = {de["field"]:number(cells.get((r,c))) for de,c in zip(definitions,data_cols)}
                if not any(v is not None for v in values.values()):
                    continue
                historical = re.fullmatch(r"(\d+)年度",clean_label(label)) if group=="年度別" else None
                year = int(historical[1])+1911 if historical else 2024
                missing = {de["field"]:cells.get((r,c)) for de,c in zip(definitions,data_cols) if values[de["field"]] is None}
                records.append({"record_id":f"{table_id}:{r}","table_id":table_id,"source_row":r,"year":year,
                    "row_kind":"historical_comparison" if historical else "overall" if a=="全體" else "subgroup",
                    "category_dimension": "全體" if a=="全體" else group,
                    "category_label":clean_label(label),"group_significance":significance if isinstance(significance,str) else None,
                    "values":values,"missing_values":missing})
                table["record_count"] += 1
            header_evidence.append({"sheet":sheet_name,"header_cells":[{"row":r,"column":column_letter(c),"value":v} for (r,c),v in cells.items() if r<=8],"merged_header_ranges":merged})
            tables.append(table)
    if len(tables)!=23:
        raise ValueError(f"Expected 23 statistical survey tables, got {len(tables)}")
    write_json(RAW / "survey_header_extraction.json",header_evidence)
    source_record_count=len(records)
    dataset_id="mode_use_survey_2024"
    if taipei_only:
        selected=[]
        for table in tables:
            taipei_fields=[f for f in table["fields"] if "臺北市" in f["label_path"]]
            if taipei_fields:
                table["fields"]=taipei_fields
                table["selection_rule"]="縣市為欄的母表只取臺北市欄；保留2024分類列，排除年度比較。"
                fields={f["field"] for f in taipei_fields}
                rows=[r for r in records if r["table_id"]==table["table_id"] and r["year"]==2024 and r["row_kind"]!="historical_comparison"]
                for r in rows:
                    r["values"]={k:v for k,v in r["values"].items() if k in fields}
                    r["missing_values"]={k:v for k,v in r["missing_values"].items() if k in fields}
            else:
                table["selection_rule"]="只取2024居住縣市為臺北市的資料列；其餘全國/其他縣市只保留原始母表。"
                rows=[r for r in records if r["table_id"]==table["table_id"] and r["year"]==2024 and r["category_dimension"]=="居住縣市" and r["category_label"]=="臺北市"]
            table["record_count"]=len(rows)
            selected.extend(rows)
        records=selected
        dataset_id="taipei_mode_survey_2024"
    data = envelope(dataset_id,records,[d.source("mode_use_survey_2024.xlsx"),d.source("mode_use_survey_2024.pdf"),d.source("mode_use_survey_2024_questionnaire.pdf")],
        title="113年民眾日常使用運具狀況調查統計表",source_updated_at="2025-06-30",survey_year=2024,
        period={"main_survey_year":2024,"historical_comparison_years":sorted({r["year"] for r in records if r["row_kind"]=="historical_comparison"})},
        grain="table × category_dimension × category_label × year; values fields use each table's dictionary",tables=tables,methodology_notes=cover_notes,
        limitations=["平日市話及手機抽樣調查彙總，並非逐站刷卡、乘客OD或公車轉乘實績。",
            "各表分別以人、旅次、運具次數或旅次主運具為分母；使用table_id及欄位字典區分，不合併分母。",
            "樣本數保留XLSX中的加權小數，並非未加權受訪人數；數值使用儲存的計算結果，不自行重算公式。",
            "每表年度別行為2009–2024的非連續比較年份，record.year逐行設定，不以2024覆蓋歷史。",
            "XLSX部分合併儲存格保留非左上角舊文字；依合併範圍只採左上角顯示值。附表11 PDF第28頁已核對汽車客運欄名。",
            "原始數字0保留為0（PDF可能以破折號顯示零），原始空白與文字缺值標記轉null。"])
    if taipei_only:
        data["metadata"].update({"geographic_scope":"Taipei City residents","source_record_count":source_record_count,
            "period":{"start":"2024","end":"2024"},"title":"113年民眾日常使用運具狀況調查：臺北市相關統計",
            "source_scope":"全國母表留raw；data只留臺北市居住地列或臺北市欄，未將國家總體列當作臺北市。"})
        data["metadata"]["limitations"][3]="原始母表另含2009–2024比較年份；本次data只取2024資料，其他年度僅保留在raw來源。"
    return data

def validate_outputs(outputs):
    report=[]
    for data in outputs:
        records=data["records"]
        key=data["dataset_id"]
        report.append({"dataset_id":key,"record_count":len(records),"empty":not bool(records),"source_count":len(data["metadata"]["sources"]),"period":data["metadata"].get("period")})
        if not records:raise ValueError(f"Empty normalized dataset {key}")
        for source in data["metadata"]["sources"]:
            if not (ROOT/source["raw_file"]).is_file() or (ROOT/source["raw_file"]).stat().st_size == 0:raise ValueError(f"Source absent or empty: {source['raw_file']}")
        if key in SERIES_FIELDS:
            dates=[int(r["year"])*12+(r["month"] or 1) for r in records]
            interval=12 if key.endswith("annual") else 1
            gaps=[(records[i-1]["period"],records[i]["period"]) for i in range(1,len(dates)) if dates[i]-dates[i-1]!=interval]
            report[-1]["period_gaps"]=gaps
            report[-1]["missing_numeric_values"]=sum(len(r["missing_values"]) for r in records)
        elif key=="taipei_mode_survey_2024":
            if len({r["record_id"] for r in records})!=len(records):
                raise ValueError("Duplicate survey table/row keys")
            tables={t["table_id"]:t for t in data["metadata"]["tables"]}
            for r in records:
                table=tables[r["table_id"]]
                fields={f["field"]:f for f in table["fields"]}
                if r["year"]!=2024 or r["row_kind"]=="historical_comparison":
                    raise ValueError("Out-of-scope survey year")
                if set(r["values"])!=set(fields):
                    raise ValueError("Survey values do not match their field dictionary")
                if not (r["category_dimension"]=="居住縣市" and r["category_label"]=="臺北市") and not all("臺北市" in f["label_path"] for f in fields.values()):
                    raise ValueError("Survey record is not Taipei scoped")
                for name,value in r["values"].items():
                    if value is not None and (value<0 or fields[name]["unit"]=="%" and value>100.000001):
                        raise ValueError("Invalid survey measure range")
            report[-1].update({"table_count":len(tables),"duplicate_keys":0,"out_of_scope_records":0,"invalid_numeric_ranges":0})
    write_json(RAW/"quality_checks.json",{"generated_at":now(),"checks":report})
    return report

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--offline", action="store_true")
    p.add_argument("--refresh", action="store_true",help="Re-download the three active Taipei source groups")
    p.add_argument("--discover", action="store_true")
    p.add_argument("--download", action="store_true")
    args = p.parse_args()
    if args.offline and args.refresh:
        p.error("--offline and --refresh are mutually exclusive")
    d = Downloads(args.offline,args.refresh)
    active_keys=("taipei_annual","taipei_monthly","mode_use_survey_2024")
    discover(d,active_keys)
    if args.discover and not args.download:
        return
    download_resources(d,active_keys)
    outputs=[normalize_series(d,key,taipei_recent=True) for key in ("taipei_annual","taipei_monthly")]
    outputs += [normalize_survey(d,taipei_only=True)]
    report=validate_outputs(outputs)
    # Leave all previous structured outputs in place if any source fails to parse.
    for data in outputs:
        write_json(OUT/(data["dataset_id"]+".json"),data)
    delivery=[]
    for data in outputs:
        key=data["dataset_id"]
        delivery.append({"dataset_id":key,"status":"normalized",
            "output_files":[f"data/background/{key}.json"],"record_count":len(data["records"]),"period":data["metadata"].get("period"),
            "limitations":data["metadata"]["limitations"],"independent_review_status":"pending"})
    for key in ("national_highway_monthly","vehicle_cost","operator_violations"):
        delivery.append({"dataset_id":key,"status":"deferred_by_user_scope","output_files":[],"record_count":None,"period":None,
            "reference_url":PAGES[key],"reference_file":"raw_data/background/references.json",
            "limitations":["使用者要求先專注臺北市近三年，因此非臺北或較舊背景資料暫不列入active data。僅保留raw_data/background/references.json的官方來源連結與必要期間說明。"],"independent_review_status":"not_applicable_deferred"})
    write_json(RAW/"delivery.json",{"generated_at":now(),"datasets":delivery})
    print(json.dumps(report,ensure_ascii=False,indent=2))

if __name__ == "__main__":
    main()
