#!/usr/bin/env python3
"""Download Taipei open data and build JSON/GeoJSON. No API keys required."""

import argparse
import base64
import csv
from datetime import datetime
from decimal import Decimal
import fcntl
import gzip
import hashlib
import io
import json
import math
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
from urllib.parse import parse_qs, urljoin, urlparse
import zipfile
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup
import pdfplumber
from pyproj import CRS, Transformer
import shapefile
from shapely import from_wkt
from shapely.geometry import mapping

ROOT = Path(__file__).resolve().parents[1]
TAIPEI = ZoneInfo("Asia/Taipei")
DATASETS = {
    "bus_routes": "efad90af-4bbe-4c6a-ac60-06f3e52d8a97",
    "bus_stops": "48aa5bca-2a4f-4fb7-a658-43cba51d5d56",
    "bus_arrivals": "f11a5af0-7b37-48ef-98cc-f6f102ed43c6",
}
PTO_URL = "https://pto.gov.taipei/RelData.aspx?ParentSN=5D189624CC86562D&sms=2A073ABACACC5A2A"
ALLOWED_HOSTS = {"data.taipei", "tcgbusfs.blob.core.windows.net", "pto.gov.taipei", "www.pto.gov.taipei", "www-ws.gov.taipei"}
MISSING = {"", "-", "－", "—", "…", "...", "--", "N/A"}
NUMBER = r"(?:ⓡ\s*)?(?:\d[\d,]*(?:\.\d+)?|[-－—…]|\.\.\.)"
ETA_STATUS = {-1: "not_departed", -2: "not_stopping", -3: "service_ended", -4: "not_operating_today"}
GO_BACK = {0: "outbound", 1: "inbound", 2: "not_departed", 3: "last_bus_departed"}
ROUTE_FIELDS = [
    "operated_sections", "distance_km", "passenger_trips",
    "passengers_per_section", "revenue_twd", "revenue_per_km_twd",
]
OPERATOR_FIELDS = [
    "approved_routes", "operating_route_length_km", "approved_vehicles",
    "allocated_vehicle_days", "operated_vehicle_days", "operated_trips",
    "operated_sections", "distance_km", "passenger_trips", "passenger_km",
    "revenue_twd", "diesel_liters", "revenue_per_km_twd",
]


def now():
    return datetime.now(TAIPEI).isoformat(timespec="seconds")


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path, obj, pretty=True):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, allow_nan=False,
                  indent=2 if pretty else None, separators=None if pretty else (",", ":"))
        f.write("\n")


def number(value):
    value = value.replace("ⓡ", "").strip().replace(",", "")
    if value in MISSING:
        return None
    n = Decimal(value)
    if not n.is_finite():
        raise ValueError(f"Non-finite number: {value}")
    return int(n) if n == n.to_integral_value() else float(n)


def local_time(value):
    if not value:
        return None
    d = datetime.fromisoformat(value.replace("/", "-").replace("Z", "+00:00"))
    return (d.replace(tzinfo=TAIPEI) if d.tzinfo is None else d).isoformat()


class Downloads:
    """Conditional, TLS-verified downloads; offline replay verifies saved hashes."""

    def __init__(self, raw_dir, offline=False):
        self.root = raw_dir
        self.offline = offline
        self.path = raw_dir / "taipei_bus_manifest.json"
        self.entries = json.loads(self.path.read_text()) if self.path.exists() else {}
        self.used = set()

    def fetch(self, url, relative):
        parsed = urlparse(url)
        if parsed.scheme != "https" or parsed.hostname not in ALLOWED_HOSTS:
            raise ValueError(f"Unexpected source URL: {url}")
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        old = self.entries.get(relative, {})
        valid_cache = (old.get("url") == url and path.exists()
                       and old.get("sha256") == sha256(path))
        if self.offline:
            if not valid_cache:
                raise ValueError(f"Missing or changed offline source: {relative}")
            self.used.add(relative)
            return path
        temporary = path.with_name(path.name + ".part")
        headers = path.with_name(path.name + ".headers")
        cmd = ["curl", "--fail", "--location", "--silent", "--show-error",
               "--proto", "=https", "--proto-redir", "=https",
               "--connect-timeout", "20", "--max-time", "120",
               "--retry", "3", "--retry-delay", "2", "--retry-max-time", "240",
               "--user-agent", "bus-analysis/1.0", "--dump-header", str(headers),
               "--output", str(temporary), "--write-out", "%{json}"]
        if valid_cache:
            if old.get("etag"):
                cmd += ["--header", "If-None-Match: " + old["etag"]]
            elif old.get("last_modified"):
                cmd += ["--header", "If-Modified-Since: " + old["last_modified"]]
        try:
            result = subprocess.run(cmd + [url], capture_output=True, text=True, timeout=300)
            if result.returncode:
                raise RuntimeError(f"Download failed for {relative}: {result.stderr.strip()}")
            info = json.loads(result.stdout)
            status = info["http_code"]
            if urlparse(info["url_effective"]).hostname not in ALLOWED_HOSTS:
                raise ValueError(f"Unexpected redirect for {relative}")
            if status == 304 and valid_cache:
                entry = {**old, "last_checked_at": now()}
            elif status == 200 and temporary.exists() and temporary.stat().st_size:
                response_headers = {}
                for line in headers.read_text(errors="replace").splitlines():
                    if line.startswith("HTTP/"):
                        response_headers = {}
                    elif ":" in line:
                        k, v = line.split(":", 1)
                        response_headers[k.lower()] = v.strip()
                temporary.replace(path)
                entry = {
                    "url": url, "resolved_url": info["url_effective"],
                    "raw_file": "raw_data/" + relative, "downloaded_at": now(),
                    "last_checked_at": now(), "sha256": sha256(path),
                    "bytes": path.stat().st_size,
                    "etag": response_headers.get("etag"),
                    "last_modified": response_headers.get("last-modified"),
                }
            else:
                raise RuntimeError(f"Unexpected HTTP {status} for {relative}")
            self.entries[relative] = entry
            self.used.add(relative)
            print(f"  {status} {relative}", flush=True)
            return path
        finally:
            temporary.unlink(missing_ok=True)
            headers.unlink(missing_ok=True)

    def source(self, relative, page_url):
        return {"page_url": page_url, **self.entries[relative]}

    def save(self):
        write_json(self.path, self.entries)


def discover_dataset(path, page_url):
    soup = BeautifulSoup(path.read_text(encoding="utf-8"), "html.parser")
    resources = []
    for a in soup.select("a[href]"):
        if a.get_text(strip=True) != "下載":
            continue
        row = a.find_parent("tr")
        cells = row.find_all("td") if row else []
        if len(cells) < 4:
            raise ValueError("Dataset download table changed")
        resources.append({"title": cells[0].get_text(strip=True),
                          "format": cells[2].get_text(strip=True).upper(),
                          "published_at": cells[3].get_text(strip=True),
                          "url": urljoin(page_url, a["href"])})
    if not resources:
        raise ValueError(f"No dataset resources found: {page_url}")
    return resources


def feed_url(path):
    with path.open(encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    if len(rows) != 1 or not rows[0].get("介接網址"):
        raise ValueError(f"Unexpected feed descriptor: {path.name}")
    return rows[0]["介接網址"].strip(), rows[0]


def read_feed(path):
    data = path.read_bytes()
    if data[:2] == b"\x1f\x8b":
        data = gzip.decompress(data)
    return json.loads(data.decode("utf-8-sig"))


def check_unique(values, label):
    if len(values) != len(set(values)):
        raise ValueError(f"Duplicate {label}")


def taiwan_bounds(bounds):
    return (all(math.isfinite(v) for v in bounds)
            and 119 <= bounds[0] <= bounds[2] <= 123
            and 21 <= bounds[1] <= bounds[3] <= 27)


def routes_geojson(rows):
    if not isinstance(rows, list) or not rows:
        raise ValueError("Empty or unexpected route feed")
    features = []
    for r in rows:
        geometry = from_wkt(r["wkt"])
        if (geometry.geom_type not in {"LineString", "MultiLineString"}
                or geometry.is_empty or not geometry.is_valid or not taiwan_bounds(geometry.bounds)):
            raise ValueError(f"Invalid route geometry: {r['RouteID']}")
        props = {"route_id": str(r["RouteID"]), "subroute_id": str(r["SubRouteID"]),
                 "uni_route_id": str(r["UniRouteId"]), "go_back": int(r["GoBack"]),
                 "source_updated_at": local_time(r["utime"])}
        key = ":".join(str(props[k]) for k in ["route_id", "subroute_id", "uni_route_id", "go_back"])
        features.append({"type": "Feature", "id": key, "properties": props,
                         "geometry": mapping(geometry)})
    check_unique([f["id"] for f in features], "route geometry keys")
    return {"type": "FeatureCollection", "features": features}


def resolve_stop_crs(bounds, prj=None):
    if prj:
        return CRS.from_wkt(prj), "shapefile_prj", []
    if taiwan_bounds(bounds):
        return CRS.from_epsg(4326), "inferred_from_coordinate_range", [
            "來源頁標示 TWD97，但 ZIP 無 .prj，座標已落於臺灣經緯度範圍；推定 EPSG:4326，未再次投影。"]
    if 100000 < bounds[0] <= bounds[2] < 400000 and 2400000 < bounds[1] <= bounds[3] < 2900000:
        return CRS.from_epsg(3826), "page_declaration_and_coordinate_range", [
            "ZIP 無 .prj；依來源頁 TWD97 與座標範圍推定 EPSG:3826。"]
    raise ValueError("Unrecognized stop CRS; refusing to guess")


def stops_geojson(path):
    with zipfile.ZipFile(path) as archive:
        shapes = [n for n in archive.namelist() if n.lower().endswith(".shp")]
        if len(shapes) != 1:
            raise ValueError("Expected exactly one shapefile")
        stem = shapes[0][:-4]
        members = {Path(n).suffix.lower(): n for n in archive.namelist() if n.startswith(stem + ".")}
        encoding = archive.read(members[".cpg"]).decode().strip() if ".cpg" in members else "utf-8"
        prj = archive.read(members[".prj"]).decode() if ".prj" in members else None
        with shapefile.Reader(shp=io.BytesIO(archive.read(members[".shp"])),
                              shx=io.BytesIO(archive.read(members[".shx"])),
                              dbf=io.BytesIO(archive.read(members[".dbf"])), encoding=encoding) as reader:
            crs, basis, warnings = resolve_stop_crs(reader.bbox, prj)
            transform = Transformer.from_crs(crs, 4326, always_xy=True)
            features = []
            for item in reader.iterShapeRecords():
                if item.shape.shapeType != shapefile.POINT:
                    raise ValueError("Expected point stop geometry")
                lon, lat = transform.transform(*item.shape.points[0])
                if not taiwan_bounds((lon, lat, lon, lat)):
                    raise ValueError("Invalid stop coordinates")
                r = item.record.as_dict()
                features.append({"type": "Feature", "id": str(r["BSM_BUSSTO"]),
                                 "properties": {"stop_location_id": str(r["BSM_BUSSTO"]), "name": r["BSM_CHINES"]},
                                 "geometry": {"type": "Point", "coordinates": [lon, lat]}})
    if not features:
        raise ValueError("Empty stops shapefile")
    check_unique([f["id"] for f in features], "stop location IDs")
    return {"type": "FeatureCollection", "features": features}, {
        "declared_crs": "TWD97 (source page)", "interpreted_crs": crs.to_string(),
        "crs_basis": basis, "output_crs": "EPSG:4326", "warnings": warnings,
        "id_note": "BSM_BUSSTO 為站位 ID，未證實等同到站 API 的 StopID；不可直接連接。"}


def arrivals_json(feed):
    rows = feed["BusInfo"]
    if not rows:
        raise ValueError("Empty arrival feed")
    records = []
    for r in rows:
        eta = int(r["EstimateTime"])
        go_back = int(r["GoBack"])
        records.append({"route_id": str(r["RouteID"]), "stop_id": str(r["StopID"]),
                        "go_back": go_back, "go_back_status": GO_BACK.get(go_back, "unknown"),
                        "estimate_time_raw": r["EstimateTime"],
                        "estimate_seconds": eta if eta >= 0 else None,
                        "status": "estimated" if eta >= 0 else ETA_STATUS.get(eta, "unknown")})
    check_unique([(r["route_id"], r["stop_id"], r["go_back"]) for r in records], "arrival keys")
    return records, local_time(feed["EssentialInfo"]["UpdateTime"])


def pdf_links(path):
    soup = BeautifulSoup(path.read_text(encoding="utf-8"), "html.parser")
    links = []
    for a in soup.select("a[data-ccms_hitcount_relfile][href]"):
        name = base64.b64decode(parse_qs(urlparse(a["href"]).query)["n"][0]).decode("utf-8")
        if not name.lower().endswith(".pdf"):
            raise ValueError(f"Unsupported new attachment: {name}")
        if Path(name).name != name or "\\" in name:
            raise ValueError("Unsafe attachment filename")
        links.append({"id": a["data-ccms_hitcount_relfile"], "filename": name,
                      "title": name[:-4], "url": a["href"]})
    if not links:
        raise ValueError("No PTO PDF attachments found; source layout may have changed")
    check_unique([x["filename"] for x in links], "PDF filenames")
    return links


def period_from_text(text):
    # The reporting period is the first standalone ROC date, not the revision/footer date.
    m = re.search(r"中華民國\s*(\d{2,3})\s*年(?:\s*(\d{1,2})\s*月)?", text)
    if not m:
        raise ValueError("No reporting period in PDF")
    year = int(m[1]) + 1911
    month = int(m[2]) if m[2] else None
    if month is not None and not 1 <= month <= 12:
        raise ValueError("Invalid reporting month")
    return {"roc_year": int(m[1]), "year": year, "month": month,
            "period": f"{year}-{month:02}" if month else str(year)}


def numeric_rows(text, count=None):
    """Extract numeric tails; retain labels and exact tokens for auditability."""
    suffix = "{" + str(count - 1) + "}" if count else "+"
    pattern = re.compile(r"^(.*?)\s+(" + NUMBER + r"(?:\s+" + NUMBER + r")" + suffix + r")$")
    rows = []
    for line_no, line in enumerate(text.splitlines(), 1):
        m = pattern.fullmatch(line)
        if m:
            tokens = re.findall(NUMBER, m[2])
            rows.append({"line": line_no, "label": m[1], "values": [number(v) for v in tokens], "tokens": tokens})
    return rows


def typed_pdf_rows(document, pages, fields, label_key):
    records = []
    for page_no, text in enumerate(pages, 1):
        # Exclude notes and approval blocks, which can also contain numeric suffixes.
        body = re.split(r"備註[：:]|填表\s+審核", text)[0]
        for r in numeric_rows(body, len(fields)):
            label = re.sub(r"\s+|[①②③④]", "", r["label"])
            records.append({"document_id": document["id"], "period": document["period"]["period"],
                            label_key: label, "is_total": label == "總計",
                            **dict(zip(fields, r["values"])),
                            "revised_fields": [field for field, token in zip(fields, r["tokens"]) if "ⓡ" in token],
                            "source_page": page_no, "source_line": r["line"]})
    if not records or sum(r["is_total"] for r in records) != 1:
        raise ValueError(f"Unrecognized statistics layout: {document['title']}")
    check_unique([r[label_key] for r in records], f"{label_key} in {document['title']}")
    return records


def reconcile_totals(records, fields, allow_difference=()):
    total = next(r for r in records if r["is_total"])
    detail = [r for r in records if not r["is_total"]]
    checks = {}
    for field in fields:
        if any(r[field] is None for r in records):
            raise ValueError(f"Missing {field} in statistics")
        difference = sum(Decimal(str(r[field])) for r in detail) - Decimal(str(total[field]))
        checks[field] = {"detail_minus_total": float(difference), "matches": difference == 0}
        # The published kilometres have a small source discrepancy; retain it explicitly.
        # Counts and revenue must reconcile exactly or the parser fails closed.
        if difference and field not in allow_difference:
            raise ValueError(f"Statistics do not reconcile: {field} ({difference})")
    return checks


def envelope(dataset_id, records, metadata):
    return {"schema_version": 1, "dataset_id": dataset_id,
            "metadata": {**metadata, "record_count": len(records)}, "records": records}


def build(root, offline=False):
    raw, data = root / "raw_data", root / "data"
    raw.mkdir(exist_ok=True)
    data.mkdir(exist_ok=True)
    downloads = Downloads(raw, offline)
    generated_at = now()
    outputs = []

    def output(name, obj, count, warnings=None):
        path = data / name
        write_json(path, obj, pretty=name not in {"bus_routes.geojson", "bus_stops.geojson", "bus_arrivals.json"})
        outputs.append({"file": "data/" + name, "record_count": count,
                        "sha256": sha256(path), "warnings": warnings or []})

    for kind, dataset_id in DATASETS.items():
        print(kind, flush=True)
        page_url = f"https://data.taipei/dataset/detail?id={dataset_id}"
        page = downloads.fetch(page_url, f"taipei/{kind}.html")
        resources = discover_dataset(page, page_url)
        meta = {"generated_at": generated_at, "dataset_id": dataset_id,
                "source_updated_at": None, "resources": resources}
        if kind == "bus_stops":
            selected = next(r for r in resources if r["format"] == "SHP")
            relative = "taipei/bus_stops.zip"
            path = downloads.fetch(selected["url"], relative)
            obj, crs_meta = stops_geojson(path)
            obj.update({"schema_version": 1, "dataset_id": kind})
            meta.update(crs_meta)
            meta["source"] = downloads.source(relative, page_url)
            meta["resource_published_at"] = selected["published_at"]
            meta["warnings"].append("此站牌資源頁的發布時間較舊；下載時間不代表站位更新時間。")
            obj["metadata"] = {**meta, "record_count": len(obj["features"])}
            output("bus_stops.geojson", obj, len(obj["features"]), meta["warnings"])
        else:
            descriptor = next(r for r in resources if r["format"] == "CSV")
            csv_path = downloads.fetch(descriptor["url"], f"taipei/{kind}_descriptor.csv")
            endpoint, description = feed_url(csv_path)
            relative = f"taipei/{kind}.gz"
            feed = read_feed(downloads.fetch(endpoint, relative))
            meta.update({"source": downloads.source(relative, page_url), "descriptor": description})
            if kind == "bus_routes":
                obj = routes_geojson(feed)
                obj.update({"schema_version": 1, "dataset_id": kind})
                # Keep the page's alternate JSON too, but use its documented daily feed.
                alternate = next((r for r in resources if r["format"] == "JSON"), None)
                warnings = []
                if alternate:
                    alt = read_feed(downloads.fetch(alternate["url"], "taipei/bus_routes_alternate.json"))
                    meta["alternate_resource"] = {"record_count": len(alt), "latest_source_updated_at": max(local_time(r["utime"]) for r in alt)}
                    warnings.append("以 CSV 指向的 GetBusShape.gz 為主；頁面的另一路 JSON 可能較舊，另存原檔。")
                meta["output_crs"] = "EPSG:4326"
                meta["latest_source_updated_at"] = max(f["properties"]["source_updated_at"] for f in obj["features"])
                obj["metadata"] = {**meta, "record_count": len(obj["features"])}
                output("bus_routes.geojson", obj, len(obj["features"]), warnings)
            else:
                records, updated = arrivals_json(feed)
                meta.update({"source_updated_at": updated, "timezone": "Asia/Taipei",
                             "estimate_time_unit": "seconds", "negative_status_codes": ETA_STATUS,
                             "go_back_codes": GO_BACK, "snapshot_only": True,
                             "note": "即時到站預估快照，非班表、班距或歷史班次。"})
                output("bus_arrivals.json", envelope(kind, records, meta), len(records))

    print("pto_statistics", flush=True)
    page = downloads.fetch(PTO_URL, "pto/index.html")
    documents, route_records, operator_records, checks, operator_checks = [], [], [], [], []
    for attachment in pdf_links(page):
        relative = "pto/" + attachment["filename"]
        path = downloads.fetch(attachment["url"], relative)
        if not path.read_bytes().startswith(b"%PDF-"):
            raise ValueError(f"Not a PDF: {attachment['filename']}")
        texts, pages = [], []
        with pdfplumber.open(path) as pdf:
            for page_no, pdf_page in enumerate(pdf.pages, 1):
                text = pdf_page.extract_text() or ""
                if not text.strip():
                    raise ValueError(f"PDF needs OCR: {attachment['filename']} page {page_no}")
                texts.append(text)
                # Generic extraction retains table geometry and positional numeric rows.
                # Only the typed exports below assign metric meanings and units.
                tables = [{"bbox": list(t.bbox), "cells": t.extract()} for t in pdf_page.find_tables()]
                pages.append({"page": page_no, "tables": tables, "numeric_rows": numeric_rows(text)})
        path.with_suffix(".txt").write_text("\n\f\n".join(texts) + "\n", encoding="utf-8")
        document = {**attachment, "period": period_from_text(texts[0]),
                    "source": downloads.source(relative, PTO_URL), "page_count": len(pages),
                    "extraction_status": "layout_extracted_requires_review", "pages": pages}
        if attachment["title"].startswith("20621-01-04"):
            typed = typed_pdf_rows(document, texts, ROUTE_FIELDS, "route_name")
            check = reconcile_totals(typed, ["operated_sections", "distance_km", "passenger_trips", "revenue_twd"],
                                     allow_difference=["distance_km"])
            checks.append({"document_id": attachment["id"], "checks": check})
            route_records.extend(typed)
            document["typed_output"] = "data/bus_route_statistics.json"
        elif attachment["title"].startswith("20621-01-01"):
            typed = typed_pdf_rows(document, texts, OPERATOR_FIELDS, "operator_name")
            operator_checks.append({"document_id": attachment["id"], "checks": reconcile_totals(
                typed, ["operated_trips", "operated_sections", "passenger_trips", "revenue_twd"])})
            operator_records.extend(typed)
            document["typed_output"] = "data/bus_operator_statistics.json"
        documents.append(document)
    if not route_records or not operator_records:
        raise ValueError("Expected bus statistics attachments disappeared")
    sources = [{k: v for k, v in doc.items() if k != "pages"} for doc in documents]
    common = {"generated_at": generated_at, "source_page": PTO_URL, "documents": sources}
    output("pto_tables.json", envelope("pto_tables", documents, {
        "generated_at": generated_at,
        "note": "PDF 版面與數值列的機器抽取；欄位位置不等於統一指標。除另列 typed_output 外，尚待人工核對欄名、單位、合併儲存格。原文另存 raw_data/pto/*.txt。",
    }), len(documents))
    route_warnings = ["行駛段次不是發車班次；年度統計路線名稱不直接等同 API RouteID，含合併路線及專車。"]
    if any(not c["checks"]["distance_km"]["matches"] for c in checks):
        route_warnings.append("來源 PDF 的路線里程加總與總計有差異，原值均保留；詳見 reconciliation。")
    output("bus_route_statistics.json", envelope("bus_route_statistics", route_records, {
        **common, "fields": dict(zip(ROUTE_FIELDS, ["行駛段次（段次）", "行駛里程（公里）", "載客人數（人次）", "每段次平均乘客（人次）", "客運收入（元）", "每公里營收（元/公里）"])),
        "reconciliation": checks, "warnings": route_warnings,
    }), len(route_records), route_warnings)
    output("bus_operator_statistics.json", envelope("bus_operator_statistics", operator_records, {
        **common, "fields": dict(zip(OPERATOR_FIELDS, ["核定路線數（線，月底）", "營業里程（公里，月底）", "核定營業車輛數（輛）", "延日配車輛數（輛日）", "行駛延日車數（輛日）", "行駛班次（班次）", "行駛段次（段次）", "行駛里程（公里）", "客運人次（人次）", "延人公里（人公里）", "客運收入（元）", "耗油情形（柴油公升）", "每公里營收（元/公里）"])),
        "reconciliation": operator_checks,
        "note": "不同月份與修訂版分別保留；總計與公司列以 is_total 區分，核定路線數不可直接加總（共同經營）。",
    }), len(operator_records))
    downloads.save()
    write_json(data / "taipei_bus_catalog.json", {"schema_version": 1, "generated_at": generated_at,
        "mode": "offline_rebuild" if offline else "online_refresh", "timezone": "Asia/Taipei",
        "outputs": outputs, "source_manifest": "raw_data/taipei_bus_manifest.json",
        "scope_notes": ["保留來源完整地理範圍，未裁切跨市路線。", "此更新腳本不處理人口、行政界、路線名稱與 API ID 對照、站序及預定班表；其他來源由獨立腳本維護。",
                        "PDF 的機器抽取不等於所有統計表已完成指標標準化。"]})
    print("Validated: " + ", ".join(f"{o['file']} ({o['record_count']})" for o in outputs), flush=True)


def run(root, offline=False):
    root.mkdir(parents=True, exist_ok=True)
    with (root / ".update-taipei-bus.lock").open("w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError("Another update is running") from exc
        with tempfile.TemporaryDirectory(prefix=".update-", dir=root) as temporary:
            stage = Path(temporary)
            (stage / "raw_data").mkdir()
            # Other agents own other sources; never copy/publish their files.
            for name in ["taipei", "pto"]:
                if (root / "raw_data" / name).exists():
                    shutil.copytree(root / "raw_data" / name, stage / "raw_data" / name)
            manifest = root / "raw_data/taipei_bus_manifest.json"
            if manifest.exists():
                shutil.copy2(manifest, stage / "raw_data/taipei_bus_manifest.json")
            build(stage, offline)
            # Network/parse/validation failures above leave the existing snapshot intact.
            # Publish data last; replace each completed file atomically.
            for directory in ["raw_data", "data"]:
                for path in sorted((stage / directory).rglob("*"),
                                   key=lambda p: (p.name == "taipei_bus_catalog.json", str(p))):
                    if path.is_file():
                        destination = root / path.relative_to(stage)
                        destination.parent.mkdir(parents=True, exist_ok=True)
                        path.replace(destination)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--offline", action="store_true", help="Rebuild from verified raw_data without network")
    parser.add_argument("--root", type=Path, default=ROOT, help="Output root (default: repository)")
    args = parser.parse_args()
    try:
        run(args.root.resolve(), args.offline)
    except Exception as exc:
        print(f"Update failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
