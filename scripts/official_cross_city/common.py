"""Bounded, HTTPS-only downloads and strict CSV parsing (standard library only)."""
from __future__ import annotations

import csv
import json
import math
import os
import re
import shutil
import tempfile
import time
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Iterable, Iterator
from urllib.parse import urljoin, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[2]
TZ = ZoneInfo("Asia/Taipei")
OGDL = {"name": "政府資料開放授權條款-第1版", "url": "https://data.gov.tw/license"}
NULLS = {"", "-", "--", "…", "N/A", "NA", "null"}


def now() -> str:
    return datetime.now(TZ).isoformat(timespec="seconds")


def check_url(url: str, hosts: set[str]) -> str:
    p = urlsplit(url)
    if (p.scheme != "https" or p.hostname not in hosts or p.username or p.password
            or p.port not in {None, 443} or p.fragment):
        raise ValueError(f"Source or redirect is outside the approved HTTPS hosts: {url}")
    return url


class SafeRedirect(HTTPRedirectHandler):
    def __init__(self, hosts: set[str]):
        self.hosts = hosts

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        check_url(newurl, self.hosts)  # Check BEFORE issuing the next network request.
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def write_json(path: Path, value: object) -> None:
    """An unsuccessful serialization cannot replace the last successful output."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=path.parent, suffix=".part")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(value, f, ensure_ascii=False, allow_nan=False, indent=2)
            f.write("\n")
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def fetch(root: Path, name: str, url: str, page_url: str, hosts: set[str],
          *, offline: bool, max_mb: int = 80) -> tuple[Path, dict]:
    """Fetch a complete file, not an API preview page. No keys or paid services."""
    check_url(url, hosts)
    if Path(name).name != name or not re.fullmatch(r"[A-Za-z0-9_.-]+", name):
        raise ValueError("Raw filename must be a simple local name")
    raw = root / "raw_data/official_cross_city"
    path, receipt_path = raw / name, raw / (name + ".source.json")
    if offline:
        if not path.is_file() or not receipt_path.is_file():
            raise ValueError(f"Offline source missing: {path}")
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        if (receipt.get("requested_url") != url or receipt.get("page_url") != page_url
                or receipt.get("bytes") != path.stat().st_size
                or receipt.get("http_status") != 200 or not receipt.get("downloaded_at")):
            raise ValueError("Cached file provenance/size does not match this source")
        check_url(receipt["effective_url"], hosts)
        return path, receipt
    if not 1 <= max_mb <= 1024:
        raise ValueError("max_mb must be between 1 and 1024")
    raw.mkdir(parents=True, exist_ok=True)
    limit = max_mb * 1024 * 1024
    if shutil.disk_usage(raw).free < limit * 2:
        raise ValueError("Insufficient free space for a bounded download and conversion")
    fd, temp_name = tempfile.mkstemp(dir=raw, suffix=".part")
    # Do not keep an old receipt attached to new bytes after a crash.
    try:
        req = Request(url, headers={"User-Agent": "bus-analysis-official-data/1.0",
                                    "Accept-Encoding": "identity"})
        opener = build_opener(SafeRedirect(hosts))
        started = time.monotonic()
        with os.fdopen(fd, "wb") as out, opener.open(req, timeout=60) as response:
            if response.status != 200:
                raise ValueError(f"Expected complete HTTP 200, got {response.status}")
            effective = check_url(response.geturl(), hosts)
            content_type = response.headers.get("Content-Type", "").lower()
            if name.endswith(".csv") and "html" in content_type:
                raise ValueError("HTML response cannot replace a CSV source")
            declared = response.headers.get("Content-Length")
            if declared and int(declared) > limit:
                raise ValueError("Response exceeds byte limit")
            count = 0
            while block := response.read(1024 * 1024):
                if time.monotonic() - started > 900:
                    raise ValueError("Download exceeded the total time budget")
                count += len(block)
                if count > limit:
                    raise ValueError("Streaming response exceeds byte limit")
                out.write(block)
            if count == 0 or (declared and count != int(declared)):
                raise ValueError("Empty or truncated response")
            receipt = {"page_url": page_url, "requested_url": url,
                       "effective_url": effective, "downloaded_at": now(),
                       "source_data_time": None, "http_status": response.status,
                       "http_last_modified": response.headers.get("Last-Modified"),
                       "content_type": response.headers.get("Content-Type"),
                       "bytes": count, "raw_file": str(path.relative_to(root))}
        receipt_path.unlink(missing_ok=True)
        os.replace(temp_name, path)
        write_json(receipt_path, receipt)
        return path, receipt
    finally:
        Path(temp_name).unlink(missing_ok=True)


def csv_rows(path: Path, required: Iterable[str], *, encoding="utf-8-sig",
             lower_headers=False) -> Iterator[tuple[int, dict[str, str]]]:
    """Fail closed on HTML errors, duplicate headers and malformed record widths."""
    with path.open(encoding=encoding, newline="") as f:
        reader = csv.reader(f)
        header = next(reader, None)
        if not header:
            raise ValueError("Empty CSV")
        keys = [v.strip().lower() if lower_headers else v.strip() for v in header]
        if any(not k for k in keys) or len(keys) != len(set(keys)):
            raise ValueError("Empty or duplicate CSV headers")
        missing = set(required) - set(keys)
        if missing:
            raise ValueError(f"CSV schema mismatch; missing: {sorted(missing)}")
        found = False
        for row in reader:
            if not row or not any(cell.strip() for cell in row):
                continue
            if len(row) != len(keys):
                raise ValueError(f"CSV row width mismatch at line {reader.line_num}")
            found = True
            yield reader.line_num, dict(zip(keys, (v.strip() for v in row)))
        if not found:
            raise ValueError("CSV has a header but no data rows")


def text(value: str | None) -> str | None:
    return None if value is None or value.strip() in NULLS else value.strip()


def integer(value: str | None, *, nullable=True) -> int | None:
    value = text(value)
    if value is None:
        if nullable:
            return None
        raise ValueError("Required integer is missing")
    # Accept correctly grouped thousands, not malformed 1,2 or fractional counts.
    if not re.fullmatch(r"(?:\d+|\d{1,3}(?:,\d{3})+)", value):
        raise ValueError(f"Invalid non-negative integer: {value}")
    return int(value.replace(",", ""))


def coordinate(lon: str | None, lat: str | None) -> list[float] | None:
    try:
        x, y = float(lon or "nan"), float(lat or "nan")
        return [x, y] if math.isfinite(x) and math.isfinite(y) and 119 <= x <= 123 and 21 <= y <= 26.5 else None
    except ValueError:
        return None


def validate_month(value: str) -> str:
    if not re.fullmatch(r"20\d{2}-(?:0[1-9]|1[0-2])", value):
        raise ValueError("Month must be YYYY-MM")
    return value


class Links(HTMLParser):
    """Preserve anchor labels for exact, explicit monthly resource selection."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.links: list[tuple[str, str]] = []
        self.href: str | None = None
        self.parts: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self.href = dict(attrs).get("href")
            self.parts = []

    def handle_data(self, data):
        if self.href is not None:
            self.parts.append(data)

    def handle_endtag(self, tag):
        if tag == "a" and self.href is not None:
            self.links.append((self.href, "".join(self.parts)))
            self.href = None
            self.parts = []


def monthly_link(html: str, page_url: str, month: str, hosts: set[str]) -> str:
    validate_month(month)
    year, mm = month.split("-")
    roc = f"{int(year) - 1911:03d}{mm}"
    parser = Links()
    parser.feed(html)
    candidates = set()
    for href, label in parser.links:
        if re.search(rf"(?<!\d)(?:{roc}|{year}{mm}|{year}-{mm})(?!\d)", label):
            url = urljoin(page_url, href)
            if urlsplit(url).hostname in hosts:
                candidates.add(check_url(url, hosts))
    if len(candidates) != 1:
        raise ValueError(f"Expected exactly one official link for {month}; found {len(candidates)}. Do not guess a URL.")
    return candidates.pop()


def metadata(source: dict, provider: str, grain: str, limitations: list[str]) -> dict:
    return {"provider": provider, "source": source, "license": OGDL,
            "grain": grain, "data_kind": "official_published_data",
            "source_data_time": None, "generated_at": now(),
            "independent_review_status": "pending",
            "limitations": limitations}
