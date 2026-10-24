"""Shared serialization and administrative-boundary helpers for traffic candidates."""

import csv
from datetime import datetime
import gzip
import json
from pathlib import Path
from zoneinfo import ZoneInfo

from shapely.geometry import shape

ROOT = Path(__file__).resolve().parents[1]
MAP = ROOT / "map-data"


def now():
    return datetime.now(ZoneInfo("Asia/Taipei")).isoformat(timespec="seconds")


def read(path):
    opener = gzip.open if str(path).endswith(".gz") else open
    with opener(path, "rt", encoding="utf-8") as stream:
        return json.load(stream)


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".part")
    # Compact GeoJSON keeps the derived layers small without requiring decompression.
    options = {"separators": (",", ":")} if path.suffix == ".geojson" else {"indent": 2}
    temporary.write_text(json.dumps(value, ensure_ascii=False, allow_nan=False, **options) + "\n", encoding="utf-8")
    temporary.replace(path)


def write_csv(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".csv.part")
    with temporary.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    temporary.replace(path)


def boundaries():
    cities = read(MAP / "admin/cities.geojson")
    city = next(shape(f["geometry"]) for f in cities["features"] if f["properties"]["admin_code"] == "63000")
    districts = [(f["properties"], shape(f["geometry"])) for f in read(MAP / "admin/districts.geojson")["features"]
                 if f["properties"]["city_code"] == "63000"]
    return city, districts


def district_for(point, districts):
    matches = sorted((p for p, geom in districts if geom.covers(point)), key=lambda p: p["admin_code"])
    return matches[0] if matches else None


def boundary_sources():
    return [{"path": entry["path"], "source_period": entry["source_period"], "sources": entry["sources"]}
            for entry in read(MAP / "admin/manifest.json")["records"]
            if entry["path"] in {"map-data/admin/cities.geojson", "map-data/admin/districts.geojson"}]


def output_entry(path, doc):
    return {"dataset_id": doc["dataset_id"], "path": str(path.relative_to(ROOT)),
            "record_count": len(doc.get("features", doc.get("records", []))), "bytes": path.stat().st_size}
