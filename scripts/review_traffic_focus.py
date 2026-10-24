#!/usr/bin/env python3
"""Independent source-to-output audit; --write saves the quality report."""

import argparse
from collections import Counter, defaultdict
import csv
from datetime import datetime
import gzip
import json
from pathlib import Path
import re
import sys
from zoneinfo import ZoneInfo

from pyproj import Transformer
from shapely import transform
from shapely.geometry import Point, shape

ROOT = Path(__file__).resolve().parents[1]
MAP = ROOT / "map-data"


def read(path):
    opener = gzip.open if str(path).endswith(".gz") else open
    with opener(path, "rt", encoding="utf-8") as stream:
        return json.load(stream)


def check(condition, message, failures):
    if not condition:
        failures.append(message)


def review():
    city = next(shape(f["geometry"]) for f in read(MAP / "admin/cities.geojson")["features"]
                if f["properties"]["admin_code"] == "63000")
    districts = [(f["properties"], shape(f["geometry"]))
                 for f in read(MAP / "admin/districts.geojson")["features"]
                 if f["properties"]["city_code"] == "63000"]
    project = Transformer.from_crs(4326, 3826, always_xy=True).transform
    marker_doc = read(MAP / "traffic-focus/osm/candidates.geojson")
    area_doc = read(MAP / "traffic-focus/osm/areas.geojson")
    housing_doc = read(MAP / "traffic-focus/housing/candidates.geojson")
    unmapped_doc = read(MAP / "traffic-focus/housing/unmapped.json")
    docs = {"osm/candidates.geojson": marker_doc, "osm/areas.geojson": area_doc,
            "housing/candidates.geojson": housing_doc, "housing/unmapped.json": unmapped_doc}
    failures = {name: [] for name in docs}
    for name, doc in docs.items():
        rows = doc.get("features", doc.get("records"))
        check(doc["schema_version"] == 1 and isinstance(doc["dataset_id"], str), "schema envelope", failures[name])
        check(len(rows) == doc["metadata"]["record_count"], "metadata count", failures[name])
        ids = [r.get("properties", r)["candidate_id"] for r in rows]
        check(len(set(ids)) == len(ids) and all(isinstance(i, str) for i in ids), "unique string candidate IDs", failures[name])
        for row in rows:
            props = row.get("properties", row)
            check(all(re.fullmatch(r"[a-z][a-z0-9_]*", k) for k in props), "snake_case fields", failures[name])
            check(props["daily_trips"] is None and props["entrance_verified"] is False, "unmeasured traffic/entrance", failures[name])
            if "geometry" not in row:
                check(props["longitude"] is None and props["latitude"] is None, "unmapped null location", failures[name])
                continue
            geom = shape(row["geometry"])
            check(geom.is_valid and not geom.is_empty, "valid geometry " + row["id"], failures[name])
            check(city.buffer(1e-10).covers(geom) if geom.geom_type != "Point" else city.covers(geom),
                  "Taipei boundary " + row["id"], failures[name])
            point = Point(props["longitude"], props["latitude"])
            check(geom.covers(point), "marker within source footprint " + row["id"], failures[name])
            district_matches = sorted(p["admin_code"] for p, g in districts if g.covers(point))
            check(district_matches and props["district_code"] == district_matches[0], "district spatial assignment " + row["id"], failures[name])
            for poly in ([geom] if geom.geom_type == "Polygon" else geom.geoms if geom.geom_type == "MultiPolygon" else []):
                check(poly.exterior.is_ccw and all(not r.is_ccw for r in poly.interiors),
                      "RFC 7946 ring orientation " + row["id"], failures[name])

    # Independently select source features, without calling producer helpers.
    classes = {
        "education": {"school", "college", "university"}, "hospital": {"hospital"},
        "shopping": {"mall", "department_store", "marketplace"},
        "culture_sports": {"stadium", "sports_centre", "sports_hall", "museum", "theatre", "arts_centre", "cinema", "library"},
        "transport": {"railway_station", "bus_station", "ferry_terminal", "airport"},
        "government": {"town_hall", "courthouse"}, "recreation": {"park", "zoo", "theme_park"},
        "residential": {"residential"}, "employment": {"commercial", "industrial"},
    }
    class_category = {cls: category for category, values in classes.items() for cls in values}
    expected = {}
    osm_manifest = read(MAP / "osm/manifest.json")
    snapshot = osm_manifest["metadata"]["source"]["source_updated_at"]
    source_file_count = 0
    for entry in osm_manifest["records"]:
        layer = entry["dataset_id"].removeprefix("osm_taipei_new_taipei_")
        if layer not in {"pois", "poi_areas", "transport", "transport_areas", "landuse"}:
            continue
        for output in entry["outputs"]:
            source_file_count += 1
            doc = read(ROOT / output["path"])
            for feature in doc["features"]:
                props = feature["properties"]
                cls = props["feature_class"]
                if cls not in class_category:
                    continue
                geom = shape(feature["geometry"])
                if not city.intersects(geom):
                    continue
                clipped = geom.intersection(city)
                if geom.geom_type != "Point" and clipped.area == 0:
                    continue
                area = None if geom.geom_type == "Point" else round(transform(clipped, project, interleaved=False).area, 2)
                if layer == "landuse":
                    if cls not in {"residential", "commercial", "industrial"} or not props.get("name"):
                        continue
                elif cls in {"residential", "commercial", "industrial"}:
                    continue
                if cls == "park" and (not props.get("name") or area is None or area < marker_doc["metadata"]["park_min_area_m2"]):
                    continue
                expected["traffic_osm:" + feature["id"]] = (feature, clipped, area, class_category[cls], output["path"])
    markers = {f["id"]: f for f in marker_doc["features"]}
    areas = {f["id"]: f for f in area_doc["features"]}
    check(set(markers) == set(expected), "all eligible source features included once", failures["osm/candidates.geojson"])
    check(set(areas) == {i for i, (_, _, area, _, _) in expected.items() if area is not None},
          "all selected source polygons included once", failures["osm/areas.geojson"])
    for ident, feature in markers.items():
        if ident not in expected:
            continue
        source, clipped, area, category, source_file = expected[ident]
        p = feature["properties"]
        check((p["name"], p["feature_class"], p["osm_id"], p["category"], p["source_file"], p["source_updated_at"], p["footprint_area_m2"])
              == (source["properties"]["name"], source["properties"]["feature_class"], source["properties"]["osm_id"], category, source_file, snapshot, area),
              "source fields " + ident, failures["osm/candidates.geojson"])
        check(all(p[k] is None for k in ("student_count", "bed_count", "household_count", "daily_visitors", "large_community_verified")),
              "no unsupported capacity " + ident, failures["osm/candidates.geojson"])
        is_landuse = p["source_layer"] == "landuse"
        check(p["traffic_evidence"] == ("landuse_only" if is_landuse else "facility_type_only") and
              p["candidate_scope_status"] == ("named_landuse_requires_site_review" if is_landuse else "osm_facility_requires_verification"),
              "landuse interpretation " + ident, failures["osm/candidates.geojson"])
        check(clipped.covers(shape(feature["geometry"])), "source location " + ident, failures["osm/candidates.geojson"])
        if ident in areas:
            check(shape(areas[ident]["geometry"]).equals(clipped), "source clipped geometry " + ident, failures["osm/areas.geojson"])
            check(areas[ident]["properties"] == p, "marker/area properties " + ident, failures["osm/areas.geojson"])
    same_names = defaultdict(list)
    for f in markers.values():
        p = f["properties"]
        if p["name"]:
            same_names[(p["category"], p["name"])].append(p)
    for group in same_names.values():
        for p in group:
            check(p["same_name_group_size"] == len(group) and
                  p["same_name_group_id"] == (min(r["candidate_id"] for r in group) if len(group) > 1 else None),
                  "same-name group " + p["candidate_id"], failures["osm/candidates.geojson"])

    raw = read(MAP / "raw/traffic-focus/housing/projects.json")
    receipt = read(MAP / "raw/traffic-focus/housing/download.json")
    housing = {f["properties"]["source_row"]: f["properties"] for f in housing_doc["features"]}
    unmapped = {p["source_row"]: p for p in unmapped_doc["records"]}
    excluded = {p["source_row"]: p for p in housing_doc["metadata"]["excluded_rows"]}
    check(not(set(housing) & set(unmapped) or set(housing) & set(excluded) or set(unmapped) & set(excluded)),
          "disjoint row outcomes", failures["housing/candidates.geojson"])
    check(set(housing) | set(unmapped) | set(excluded) == {str(i) for i in range(1, len(raw) + 1)},
          "all raw rows accounted for", failures["housing/candidates.geojson"])
    for index, source in enumerate(raw, 1):
        ident = str(index)
        if ident in excluded:
            check(not city.covers(Point(float(source["lng"]), float(source["lat"]))),
                  "excluded coordinate outside city " + ident, failures["housing/candidates.geojson"])
            continue
        props = housing.get(ident, unmapped.get(ident))
        if props is None:
            continue
        target = "housing/candidates.geojson" if ident in housing else "housing/unmapped.json"
        for source_key, output_key in (("name", "name"), ("distict", "source_district_name"), ("progress", "project_progress"), ("address", "address"), ("floors", "floors_description")):
            check(props[output_key] == (str(source[source_key]).strip() or None), "source text " + ident + ":" + output_key, failures[target])
        for source_key, output_key in (("houseHolds", "planned_households"), ("persons", "reported_persons")):
            expected_count = int(source[source_key]) if source[source_key] else None
            if source_key == "houseHolds" and expected_count == 0:
                expected_count = None
            check(props[output_key] == expected_count,
                  "source capacity " + ident + ":" + output_key, failures[target])
        check(props["is_completed"] == (source["progress"] == "已完工") and props["occupancy_verified"] is False,
              "completion and occupancy " + ident, failures[target])
        aggregate = bool(re.search(r"[（(]\d+處[）)]", source["name"]))
        if aggregate:
            check(ident in unmapped and props["record_kind"] == "multi_project_aggregate" and props["meets_household_threshold"] is None,
                  "aggregate not a site " + ident, failures[target])
        elif ident in housing:
            check((props["longitude"], props["latitude"]) == (float(source["lng"]), float(source["lat"])),
                  "source coordinates " + ident, failures[target])
            check(props["location_review_required"] == (props["district_name"] != props["source_district_name"]),
                  "district conflict requires review " + ident, failures[target])
        else:
            check(not source["lat"] or not source["lng"] or float(source["lat"]) == 0 or float(source["lng"]) == 0,
                  "missing coordinate source " + ident, failures[target])
        expected_threshold = (props["planned_households"] >= housing_doc["metadata"]["large_households_threshold"]
                              if props["planned_households"] is not None and props["record_kind"] == "project" else None)
        check(props["meets_household_threshold"] == expected_threshold, "household threshold " + ident, failures[target])
    for name in ("housing/candidates.geojson", "housing/unmapped.json"):
        metadata = docs[name]["metadata"]
        check(metadata["source_period"] is None and metadata["source"]["source_updated_at"] is None,
              "unknown source update date stays null", failures[name])
        check(metadata["source"]["downloaded_at"] == receipt["downloaded_at"], "source receipt version", failures[name])
    for group in ("osm", "housing"):
        manifest = read(MAP / "traffic-focus" / group / "manifest.json")
        for entry in manifest["records"]:
            name = entry["path"].removeprefix("map-data/traffic-focus/")
            check(entry["record_count"] == len(docs[name].get("features", docs[name].get("records"))) and
                  entry["bytes"] == (ROOT / entry["path"]).stat().st_size, "manifest count/size", failures[name])
        with (MAP / "traffic-focus" / group / "candidates.csv").open(encoding="utf-8-sig", newline="") as stream:
            csv_rows = list(csv.DictReader(stream))
        expected_csv = [f["properties"] for f in docs[group + "/candidates.geojson"]["features"]]
        if group == "housing":
            expected_csv += list(unmapped.values())
        check(len(csv_rows) == len(expected_csv) and all(
            row == {key: "" if value is None else str(value) for key, value in expected_row.items()}
            for row, expected_row in zip(csv_rows, expected_csv)), "CSV parity and empty-cell nulls", failures[group + "/candidates.geojson"])

    duplicate_locations = defaultdict(list)
    for props in housing.values():
        duplicate_locations[(props["longitude"], props["latitude"])].append(props)
    for group in duplicate_locations.values():
        for p in group:
            check(p["same_location_group_size"] == len(group) and
                  p["same_location_group_id"] == (min(r["candidate_id"] for r in group) if len(group) > 1 else None),
                  "same-location group " + p["candidate_id"], failures["housing/candidates.geojson"])
    diagnostics = {
        "osm_source_files_checked": source_file_count,
        "osm_category_counts": dict(sorted(Counter(f["properties"]["category"] for f in markers.values()).items())),
        "osm_missing_name_count": sum(f["properties"]["name"] is None for f in markers.values()),
        "osm_same_name_groups": sum(len(group) > 1 for group in same_names.values()),
        "osm_same_name_affected_features": sum(len(group) for group in same_names.values() if len(group) > 1),
        "housing_source_rows": len(raw), "housing_mapped": len(housing), "housing_unmapped": len(unmapped), "housing_excluded": len(excluded),
        "housing_mapped_kind_counts": dict(Counter(p["record_kind"] for p in housing.values())),
        "housing_progress_counts": dict(Counter(p["project_progress"] for p in housing.values())),
        "housing_threshold_count": sum(p["meets_household_threshold"] is True for p in housing.values()),
        "housing_completed_threshold_count": sum(p["meets_household_threshold"] is True and p["is_completed"] for p in housing.values()),
        "housing_mapped_planned_households_sum_for_reconciliation_only": sum(p["planned_households"] or 0 for p in housing.values()),
        "housing_district_conflicts": [{k: p[k] for k in ("candidate_id", "name", "source_district_name", "district_name")} for p in housing.values() if p["district_matches_source"] is False],
        "housing_shared_source_coordinates": [{"coordinates": list(coords), "names": [p["name"] for p in group]} for coords, group in duplicate_locations.items() if len(group) > 1],
    }
    check(marker_doc["metadata"]["counts_by_category"] == diagnostics["osm_category_counts"],
          "metadata category counts", failures["osm/candidates.geojson"])
    for key, expected_value in (("source_row_count", len(raw)), ("mapped_count", len(housing)),
                                ("unmapped_count", len(unmapped)), ("excluded_count", len(excluded)),
                                ("mapped_kind_counts", diagnostics["housing_mapped_kind_counts"]),
                                ("mapped_progress_counts", diagnostics["housing_progress_counts"]),
                                ("mapped_threshold_count", diagnostics["housing_threshold_count"]),
                                ("mapped_completed_threshold_count", diagnostics["housing_completed_threshold_count"]),
                                ("district_conflict_count", len(diagnostics["housing_district_conflicts"]))):
        check(housing_doc["metadata"][key] == expected_value, "metadata " + key, failures["housing/candidates.geojson"])
    limitations = [
        "OSM 每筆是來源圖徵；點、面、多棟、同名或跨分類重複尚未實體合併，圖層筆數不是不重複設施數。",
        "住宅土地使用包括金瑞里、中八仙、下八仙等地名；809 筆不等於社區數，更不等於大型社區數。",
        "商業與工業土地使用包括變電所、抽水站、機房及倉庫；268 筆不等於高人流或就業集聚設施。",
        "社宅規劃戶數、來源人口欄與工程進度均不證明實際入住或旅次；未定位與合計列不作地點。",
        "來源未提供社宅更新日期；使用下載收據識別受審快照，沒有把下載時間當資料更新時間。",
        "圖形與市界驗證不代表逐一核對實際入口、營運情況、完整設施名冊或實測交通量。",
    ]
    records = []
    for name, doc in docs.items():
        source = doc["metadata"]["source"]
        records.append({"path": "map-data/traffic-focus/" + name,
                        "record_count": len(doc.get("features", doc.get("records"))),
                        "status": "needs_correction" if failures[name] else "pass_with_source_limitations",
                        "source_periods": [snapshot] if name.startswith("osm/") else [],
                        "source_downloaded_at": source["downloaded_at"],
                        "reviewed_generated_at": doc["metadata"]["generated_at"],
                        "checks": {"failures": failures[name], "failure_count": len(failures[name]),
                                   "source_and_geometry_reconciled": not failures[name]},
                        "limitations": limitations[:3] if name.startswith("osm/") else limitations[3:]})
    return {"schema_version": 1, "dataset_id": "taipei_traffic_focus_independent_review",
            "metadata": {"reviewer_agent": "/root/review_traffic_focus", "independent_of_producer": True,
                         "reviewed_at": datetime.now(ZoneInfo("Asia/Taipei")).isoformat(timespec="seconds"),
                         "status": "needs_correction" if any(failures.values()) else "pass_with_source_limitations",
                         "record_count": len(records),
                         "method": "Independent source selection, field reconciliation, Shapely geometry/city/district checks, projected area, RFC 7946 rings, CSV and manifest parity; no producer helper imports.",
                         "script": "scripts/review_traffic_focus.py", "polygon_boundary_tolerance_degrees": 1e-10,
                         "diagnostics": diagnostics,
                         "housing_source_page_evidence": {
                             "checked_on": "2026-10-24", "evidence_location": "Official dataset page HTML and embedded Nuxt JSON",
                             "license_name": "依政府資料開放授權條款-第1版",
                             "license_id": "4550e68f-6812-4a15-a2e2-f954eee779c5",
                             "source_field_descriptions": {"houseHolds": "規劃戶數", "persons": "居住人口"},
                             "note": "Catalogue page timestamps and daily refresh frequency do not establish the API row update date."
                         },
                         "source_pages_checked": ["https://data.taipei/dataset/detail?id=659c3565-df41-4f80-915f-95e83071bdcd", "https://hms.udd.gov.taipei/api/BigData/project", "https://www.rfc-editor.org/rfc/rfc7946"]},
            "records": records}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    report = review()
    if args.write:
        path = MAP / "quality/traffic_focus_review.json"
        path.write_text(json.dumps(report, ensure_ascii=False, allow_nan=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": report["metadata"]["status"], "diagnostics": report["metadata"]["diagnostics"],
                      "failures": {r["path"]: r["checks"]["failures"][:5] for r in report["records"]}}, ensure_ascii=False, indent=2))
    sys.exit(1 if report["metadata"]["status"] == "needs_correction" else 0)
