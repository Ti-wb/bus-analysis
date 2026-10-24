#!/usr/bin/env python3
"""Build a GIS layer catalogue with paths relative to map-data/."""

import gzip
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAP = ROOT / "map-data"


def read(path):
    opener = gzip.open if path.name.endswith(".gz") else open
    with opener(path, "rt", encoding="utf-8") as f:
        return json.load(f)


def source_periods(doc):
    metadata = doc["metadata"]
    sources = metadata.get("sources", []) + [metadata.get("source", {})]
    periods = [metadata.get("source_period")]
    periods.extend(source.get("source_period") or source.get("source_updated_at") for source in sources)
    return sorted({period for period in periods if period})


def review_matches_source(doc, review):
    periods = source_periods(doc)
    if periods:
        return review.get("source_periods", []) == periods
    # A download receipt identifies this snapshot, never its source update date.
    acquired_at = doc["metadata"].get("source", {}).get("downloaded_at")
    return bool(acquired_at) and review.get("source_downloaded_at") == acquired_at


def build():
    reviews = {}
    for path in sorted((MAP / "quality").glob("*_review.json")):
        for record in read(path).get("records", []):
            file = record.get("path") or record.get("file")
            if file:
                reviews[file] = {"status": record.get("status", "pending"),
                                 "record_count": record.get("record_count"),
                                 "source_periods": record.get("source_periods", []),
                                 "source_downloaded_at": record.get("source_downloaded_at"),
                                 "report": str(path.relative_to(MAP))}
    admin = read(MAP / "admin/manifest.json")
    osm = read(MAP / "osm/manifest.json")
    records = []
    groups = [("admin", admin), ("osm", osm)]
    for group in ("traffic-focus/osm", "traffic-focus/housing"):
        if (MAP / group / "manifest.json").exists():
            groups.append((group, read(MAP / group / "manifest.json")))
    for group, manifest in groups:
        for entry in manifest["records"]:
            outputs = entry.get("outputs", [{"path": entry.get("path")}])
            files = []
            periods = set()
            for output in outputs:
                path = (ROOT / output["path"]).resolve()
                if not path.is_relative_to(MAP) or not path.is_file():
                    raise ValueError(f"Missing/out-of-scope map output: {output['path']}")
                doc = read(path)
                rows = doc.get("features", doc.get("records"))
                if not isinstance(rows, list):
                    raise ValueError(f"Map output has no records: {path}")
                expected = output.get("record_count", entry.get("feature_count", entry.get("record_count")))
                if len(rows) != expected or path.stat().st_size != output.get("bytes", entry.get("bytes")):
                    raise ValueError(f"Manifest count/size differs from map output: {path}")
                file_periods = source_periods(doc)
                periods.update(file_periods)
                review = reviews.get(output["path"], {"status": "pending"})
                if review.get("report"):
                    if not review_matches_source(doc, review):
                        review = {**review, "status": "pending_source_version"}
                if review.get("record_count") is not None and review["record_count"] != len(rows):
                    review = {**review, "status": "pending_changed_count"}
                files.append({"path": str(path.relative_to(MAP)), "encoding": output.get("encoding", "utf-8"),
                              "record_count": len(rows), "bytes": path.stat().st_size,
                              "uncompressed_bytes": output.get("uncompressed_bytes", path.stat().st_size),
                              "bbox": doc.get("bbox"), "independent_review": review})
                title = doc["metadata"].get("title", "行政區階層與標示點索引")
                kind = "geojson" if doc.get("type") == "FeatureCollection" else "json"
            if all(file["independent_review"]["status"].startswith("pass") for file in files):
                status = "passed"
            else:
                status = "pending_or_needs_correction"
            records.append({"dataset_id": entry["dataset_id"], "title": title, "group": group, "format": kind,
                            "record_count": sum(file["record_count"] for file in files), "files": files,
                            "source_periods": sorted(periods),
                            "manifest": f"{group}/manifest.json", "independent_review_status": status})
    bounds = osm["metadata"]["bbox"]
    return {"schema_version": 1, "dataset_id": "taipei_new_taipei_map_layers", "metadata": {
        "title": "雙北 GIS 底圖、行政界與臺北市交通需求候選設施", "coordinate_reference_system": "EPSG:4326",
        "coordinate_order": ["longitude", "latitude"], "path_base": "map-data/",
        "record_count": len(records), "bbox": bounds,
        "fit_bounds": [[bounds[0], bounds[1]], [bounds[2], bounds[3]]],
        "admin_city_codes": ["63000", "65000"],
        "total_layer_file_bytes": sum(f["bytes"] for r in records for f in r["files"]),
        "attributions": [admin["metadata"]["license"], {"name": "ODbL-1.0", "url": osm["metadata"]["license_url"],
                          "attribution": osm["metadata"]["attribution"]}]
                        + [{"name": m["metadata"]["source"]["license"],
                            "url": m["metadata"]["source"]["license_url"],
                            "attribution": m["metadata"]["source"]["attribution"]}
                           for g, m in groups if g == "traffic-focus/housing"],
        "notes": ["files與manifest路徑皆相對map-data/；同圖層的所有files合起來才是完整圖層。",
                  "OSM採gzip壓縮GeoJSON，可使用load-geojson.mjs或先解壓；依畫面需要載入圖層。",
                  "各圖層可能描述同一設施，不可把所有圖層筆數相加當成不重複設施數。",
                  "行政界與OSM分別保留官方版本，使用限制與來源見manifest及各檔metadata。"]}, "records": records}


if __name__ == "__main__":
    doc = build()
    path = MAP / "catalog.json"
    part = path.with_suffix(".json.part")
    part.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    part.replace(path)
    print(json.dumps({"datasets": len(doc["records"]), "files": sum(len(r["files"]) for r in doc["records"]),
                      "layer_mib": round(doc["metadata"]["total_layer_file_bytes"] / 1024**2, 2),
                      "pending_reviews": [r["dataset_id"] for r in doc["records"] if r["independent_review_status"] != "passed"]}))
