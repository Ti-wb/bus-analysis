#!/usr/bin/env python3
"""Download and normalize Taipei social-housing traffic-demand candidates."""

import argparse
from collections import Counter, defaultdict
import math
import re
import subprocess

from shapely.geometry import Point, mapping

from traffic_focus_common import MAP, ROOT, boundaries, boundary_sources, district_for, now, output_entry, read, write_csv, write_json

RAW = MAP / "raw/traffic-focus/housing"
OUT = MAP / "traffic-focus/housing"
URL = "https://hms.udd.gov.taipei/api/BigData/project"
SOURCE_PAGE = "https://data.taipei/dataset/detail?id=659c3565-df41-4f80-915f-95e83071bdcd"


def clean(value):
    return str(value).strip() or None if value is not None else None


def count(value, zero_as_unknown=False):
    text = clean(value)
    if text is None:
        return None
    if not text.isdigit():
        raise ValueError(f"Unexpected count: {value!r}")
    # Zero household values in this feed do not establish that the site has no households.
    return None if zero_as_unknown and int(text) == 0 else int(text)


def coordinates(row):
    lat, lon = clean(row.get("lat")), clean(row.get("lng"))
    if lat is None or lon is None:
        return None
    latitude, longitude = float(lat), float(lon)
    if not all(math.isfinite(v) for v in (latitude, longitude)):
        raise ValueError("Non-finite source coordinate")
    if latitude == 0 or longitude == 0:
        return None
    if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
        raise ValueError("Invalid source coordinate range")
    return Point(longitude, latitude)


def normalize(rows, large_households=300):
    city, districts = boundaries()
    names = {p["name"] for p, _ in districts}
    features, unmapped, excluded = [], [], []
    for index, row in enumerate(rows, 1):
        name = clean(row["name"])
        point = coordinates(row)
        source_district = clean(row["distict"])
        aggregate = bool(re.search(r"[（(]\d+處[）)]", name or ""))
        ancillary = any(term in (name or "") for term in ("行政中心", "行政大樓", "衛福大樓"))
        if point is not None and not city.covers(point):
            excluded.append({"source_row": str(index), "name": name, "source_district": source_district, "reason": "coordinate_outside_taipei"})
            continue
        if point is None and source_district not in names:
            excluded.append({"source_row": str(index), "name": name, "source_district": source_district, "reason": "missing_coordinates_and_no_taipei_district"})
            continue
        district = district_for(point, districts) if point else None
        households = count(row["houseHolds"], zero_as_unknown=True)
        props = {
            "candidate_id": f"taipei_housing:{index}", "name": name,
            "category": "ancillary_facility" if ancillary else "social_housing",
            "category_label": "社宅工程附屬公共設施" if ancillary else "社會住宅", "city_code": "63000",
            "district_code": district["admin_code"] if district else None,
            "district_name": district["name"] if district else None,
            "source_district_name": source_district,
            "district_matches_source": district["name"] == source_district if district else None,
            "location_review_required": district["name"] != source_district if district else True,
            "address": clean(row["address"]), "longitude": point.x if point and not aggregate else None,
            "latitude": point.y if point and not aggregate else None,
            "record_kind": "multi_project_aggregate" if aggregate else "ancillary_facility" if ancillary else "project",
            "location_status": "aggregate_not_a_site" if aggregate else "source_point" if point else "missing_coordinates",
            "entrance_verified": False, "planned_households": households,
            "source_households_zero": clean(row["houseHolds"]) == "0",
            "meets_household_threshold": households >= large_households if households is not None and not aggregate and not ancillary else None,
            "reported_persons": count(row["persons"]), "floors_description": clean(row["floors"]),
            "project_progress": clean(row["progress"]),
            "is_completed": row["progress"] == "已完工" if clean(row["progress"]) else None,
            "occupancy_verified": False, "daily_trips": None, "traffic_evidence": "planned_capacity_only",
            "source_row": str(index), "source_updated_at": None, "source_url": URL,
            "same_location_group_id": None, "same_location_group_size": 1,
        }
        if point is not None and not aggregate:
            if district is None:
                raise ValueError(f"No district for housing row {index}")
            features.append({"type": "Feature", "id": props["candidate_id"], "properties": props, "geometry": mapping(point)})
        else:
            unmapped.append(props)
    groups = defaultdict(list)
    for feature in features:
        p = feature["properties"]
        groups[(p["longitude"], p["latitude"])].append(p)
    for group in groups.values():
        if len(group) > 1:
            for p in group:
                p.update(same_location_group_id=min(r["candidate_id"] for r in group), same_location_group_size=len(group))
    return features, unmapped, excluded


def build(offline=False, large_households=300):
    path = RAW / "projects.json"
    receipt_path = RAW / "download.json"
    if not offline:
        RAW.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".json.part")
        try:
            subprocess.run(["curl", "-fLsS", "--max-time", "60", "--max-filesize", "10485760", URL, "-o", str(temporary)], check=True)
            candidate = read(temporary)
            if not isinstance(candidate, list) or not candidate:
                raise ValueError("Empty or changed housing API response")
            normalize(candidate, large_households)  # Check before replacing the previous raw snapshot.
            temporary.replace(path)
            write_json(receipt_path, {"download_url": URL, "downloaded_at": now()})
        finally:
            temporary.unlink(missing_ok=True)
    rows = read(path)
    features, unmapped, excluded = normalize(rows, large_households)
    receipt = read(receipt_path) if receipt_path.exists() else {"downloaded_at": None}
    source = {"source_page": SOURCE_PAGE, "download_url": URL, "source_updated_at": None,
              "downloaded_at": receipt["downloaded_at"], "raw_path": str(path.relative_to(ROOT)),
              "provider": "臺北市政府都市發展局", "license": "政府資料開放授權條款-第1版",
              "license_url": "https://data.gov.tw/license", "attribution": "臺北市政府都市發展局"}
    metadata = {
        "title": "臺北市社宅工程及附屬設施交通需求候選", "generated_at": now(), "source": source,
        "source_period": None, "boundary_sources": boundary_sources(),
        "coordinate_reference_system": "EPSG:4326", "coordinate_order": ["longitude", "latitude"],
        "source_crs_note": "來源僅標明經緯度，未明示 datum；按 WGS84 使用，已核對臺北市界與行政區，仍屬推定。",
        "scope": {"city_code": "63000", "city_name": "臺北市"},
        "grain": "one source project row; aggregate rows kept separately and never mapped",
        "large_households_threshold": large_households,
        "units": {"planned_households": "households", "reported_persons": "persons", "daily_trips": "trips/day"},
        "source_row_count": len(rows), "mapped_count": len(features), "unmapped_count": len(unmapped),
        "excluded_count": len(excluded), "excluded_rows": excluded,
        "mapped_progress_counts": dict(sorted(Counter(f["properties"]["project_progress"] for f in features).items())),
        "mapped_kind_counts": dict(sorted(Counter(f["properties"]["record_kind"] for f in features).items())),
        "district_conflict_count": sum(f["properties"]["district_matches_source"] is False for f in features),
        "mapped_threshold_count": sum(f["properties"]["meets_household_threshold"] is True for f in features),
        "mapped_completed_threshold_count": sum(f["properties"]["meets_household_threshold"] is True and f["properties"]["is_completed"] for f in features),
        "limitations": [
            "houseHolds 是規劃戶數，不是實際入住戶數；零戶數意義未明，改 null 並用 source_households_zero 保留識別。",
            "persons 原欄名為居住人口，但未提供統計期間或說明是實住或估計；只保留 reported_persons，不當作實住人口或旅次。",
            "完工不代表已入住，施工與規劃案件不可當作現況人流；工程進度為來源原值。",
            "規劃戶數門檻僅為可調的研究篩選值，非官方大型社區定義；合計列不參與門檻判定。",
            "零值與空座標不定位；多案合計列沒有單一位置，另存 unmapped.json。新北市案件按座標排除。",
            "API 無資料更新時間或穩定案號；下載時間不是更新時間，source_row 與 ID 僅在本快照內有效。",
            "與 OSM 社宅可能重複，兩來源尚未對照，不能相加當作不重複社區數。",
            "名稱含行政中心、行政大樓、衛福大樓者標為附屬設施，不視為住宅社區；此用途分類根據來源名稱判讀。",
            "已核對座標範圍；尚未逐一核對實際入口、建物位置與營運現況。",
            "來源行政區與座標空間判定衝突時保留兩者並標 location_review_required=true；該標記不宜直接用於距離或服務涵蓋評估。",
            "不同工程案可能共用來源座標，以 same_location_group 提示；仍保留不同案列，地圖需讓重疊標記可被選取。",
        ],
    }
    geojson = {"type": "FeatureCollection", "schema_version": 1, "dataset_id": "taipei_traffic_social_housing",
               "metadata": {**metadata, "record_count": len(features)}, "features": features}
    unresolved = {"schema_version": 1, "dataset_id": "taipei_traffic_social_housing_unmapped",
                  "metadata": {**metadata, "title": "臺北市社宅未定位案件與多案合計列", "record_count": len(unmapped)}, "records": unmapped}
    records = []
    for filename, doc in (("candidates.geojson", geojson), ("unmapped.json", unresolved)):
        target = OUT / filename
        write_json(target, doc)
        records.append(output_entry(target, doc))
    write_csv(OUT / "candidates.csv", [f["properties"] for f in features] + unmapped)
    write_json(OUT / "manifest.json", {"schema_version": 1, "dataset_id": "taipei_traffic_housing_manifest",
               "metadata": {**metadata, "csv_path": "map-data/traffic-focus/housing/candidates.csv", "csv_missing_value": "empty cell", "update_command": "uv run scripts/update_traffic_housing.py"}, "records": records})
    return {k: metadata[k] for k in ("source_row_count", "mapped_count", "unmapped_count", "excluded_count", "mapped_threshold_count", "mapped_completed_threshold_count")}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--large-households", type=int, default=300)
    args = parser.parse_args()
    if args.large_households <= 0:
        parser.error("--large-households must be positive")
    print(build(args.offline, args.large_households))
