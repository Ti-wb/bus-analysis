#!/usr/bin/env python3
"""Extract Taipei traffic-demand candidates from the repository's OSM snapshot."""

import argparse
from collections import Counter, defaultdict

from pyproj import Transformer
from shapely import orient_polygons, transform
from shapely.geometry import mapping, shape

from traffic_focus_common import MAP, ROOT, boundaries, boundary_sources, district_for, now, output_entry, read, write_csv, write_json
from update_map_osm import same_dimension

OUT = MAP / "traffic-focus/osm"
CATEGORIES = {
    "education": ("學校與大專院校", {"school", "college", "university"}),
    "hospital": ("醫院", {"hospital"}),
    "shopping": ("百貨、商場與市場", {"mall", "department_store", "marketplace"}),
    "culture_sports": ("文教與運動場館", {"stadium", "sports_centre", "sports_hall", "museum", "theatre", "arts_centre", "cinema", "library"}),
    "transport": ("軌道站與轉運設施", {"railway_station", "bus_station", "ferry_terminal", "airport"}),
    "government": ("市政與司法設施", {"town_hall", "courthouse"}),
    "recreation": ("公園與遊憩設施", {"park", "zoo", "theme_park"}),
    "residential": ("具名住宅區候選", {"residential"}),
    "employment": ("具名商業與產業區候選", {"commercial", "industrial"}),
}
CLASS_CATEGORY = {cls: category for category, (_, classes) in CATEGORIES.items() for cls in classes}


def select_category(layer, properties, area_m2, park_min_area_m2):
    cls = properties["feature_class"]
    category = CLASS_CATEGORY.get(cls)
    if layer == "landuse":
        return category if cls in {"residential", "commercial", "industrial"} and properties.get("name") else None
    if category in {"residential", "employment"}:
        return None
    if cls == "park" and (not properties.get("name") or area_m2 is None or area_m2 < park_min_area_m2):
        return None
    return category


def build(park_min_area_m2=10000):
    city, districts = boundaries()
    to_meters = Transformer.from_crs("EPSG:4326", "EPSG:3826", always_xy=True).transform
    manifest = read(MAP / "osm/manifest.json")
    source = manifest["metadata"]["source"]
    markers, areas, inputs = [], [], []
    excluded = Counter()
    for layer in ("pois", "poi_areas", "transport", "transport_areas", "landuse"):
        entry = next(r for r in manifest["records"] if r["dataset_id"] == f"osm_taipei_new_taipei_{layer}")
        for file in entry["outputs"]:
            inputs.append(file["path"])
            doc = read(ROOT / file["path"])
            if doc["metadata"]["source"]["source_updated_at"] != source["source_updated_at"]:
                raise ValueError("Mixed OSM snapshot versions")
            for feature in doc["features"]:
                properties = feature["properties"]
                if properties["feature_class"] not in CLASS_CATEGORY:
                    continue
                original = shape(feature["geometry"])
                if not city.intersects(original):
                    continue
                if original.geom_type == "Point":
                    geom, area = original, None
                else:
                    geom = same_dimension(original.intersection(city), 2)
                    if geom is None:
                        excluded["boundary_touch_without_area"] += 1
                        continue
                    geom = orient_polygons(geom)
                    area = round(transform(geom, to_meters, interleaved=False).area, 2)
                category = select_category(layer, properties, area, park_min_area_m2)
                if category is None:
                    continue
                point = geom if geom.geom_type == "Point" else geom.representative_point()
                district = district_for(point, districts)
                if district is None:
                    raise ValueError(f"No Taipei district for {feature['id']}")
                feature_id = "traffic_osm:" + feature["id"]
                props = {
                    "candidate_id": feature_id, "name": properties.get("name"), "category": category,
                    "category_label": CATEGORIES[category][0], "feature_class": properties["feature_class"],
                    "city_code": "63000", "district_code": district["admin_code"], "district_name": district["name"],
                    "longitude": point.x, "latitude": point.y,
                    "location_method": "source_point" if area is None else "point_on_clipped_surface",
                    "entrance_verified": False, "footprint_area_m2": area,
                    "student_count": None, "bed_count": None, "household_count": None,
                    "daily_visitors": None, "daily_trips": None,
                    "traffic_evidence": "landuse_only" if layer == "landuse" else "facility_type_only",
                    "candidate_scope_status": "named_landuse_requires_site_review" if layer == "landuse" else "osm_facility_requires_verification",
                    "large_community_verified": None,
                    "source_layer": layer, "source_feature_id": feature["id"], "osm_id": properties["osm_id"],
                    "source_updated_at": source["source_updated_at"], "source_file": file["path"],
                    "same_name_group_id": None, "same_name_group_size": 1,
                }
                markers.append({"type": "Feature", "id": feature_id, "properties": props, "geometry": mapping(point)})
                if area is not None:
                    areas.append({"type": "Feature", "id": feature_id, "properties": props, "geometry": mapping(geom)})
    markers.sort(key=lambda f: (f["properties"]["category"], f["properties"]["district_code"], f["properties"]["name"] or "", f["id"]))
    groups = defaultdict(list)
    for feature in markers:
        p = feature["properties"]
        if p["name"]:
            groups[(p["category"], p["name"])].append(p)
    for group in groups.values():
        if len(group) > 1:
            for p in group:
                p.update(same_name_group_id=min(r["candidate_id"] for r in group), same_name_group_size=len(group))
    metadata = {
        "title": "臺北市 OSM 交通需求候選設施標記", "generated_at": now(), "source": source,
        "source_period": source["source_updated_at"], "input_files": inputs, "boundary_sources": boundary_sources(),
        "coordinate_reference_system": "EPSG:4326", "coordinate_order": ["longitude", "latitude"],
        "scope": {"city_code": "63000", "city_name": "臺北市"},
        "grain": "one selected OSM source feature; not one unique facility",
        "units": {"footprint_area_m2": "m2", "student_count": "persons", "bed_count": "beds", "household_count": "households", "daily_visitors": "persons/day", "daily_trips": "trips/day"},
        "area_crs": "EPSG:3826", "park_min_area_m2": park_min_area_m2,
        "counts_by_category": dict(sorted(Counter(f["properties"]["category"] for f in markers).items())),
        "excluded_counts": dict(excluded),
        "limitations": [
            "這是潛在交通需求設施清單，沒有實測人流或交通量，不可據此認定高流量或排列運量。",
            "每列為來源圖徵；同一設施可能有點、面、多棟或多校區。標記與範圍是同一批資料的兩種呈現，不可加總。",
            "same_name_group 僅提示同類別同名圖徵，未自動合併；不同名稱的重複與同名分館仍需核對。",
            "具名住宅區只是候選，未驗證戶數或大型社區資格；未具名住宅區和個別住宅建物未納入。",
            "住宅與商業工業 landuse 包含一般地名、倉庫、機房等，不是已核實的社區名冊或就業集聚區；traffic_evidence=landuse_only。",
            "公園僅保留具名且臺北市內面積達設定門檻者；門檻是研究篩選設定，不是官方分級。",
            "面內代表點不是出入口，區別依代表點判定；跨市面僅保留臺北市內部分。",
            "OSM 有漏標、分類或停業資訊落差；Geofabrik 選集沒有完整 OSM tags，需官方名冊補齊。",
            "ID 只在本快照內有效，osm_id 未含 node/way/relation 類型，不能直接關聯其他資料。",
        ],
    }
    records = []
    for filename, features, title in (("candidates.geojson", markers, metadata["title"]), ("areas.geojson", areas, "臺北市 OSM 交通需求候選設施範圍")):
        doc = {"type": "FeatureCollection", "schema_version": 1, "dataset_id": "taipei_traffic_osm_" + filename.split(".")[0],
               "metadata": {**metadata, "title": title, "record_count": len(features)}, "features": features}
        path = OUT / filename
        write_json(path, doc)
        records.append(output_entry(path, doc))
    write_csv(OUT / "candidates.csv", [f["properties"] for f in markers])
    write_json(OUT / "manifest.json", {"schema_version": 1, "dataset_id": "taipei_traffic_osm_manifest",
               "metadata": {**metadata, "csv_path": "map-data/traffic-focus/osm/candidates.csv", "csv_missing_value": "empty cell", "update_command": "uv run scripts/build_traffic_focus_osm.py"}, "records": records})
    return {"markers": len(markers), "areas": len(areas), "categories": metadata["counts_by_category"]}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--park-min-area-m2", type=float, default=10000)
    args = parser.parse_args()
    if args.park_min_area_m2 < 0:
        parser.error("--park-min-area-m2 must be nonnegative")
    print(build(args.park_min_area_m2))
