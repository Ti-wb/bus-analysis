#!/usr/bin/env python3
"""Download the official Taipei YouBike 2.0 feed and normalize its station list."""
import argparse
from collections import Counter
from datetime import datetime
import json
import math
from pathlib import Path
import subprocess
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from shapely.geometry import Point, shape
from shapely.prepared import prep

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "raw_data/youbike"
OUT = ROOT / "data/youbike"
TZ = ZoneInfo("Asia/Taipei")
PAGE_URL = "https://data.taipei/dataset/detail?id=c6bc8aed-557d-41d5-bfb1-8da24f78f2fb"
FEED_URL = "https://tcgbusfs.blob.core.windows.net/dotapp/youbike/v2/youbike_immediate.json"
FEED_FILE = "youbike_immediate.json"
NAME_PREFIX = "YouBike2.0_"
# Source field -> output field. Live bike counts are left in the raw snapshot only.
FIELDS = {
    "sno": "station_id",
    "sna": "source_name",
    "snaen": "source_name_en",
    "sarea": "source_area",
    "sareaen": "source_area_en",
    "ar": "address",
    "aren": "address_en",
    "Quantity": "dock_count",
    "act": "source_active_code",
    "mday": "source_updated_at",
}


def now():
    return datetime.now(TZ).isoformat(timespec="seconds")


def write_json(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".part")
    with temp.open("w", encoding="utf-8", newline="\n") as f:
        json.dump(obj, f, ensure_ascii=False, allow_nan=False, indent=2)
        f.write("\n")
    temp.replace(path)


def read_json(path):
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def fetch(offline):
    """Download the feed into raw_data/youbike/ and return its manifest entry."""
    manifest_path = RAW / "manifest.json"
    manifest = read_json(manifest_path) if manifest_path.exists() else {}
    path = RAW / FEED_FILE
    if offline:
        if FEED_FILE not in manifest or not path.exists() or not path.stat().st_size:
            raise ValueError(f"Missing offline source: {path.relative_to(ROOT)}")
        return manifest[FEED_FILE]
    if urlparse(FEED_URL).scheme != "https":
        raise ValueError(f"HTTPS required: {FEED_URL}")
    RAW.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".part")
    headers = path.with_suffix(".headers")
    command = ["curl", "--fail", "--location", "--silent", "--show-error",
               "--proto", "=https", "--proto-redir", "=https",
               "--connect-timeout", "20", "--max-time", "120", "--retry", "2",
               "--user-agent", "bus-analysis/1.0", "--dump-header", str(headers),
               "--output", str(temp), "--write-out", "%{json}", FEED_URL]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=400)
        if result.returncode:
            raise RuntimeError(result.stderr.strip())
        stat = json.loads(result.stdout)
        if stat["http_code"] != 200 or not temp.stat().st_size:
            raise ValueError("Unexpected YouBike download")
        response = {}
        for line in headers.read_text(encoding="latin-1").splitlines():
            if line.startswith("HTTP/"):
                response = {}
            elif ":" in line:
                key, value = line.split(":", 1)
                response[key.lower()] = value.strip()
        temp.replace(path)
    finally:
        temp.unlink(missing_ok=True)
        headers.unlink(missing_ok=True)
    manifest[FEED_FILE] = {
        "page_url": PAGE_URL,
        "download_url": FEED_URL,
        "resolved_url": stat["url_effective"],
        "raw_file": path.relative_to(ROOT).as_posix(),
        "downloaded_at": now(),
        "bytes": path.stat().st_size,
        "http_last_modified": response.get("last-modified"),
        "content_type": response.get("content-type"),
    }
    write_json(manifest_path, manifest)
    print(FEED_FILE, path.stat().st_size, flush=True)
    return manifest[FEED_FILE]


def text_or_none(value):
    if value is None:
        return None
    return str(value).strip() or None


def source_time(value):
    """Source times are local 'YYYY-MM-DD HH:MM:SS'; keep them as Asia/Taipei ISO strings."""
    s = text_or_none(value)
    return datetime.fromisoformat(s).replace(tzinfo=TZ).isoformat() if s else None


def load_districts():
    """Taipei district polygons from map-data/admin, used to assign an administrative district."""
    collection = read_json(ROOT / "map-data/admin/districts.geojson")
    districts = []
    for feature in collection["features"]:
        props = feature["properties"]
        if props["city_code"] != "63000":
            continue
        districts.append((props["district_name"], props["admin_code"], prep(shape(feature["geometry"]))))
    if len(districts) != 12:
        raise ValueError(f"Expected 12 Taipei districts, found {len(districts)}")
    return districts


def build(entry):
    rows = read_json(RAW / FEED_FILE)
    if not isinstance(rows, list) or not rows:
        raise ValueError("YouBike feed is not a non-empty list")
    districts = load_districts()
    features, outside, mismatched = [], [], []
    for row in rows:
        missing = [k for k in (*FIELDS, "latitude", "longitude") if k not in row]
        if missing:
            raise ValueError(f"Station {row.get('sno')} lacks fields {missing}")
        props = {out: text_or_none(row[src]) for src, out in FIELDS.items()}
        props["dock_count"] = row["Quantity"] if isinstance(row["Quantity"], int) else None
        props["source_updated_at"] = source_time(row["mday"])
        name = props["source_name"] or ""
        props["name"] = name.removeprefix(NAME_PREFIX) or None
        props["is_active"] = {"1": True, "0": False}.get(props["source_active_code"])
        lon, lat = row["longitude"], row["latitude"]
        if not all(isinstance(v, (int, float)) and math.isfinite(v) for v in (lon, lat)):
            raise ValueError(f"Station {props['station_id']} has a non-numeric coordinate")
        if not (121.3 <= lon <= 121.8 and 24.9 <= lat <= 25.3):
            raise ValueError(f"Station {props['station_id']} lies outside the Taipei area")
        point = Point(lon, lat)
        hit = next(((n, c) for n, c, poly in districts if poly.covers(point)), (None, None))
        props["district"], props["district_code"] = hit
        if hit[0] is None:
            outside.append(props["station_id"])
        elif props["source_area"] != hit[0]:
            mismatched.append(props["station_id"])
        features.append({"type": "Feature", "id": props["station_id"], "properties": props,
                         "geometry": {"type": "Point", "coordinates": [lon, lat]}})
    ids = Counter(f["id"] for f in features)
    duplicates = sorted(k for k, v in ids.items() if v > 1)
    if duplicates:
        raise ValueError(f"Duplicate station IDs: {duplicates[:5]}")
    features.sort(key=lambda f: f["id"])
    snapshot = Counter(f["properties"]["source_updated_at"] for f in features).most_common(1)[0][0]
    live = Counter((r["available_rent_bikes"], r["available_return_bikes"]) == (0, 0) for r in rows)
    metadata = {
        "crs": "EPSG:4326",
        "coordinate_order": ["longitude", "latitude"],
        "sources": [{**entry, "source_data_time": snapshot, "source_update_frequency": "每 1 分鐘（來源標示）",
                     "provider": "臺北市政府交通局", "license": "data.taipei 標示資料授權「公開」"}],
        "source_data_time": snapshot,
        "statistical_period": None,
        "grain": "one YouBike 2.0 station in one feed snapshot",
        "units": {"dock_count": "docks"},
        "timezone": "Asia/Taipei",
        "source_crs": "EPSG:4326 inferred from explicit longitude/latitude fields and Taipei bounds",
        "field_mapping": {out: src for src, out in FIELDS.items()} | {"name": "sna without the 'YouBike2.0_' prefix",
                                                                      "is_active": "act: '1' true, '0' false",
                                                                      "district": "point in map-data/admin/districts.geojson"},
        "missing_values": "Blank source values become null; no missing values imputed as zero.",
        "limitations": [
            "來源是每分鐘更新的即時資料；本檔只保留站點屬性，可借、可還車數只留在原始快照，不能當成需求或使用量。",
            "原始快照的可借、可還車數全站皆為 0，疑為來源異常，不可當實際車況。",
            "source_updated_at 取自來源 mday，全站相同，是來源批次時間，不是各站實際異動時間。",
            "dock_count 是來源 Quantity（總停車格），不是站點容量評估。",
            "is_active 為 false 的站點仍保留，代表快照當下暫停營運，不代表已撤站。",
            "source_area 沿用來源場站區域，含「臺大公館校區」等非行政區；district 由座標落點對應 map-data/admin 行政區另行計算。",
            "只含臺北市 YouBike 2.0，不含新北市或其他系統的站點。",
        ],
        "quality": {
            "source_record_count": len(rows),
            "inactive_station_count": sum(1 for f in features if f["properties"]["is_active"] is False),
            "outside_taipei_district_ids": outside,
            "source_area_differs_from_district_ids": mismatched,
            "snapshot_rent_and_return_both_zero_count": live[True],
        },
    }
    output = OUT / "stations.geojson"
    write_json(output, {"type": "FeatureCollection", "schema_version": 1, "dataset_id": "taipei_youbike_stations",
                        "metadata": metadata, "features": features})
    write_json(RAW / "delivery.json", {"schema_version": 1, "prepared_at": now(), "datasets": [{
        "dataset_id": "taipei_youbike_stations",
        "status": "normalized",
        "output_files": [output.relative_to(ROOT).as_posix()],
        "record_count": len(features),
        "period": snapshot,
        "limitations": metadata["limitations"],
        "bytes": output.stat().st_size,
    }]})
    print(json.dumps({"stations": len(features), **{k: v if isinstance(v, int) else len(v)
                                                     for k, v in metadata["quality"].items()}},
                     ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--offline", action="store_true", help="Rebuild from the kept raw snapshot without downloading.")
    args = parser.parse_args()
    build(fetch(args.offline))


if __name__ == "__main__":
    main()
