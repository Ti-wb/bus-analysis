#!/usr/bin/env python3
"""Download a dated Geofabrik extract and keep GIS layers inside Taipei/New Taipei."""

import argparse
from collections import Counter
from datetime import datetime
import gzip
import json
from pathlib import Path
import re
import shutil
import sqlite3
import struct
from urllib.request import Request, urlopen
import zipfile
from zoneinfo import ZoneInfo

from pyproj import CRS
from shapely import from_wkb, make_valid, orient_polygons, union_all
from shapely.geometry import shape, mapping, MultiPolygon, MultiLineString, MultiPoint
from shapely.prepared import prep

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "map-data/raw/osm"
OUT = ROOT / "map-data/osm"
BOUNDARY = ROOT / "map-data/admin/cities.geojson"
SOURCE_PAGE = "https://download.geofabrik.de/asia/taiwan.html"
FORMAT_URL = "https://download.geofabrik.de/osm-data-in-gis-formats-free.pdf"
LICENSE_URL = "https://opendatacommons.org/licenses/odbl/1-0/"
LAYERS = {
    "roads": ("gis_osm_roads_free", "道路與步行路徑"),
    "railways": ("gis_osm_railways_free", "鐵路、捷運與輕軌線形"),
    "waterways": ("gis_osm_waterways_free", "河流與水道中心線"),
    "water": ("gis_osm_water_a_free", "河川、湖泊與水庫水面"),
    "landuse": ("gis_osm_landuse_a_free", "土地覆蓋與公園綠地"),
    "protected_areas": ("gis_osm_protected_areas_a_free", "保護區"),
    "buildings": ("gis_osm_buildings_a_free", "建物輪廓"),
    "places": ("gis_osm_places_free", "聚落與地名標示點"),
    "place_areas": ("gis_osm_places_a_free", "聚落與島嶼範圍"),
    "transport": ("gis_osm_transport_free", "車站與運輸設施點"),
    "transport_areas": ("gis_osm_transport_a_free", "車站與運輸設施範圍"),
    "pois": ("gis_osm_pois_free", "公共設施與興趣點"),
    "poi_areas": ("gis_osm_pois_a_free", "公共設施與興趣點範圍"),
    "traffic": ("gis_osm_traffic_free", "交通與停車設施點"),
    "traffic_areas": ("gis_osm_traffic_a_free", "交通與停車設施範圍"),
    "natural": ("gis_osm_natural_free", "自然地標點"),
    "natural_areas": ("gis_osm_natural_a_free", "自然地標範圍"),
    "worship": ("gis_osm_pofw_free", "宗教設施點"),
    "worship_areas": ("gis_osm_pofw_a_free", "宗教設施範圍"),
}


def now():
    return datetime.now(ZoneInfo("Asia/Taipei")).isoformat(timespec="seconds")


def encode(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    part = path.with_suffix(path.suffix + ".part")
    part.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    part.replace(path)


def download(source_date, offline):
    filename = "taiwan-" + source_date.strftime("%y%m%d") + "-free.gpkg.zip"
    archive = RAW / filename
    url = "https://download.geofabrik.de/asia/" + filename
    receipt = RAW / "download.json"
    if not archive.exists():
        if offline:
            raise FileNotFoundError(f"Offline source archive missing: {archive}")
        RAW.mkdir(parents=True, exist_ok=True)
        part = archive.with_suffix(".zip.part")
        limit = 350 * 1024**2
        try:
            with urlopen(Request(url, headers={"User-Agent": "bus-analysis GIS data preparation"}), timeout=120) as response, part.open("wb") as f:
                expected = int(response.headers.get("Content-Length", 0))
                if expected > limit:
                    raise ValueError("Source archive exceeds 350 MiB local download limit")
                size = 0
                while chunk := response.read(1024**2):
                    size += len(chunk)
                    if size > limit:
                        raise ValueError("Source archive exceeds 350 MiB local download limit")
                    f.write(chunk)
                if expected and size != expected:
                    raise ValueError("Incomplete source archive")
                source = {"source_page": SOURCE_PAGE, "download_url": url, "downloaded_at": now(),
                          "http_last_modified": response.headers.get("Last-Modified"), "download_bytes": size}
            part.replace(archive)
            write_json(receipt, source)
        finally:
            part.unlink(missing_ok=True)
    if receipt.exists():
        source = json.loads(receipt.read_text(encoding="utf-8"))
        if source["download_url"] != url:
            raise ValueError("Cached download receipt refers to a different source date")
    else:
        # A manually fetched archive is supported without inventing a download timestamp.
        source = {"source_page": SOURCE_PAGE, "download_url": url, "downloaded_at": None,
                  "http_last_modified": None, "download_bytes": archive.stat().st_size}
    gpkg = RAW / "taiwan.gpkg"
    with zipfile.ZipFile(archive) as z:
        info = z.getinfo("taiwan.gpkg")
        if info.file_size > 2 * 1024**3:
            raise ValueError("GeoPackage exceeds 2 GiB extraction limit")
        # Extract each run so --date cannot accidentally reuse another snapshot's database.
        part = gpkg.with_suffix(".gpkg.part")
        with z.open(info) as src, part.open("wb") as target:
            shutil.copyfileobj(src, target, 1024**2)
        part.replace(gpkg)
        text = z.read("README").decode("utf-8")
        (RAW / "source-readme.txt").write_text(text, encoding="utf-8")
    match = re.search(r"data as of\s+(\d{4}-\d{2}-\d{2}T\S+Z)", text)
    if not match:
        raise ValueError("Source README does not identify the OSM snapshot timestamp")
    source.update({"source_updated_at": match[1], "archive_path": str(archive.relative_to(ROOT)),
                   "format_description": FORMAT_URL, "license": "ODbL-1.0", "license_url": LICENSE_URL,
                   "attribution": "© OpenStreetMap contributors; processed by Geofabrik"})
    return gpkg, source


def decode_geometry(blob, expected_srs):
    if blob is None or blob[:3] != b"GP\x00" or blob[3] & 0b11100000:
        raise ValueError("Unexpected GeoPackage geometry header")
    sizes = {0: 0, 1: 32, 2: 48, 3: 48, 4: 64}
    flags = blob[3]
    envelope = (flags >> 1) & 7
    if envelope not in sizes:
        raise ValueError("Reserved GeoPackage envelope code")
    endian = "<" if flags & 1 else ">"
    if struct.unpack_from(endian + "i", blob, 4)[0] != expected_srs:
        raise ValueError("Geometry header CRS differs from layer CRS")
    bounds = None
    if envelope:
        xmin, xmax, ymin, ymax = struct.unpack_from(endian + "4d", blob, 8)
        bounds = (xmin, ymin, xmax, ymax)
    return blob[8 + sizes[envelope]:], bounds


def same_dimension(geom, dimension):
    """Drop lower-dimensional boundary touches, not actual line/area features."""
    singular = {0: "Point", 1: "LineString", 2: "Polygon"}[dimension]
    if geom.is_empty:
        return None
    if geom.geom_type in {singular, "Multi" + singular}:
        return geom
    if not hasattr(geom, "geoms"):
        return None
    parts = []
    for part in geom.geoms:
        selected = same_dimension(part, dimension)
        if selected is not None:
            parts.extend(selected.geoms if selected.geom_type.startswith("Multi") else [selected])
    if not parts:
        return None
    return {0: MultiPoint, 1: MultiLineString, 2: MultiPolygon}[dimension](parts)


def rounded_geometry(geom):
    original = mapping(orient_polygons(geom))
    def rounding(value):
        if isinstance(value, (tuple, list)):
            return [rounding(v) for v in value]
        return round(value, 7)
    rounded = {"type": original["type"], "coordinates": rounding(original["coordinates"])}
    candidate = shape(rounded)
    if candidate.is_empty or not candidate.is_valid:
        return original, True
    return mapping(orient_polygons(candidate)), False


def properties(row, layer, zero_counts):
    out = {"osm_id": str(row["osm_id"]), "source_fid": str(row["fid"]),
           "class_code": str(row["code"]), "feature_class": row["fclass"]}
    rename = {"maxspeed": "maxspeed_kmh", "width": "width_m", "type": "building_type"}
    for key in row.keys():
        if key in {"fid", "geom", "osm_id", "code", "fclass"}:
            continue
        value = row[key]
        if isinstance(value, str) and not value.strip():
            value = None
        if key in {"maxspeed", "population", "width"} and value == 0:
            zero_counts[rename.get(key, key)] += 1
            value = None
        out[rename.get(key, key)] = value
    return out


def build_layer(con, name, table, title, source, mask, clip_metadata, stage, max_mib):
    info = con.execute("SELECT * FROM gpkg_geometry_columns WHERE table_name=?", (table,)).fetchone()
    if not info or info["column_name"] != "geom" or info["z"] or info["m"]:
        raise ValueError(f"Unsupported layer schema: {table}")
    srs = con.execute("SELECT * FROM gpkg_spatial_ref_sys WHERE srs_id=?", (info["srs_id"],)).fetchone()
    if not CRS.from_wkt(srs["definition"]).equals(CRS.from_epsg(4326), ignore_axis_order=True):
        raise ValueError("OSM source CRS is not WGS84")
    dim = 2 if "POLYGON" in info["geometry_type_name"] else 1 if "LINESTRING" in info["geometry_type_name"] else 0
    prepared = prep(mask)
    xmin, ymin, xmax, ymax = mask.bounds
    counters = Counter()
    zeros = Counter()
    classes = Counter()
    chunks = []
    lines = []
    current_bytes = 0
    chunk_bounds = []
    repaired_ids = []
    fallback_ids = []
    def flush():
        nonlocal lines, current_bytes, chunk_bounds
        if not lines:
            return
        number = len(chunks) + 1
        filename = f"{name}.geojson.gz" if number == 1 else f"{name}-{number:03}.geojson.gz"
        path = stage / filename
        bbox = [min(b[0] for b in chunk_bounds), min(b[1] for b in chunk_bounds),
                max(b[2] for b in chunk_bounds), max(b[3] for b in chunk_bounds)]
        metadata = {"title": title, "record_count": len(lines), "source": source,
                    "grain": "one retained source fid per feature; clipped geometry may contain multiple parts",
                    "source_layer": table, "coordinate_reference_system": "EPSG:4326",
                    "source_crs": "OGC:CRS84 (WGS84 longitude, latitude)",
                    "scope": "臺北市與新北市官方市界聯集", "clip_boundary": clip_metadata,
                    "coordinate_decimal_places": 7,
                    "rounding_note": "約公分級座標取位；若取位造成無效幾何，保留裁切後完整精度。未簡化線形。",
                    "ring_orientation": "RFC 7946: exterior counter-clockwise, holes clockwise",
                    "id_note": "feature.id由圖層及來源fid組成，只在同一來源快照內穩定；osm_id可能跨OSM型別或拆分要素重複。",
                    "limitations": ["Geofabrik免費版是OSM圖徵與欄位的選集，不是完整OSM資料庫。",
                                    "沒有完整轉向限制、通行權限、車高車重與路網拓樸，不可直接當公車導航路網。",
                                    "市界裁切會截斷跨市邊界要素；點與面圖層可能描述同一設施，不可直接加總。",
                                    "來源未提供各要素更新時間；OSM車站ID不可直接連接TDX或北市公車ID。"]}
        header = {"type": "FeatureCollection", "schema_version": 1, "dataset_id": "osm_taipei_new_taipei_" + name,
                  "bbox": bbox, "metadata": metadata}
        body = (encode(header)[:-1] + ',"features":[\n' + ',\n'.join(lines) + '\n]}\n').encode("utf-8")
        with path.open("wb") as f:
            with gzip.GzipFile(filename="", mode="wb", fileobj=f, compresslevel=9, mtime=0) as compressed:
                compressed.write(body)
        chunks.append({"path": str((OUT / filename).relative_to(ROOT)), "record_count": len(lines),
                       "bytes": path.stat().st_size, "uncompressed_bytes": len(body),
                       "encoding": "gzip", "bbox": bbox})
        lines = []; current_bytes = 0; chunk_bounds = []
    for row in con.execute(f'SELECT * FROM "{table}" ORDER BY fid'):
        counters["source_rows"] += 1
        wkb, bounds = decode_geometry(row["geom"], info["srs_id"])
        if bounds and (bounds[0] > xmax or bounds[2] < xmin or bounds[1] > ymax or bounds[3] < ymin):
            continue
        geom = from_wkb(wkb)
        if geom.is_empty:
            counters["empty_source_geometries"] += 1
            continue
        if not geom.is_valid:
            geom = same_dimension(make_valid(geom), dim)
            if geom is None:
                raise ValueError(f"Source geometry cannot be repaired: {table}:{row['fid']}")
            repaired_ids.append(str(row["fid"]))
        if not prepared.intersects(geom):
            continue
        if not prepared.covers(geom):
            geom = same_dimension(geom.intersection(mask), dim)
            if geom is None:
                counters["boundary_touch_only"] += 1
                continue
            counters["clipped_features"] += 1
        if not geom.is_valid:
            raise ValueError(f"Invalid clipped geometry: {table}:{row['fid']}")
        geometry, fallback = rounded_geometry(geom)
        if fallback:
            fallback_ids.append(str(row["fid"]))
        props = properties(row, name, zeros)
        feature = {"type": "Feature", "id": f"{name}:{row['fid']}", "properties": props, "geometry": geometry}
        line = encode(feature)
        size = len(line.encode("utf-8")) + 2
        if size > max_mib * 1024**2:
            raise ValueError(f"Single feature exceeds output size cap: {feature['id']}")
        if lines and current_bytes + size > max_mib * 1024**2:
            flush()
        lines.append(line); current_bytes += size; chunk_bounds.append(shape(geometry).bounds)
        counters["output_features"] += 1
        classes[props["feature_class"]] += 1
    flush()
    if not chunks:
        raise ValueError(f"Expected layer has no features in Taipei/New Taipei: {table}")
    print(f"{name}: {counters['output_features']} features; {sum(c['bytes'] for c in chunks)/1024**2:.2f} MiB", flush=True)
    return {"dataset_id": "osm_taipei_new_taipei_" + name, "title": title, "source_layer": table,
            "grain": "one retained source fid per feature; clipped geometry may contain multiple parts",
            "geometry_dimension": dim, "source_srs_id": info["srs_id"], "outputs": chunks,
            "counts": dict(counters), "classes": dict(sorted(classes.items())),
            "source_zero_to_null_counts": dict(zeros), "repaired_source_fids": repaired_ids,
            "full_precision_fallback_fids": fallback_ids}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", default="2026-10-20", help="Dated Geofabrik archive, YYYY-MM-DD")
    parser.add_argument("--offline", action="store_true", help="Rebuild from the local cached source archive")
    parser.add_argument("--max-file-mib", type=float, default=24, help="Split large layers into bounded GeoJSON files")
    args = parser.parse_args()
    if not 1 <= args.max_file_mib <= 40:
        parser.error("--max-file-mib must be between 1 and 40")
    source_date = datetime.strptime(args.date, "%Y-%m-%d").date()
    boundary = json.loads(BOUNDARY.read_text(encoding="utf-8"))
    if len(boundary["features"]) != 2 or {f["properties"]["city_code"] for f in boundary["features"]} != {"63000", "65000"}:
        raise ValueError("Expected official boundaries for exactly Taipei and New Taipei")
    mask = union_all([shape(f["geometry"]) for f in boundary["features"]])
    if not mask.is_valid or mask.is_empty:
        raise ValueError("Invalid city clipping boundary")
    gpkg, source = download(source_date, args.offline)
    con = sqlite3.connect(gpkg.as_uri() + "?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    stage = RAW / "build"
    stage.mkdir(parents=True, exist_ok=True)
    clip_metadata = {"path": str(BOUNDARY.relative_to(ROOT)), "metadata": boundary.get("metadata", {})}
    records = [build_layer(con, name, table, title, source, mask, clip_metadata, stage, args.max_file_mib)
               for name, (table, title) in LAYERS.items()]
    con.close()
    manifest = {"schema_version": 1, "dataset_id": "osm_taipei_new_taipei_catalog",
                "metadata": {"generated_at": now(), "source": source, "record_count": len(records),
                    "clip_boundary": str(BOUNDARY.relative_to(ROOT)), "bbox": list(mask.bounds),
                    "attribution": source["attribution"], "license": "ODbL-1.0", "license_url": LICENSE_URL,
                    "excluded_source_layers": ["gis_osm_adminareas_a_free: 改用admin/官方行政界"],
                    "normalization": ["空字串改null；ID/code保留字串。",
                        "population來源0為未知；maxspeed與width來源0不作有效量測值，均轉null並記錄筆數。",
                        "oneway保留Geofabrik F=順線、T=逆線、B=雙向；bridge/tunnel保留T/F來源碼，可能含來源預設值。",
                        "保留全部相交的來源圖徵，線與面裁切至雙北市界；純點/線接觸不當成面積要素。"],
                    "units": {"coordinates": "degrees", "maxspeed_kmh": "km/h", "width_m": "m", "population": "persons", "layer": "relative drawing order"}},
                "records": records}
    previous = json.loads((OUT / "manifest.json").read_text(encoding="utf-8")) if (OUT / "manifest.json").exists() else {"records": []}
    OUT.mkdir(parents=True, exist_ok=True)
    current = set()
    for record in records:
        for output in record["outputs"]:
            path = ROOT / output["path"]
            (stage / path.name).replace(path)
            current.add(path)
    for record in previous["records"]:
        for output in record.get("outputs", []):
            old = ROOT / output["path"]
            if old.parent == OUT and (old.name.endswith(".geojson") or old.name.endswith(".geojson.gz")) and old not in current:
                old.unlink(missing_ok=True)
    write_json(OUT / "manifest.json", manifest)
    print(encode({"layers": len(records), "features": sum(r["counts"]["output_features"] for r in records),
                  "output_bytes": sum(f["bytes"] for r in records for f in r["outputs"]),
                  "uncompressed_bytes": sum(f["uncompressed_bytes"] for r in records for f in r["outputs"])}))


if __name__ == "__main__":
    main()
