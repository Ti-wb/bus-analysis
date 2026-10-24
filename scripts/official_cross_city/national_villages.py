"""Audit all national village identities; retain authoritative northern geometry.

The RIS monthly roster is independent of the population CSV. Every SHP layer in
the NLSC distribution is accounted for, including the undated Sanhe supplement.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import date
import gzip
from io import BytesIO
import json
from pathlib import Path
import re
from urllib.parse import unquote, urljoin
from zipfile import ZipFile

from .boundary_evidence import parse_ris, verify_alias, VILLAGE_URL
from .common import ROOT, Links, OGDL, fetch, metadata, validate_month, write_json
from .village_age import write_gzip

NAMESPACE = "national_village_age"
RIS_PAGE = "https://www.ris.gov.tw/documents/html/5/1/167.html"
BOUNDARY_PAGE = "https://data.gov.tw/dataset/7438"
NORTHERN_COUNTIES = {"63000", "65000", "10017"}


def read_json(path):
    with (gzip.open(path, "rt", encoding="utf-8") if path.suffix == ".gz" else path.open(encoding="utf-8")) as stream:
        return json.load(stream)


def archive_layers(path):
    """An extra SHP is never silently discarded or assigned the main vintage."""
    import shapefile
    from pyproj import CRS
    from shapely.geometry import shape
    rows, layers, seen = [], [], set()
    with ZipFile(path) as archive:
        stems = sorted(name[:-4] for name in archive.namelist() if name.lower().endswith(".shp"))
        dated = [stem for stem in stems if re.fullmatch(r"VILLAGE_NLSC_[0-9]{7}", stem)]
        if len(dated) != 1 or set(stems) - {dated[0], "Village_Sanhe"}:
            raise ValueError("Unexpected NLSC SHP layers; review every layer before normalization")
        for stem in stems:
            matched = re.fullmatch(r"VILLAGE_NLSC_([0-9]{3})([0-9]{2})([0-9]{2})", stem)
            vintage = date(int(matched[1]) + 1911, int(matched[2]), int(matched[3])).isoformat() if matched else None
            crs = CRS.from_wkt(archive.read(stem + ".prj").decode("utf-8-sig"))
            if crs.to_epsg() != 3824:
                raise ValueError("Each NLSC layer must independently declare EPSG:3824")
            reader = shapefile.Reader(**{ext: BytesIO(archive.read(stem + "." + ext)) for ext in ("shp", "shx", "dbf")}, encoding="utf-8")
            count = 0
            for item in reader.iterShapeRecords():
                p = item.record.as_dict()
                code = p["VILLCODE"]
                valid_code = bool(re.fullmatch(r"[0-9]{11}", code) or (
                    p["NOTE"] == "未編定村里" and not p["VILLNAME"]
                    and re.fullmatch(r"[0-9]{8}[A-Z][0-9]{2}", code)))
                if (not valid_code or code in seen
                        or p["TOWNCODE"] != code[:8] or p["COUNTYCODE"] != code[:5]):
                    raise ValueError("Duplicate, malformed or conflicting NLSC administrative identity")
                geometry = shape(item.shape.__geo_interface__)
                if geometry.is_empty or not geometry.is_valid or geometry.geom_type not in {"Polygon", "MultiPolygon"}:
                    raise ValueError(f"Invalid source geometry: {stem}/{code}")
                seen.add(code)
                rows.append({"area_id": code, "district_id": p["TOWNCODE"], "county_code": p["COUNTYCODE"],
                             "county_name": p["COUNTYNAME"], "district_name": p["TOWNNAME"],
                             "village_name": p["VILLNAME"] or None, "note": p["NOTE"] or None,
                             "boundary_layer": stem, "boundary_vintage": vintage, "geometry": geometry})
                count += 1
            layers.append({"layer": stem, "feature_count": count, "source_crs": "EPSG:3824",
                           "boundary_vintage": vintage, "same_archive_distribution": True,
                           "vintage_evidence": "dated SHP filename" if matched else "unknown; ZIP member timestamps are not geometry dates"})
    return rows, layers


def supplemental_conflicts(boundaries):
    """Find every positive-area overlap with an undated supplemental polygon."""
    from pyproj import CRS, Transformer
    from shapely import STRtree, transform
    main = [row for row in boundaries if row["boundary_vintage"] is not None]
    tree = STRtree([row["geometry"] for row in main])
    conflicts, records = set(), []
    for row in boundaries:
        if row["boundary_vintage"] is not None:
            continue
        geometry = row["geometry"]
        centre = geometry.centroid
        # A local equal-area projection works at the supplement, including offshore.
        local = CRS.from_proj4(f"+proj=laea +lat_0={centre.y} +lon_0={centre.x} +datum=WGS84 +units=m")
        project = Transformer.from_crs(3824, local, always_xy=True).transform
        for index in tree.query(geometry, predicate="intersects"):
            other = main[int(index)]
            intersection = geometry.intersection(other["geometry"])
            area = transform(intersection, project, interleaved=False).area
            if area > 0.01:
                conflicts.update((row["area_id"], other["area_id"]))
                records.append({"supplement_area_id": row["area_id"], "main_area_id": other["area_id"],
                                "intersection_area_m2": round(area, 2), "geometry_eligible": False})
    return conflicts, records


def reconcile(population, roster, boundaries, layers, month):
    """Compare sets, parents and literal names; retain every unmatched member."""
    from ..build_age_catchments import AGE_GROUPS
    if population.get("dataset_id") != "moi_village_population_age_monthly" or population["metadata"]["period"] != month:
        raise ValueError("Population dataset/month differs from requested roster month")
    counts = {}
    for row in population["records"]:
        if row["sex"] != "total":
            continue
        code = row["area_id"]
        if code in counts or row["period"] != month or len(row["age_counts"]) != 101:
            raise ValueError("Invalid or duplicate population village")
        counts[code] = row
    by_code = {row["area_id"]: row for row in boundaries}
    if len(by_code) != len(boundaries):
        raise ValueError("Duplicate boundary identity")
    conflicts, overlap_records = supplemental_conflicts(boundaries)
    records, features = [], []
    for code in sorted(counts.keys() | roster.keys() | by_code.keys()):
        p, r, b = counts.get(code), roster.get(code), by_code.get(code)
        roster_status = ("missing_population" if p is None else "missing_roster" if r is None else
                         "matched_code_parent_and_name" if p["district_id"] == r["district_code"] and p["village_name"] == r["village_name"] else "roster_identity_mismatch")
        status, alias = "missing_boundary", None
        if b is not None:
            if p is None:
                status = "unassigned" if b["note"] == "未編定村里" else "missing_population"
            elif (b["district_id"] != p["district_id"] or b["county_name"] != p["county_name"]
                  or b["county_name"] + b["district_name"] != p["area_name"]):
                status = "parent_mismatch"
            elif b["village_name"] == p["village_name"]:
                status = "verified"
            else:
                alias = verify_alias(code, p["village_name"], b["village_name"], b["district_name"])
                status = "verified_alias" if alias else "name_mismatch"
        identity_status = status
        if p is not None and roster_status != "matched_code_parent_and_name":
            status = "roster_identity_mismatch"
        if b is not None and (code in conflicts or b["boundary_vintage"] is None):
            status = "geometry_conflict"
        eligible = status in {"verified", "verified_alias"}
        record = {"area_id": code, "county_code": code[:5], "population_present": p is not None,
                  "roster_present": r is not None, "boundary_present": b is not None,
                  "population_name": p["village_name"] if p else None,
                  "roster_name": r["village_name"] if r else None,
                  "boundary_name": b["village_name"] if b else None,
                  "district_id": r["district_code"] if r else (b["district_id"] if b else p["district_id"]),
                  "roster_match_status": roster_status, "boundary_identity_status": identity_status,
                  "boundary_join_status": status, "allocation_eligible": eligible,
                  "source_population": p["population"] if p else None,
                  "population_period": month, "roster_period": month,
                  "boundary_layer": b["boundary_layer"] if b else None,
                  "boundary_vintage": b["boundary_vintage"] if b else None,
                  "same_month_geometry_verified": False}
        if alias:
            record["name_alias_evidence"] = "data/official_cross_city/boundary_evidence/name_aliases.json; exact code, district and both names only"
        records.append(record)
        if b is not None and b["county_code"] in NORTHERN_COUNTIES:
            from pyproj import Transformer
            from shapely import transform
            from shapely.geometry import mapping
            properties = {"area_id": code, "boundary_id": code, "county_name": b["county_name"],
                          "district_name": b["district_name"], "area_name": b["county_name"] + b["district_name"],
                          "village_name": p["village_name"] if p else b["village_name"],
                          "boundary_village_name": b["village_name"], "boundary_join_status": status,
                          "population": p["population"] if p else None,
                          "age_counts": p["age_counts"] if p else None,
                          "boundary_vintage": b["boundary_vintage"], "source_note": b["note"]}
            properties["age_groups"] = {
                group["key"]: (sum(p["age_counts"][group["min_age"]:group["max_age"] + 1])
                               if p and all(value is not None for value in p["age_counts"][group["min_age"]:group["max_age"] + 1]) else None)
                for group in AGE_GROUPS}
            convert = Transformer.from_crs(3824, 4326, always_xy=True)
            features.append({"type": "Feature", "id": code, "properties": properties,
                             "geometry": mapping(transform(b["geometry"], convert.transform, interleaved=False))})
    def known_sum(values):
        return sum(value for value in values if value is not None)
    excluded = [row for row in records if row["population_present"] and not row["allocation_eligible"]]
    summary = {"population_village_count": len(counts), "roster_village_count": len(roster),
               "boundary_feature_count": len(boundaries), "boundary_layers": layers,
               "population_missing_from_roster": sorted(counts.keys() - roster.keys()),
               "roster_missing_from_population": sorted(roster.keys() - counts.keys()),
               "population_missing_boundary": sorted(counts.keys() - by_code.keys()),
               "roster_status_counts": dict(Counter(row["roster_match_status"] for row in records if row["population_present"])),
               "boundary_status_counts": dict(Counter(row["boundary_join_status"] for row in records)),
               "population_sum": known_sum(row["population"] for row in counts.values()),
               "excluded_population_village_count": len(excluded),
               "excluded_source_population_known_sum": known_sum(row["source_population"] for row in excluded),
               "excluded_source_population_unknown_count": sum(row["source_population"] is None for row in excluded),
               "supplemental_overlap_records": overlap_records,
               "same_month_geometry_verified": False}
    return records, features, summary


def acquire(root, month, offline):
    roc = f"{int(month[:4]) - 1911:03d}{month[5:]}"
    page, page_source = fetch(root, "ris_catalog.html", RIS_PAGE, RIS_PAGE, {"www.ris.gov.tw"},
                              offline=offline, max_mb=5, raw_namespace=NAMESPACE)
    links = Links(); links.feed(page.read_text(encoding="utf-8-sig"))
    expected = f"https://www.ris.gov.tw/documents/data/5/1/{roc}VillageUTF8.txt"
    if not any(expected == urljoin(RIS_PAGE, url) for url, _ in links.links):
        raise ValueError("Requested monthly RIS roster is not advertised in the official catalog")
    roster_path, roster_source = fetch(root, f"{roc}VillageUTF8.txt", expected, RIS_PAGE, {"www.ris.gov.tw"},
                                      offline=offline, raw_namespace=NAMESPACE)
    boundary_page, boundary_catalog_source = fetch(root, "boundary_catalog.html", BOUNDARY_PAGE, BOUNDARY_PAGE,
                                                  {"data.gov.tw"}, offline=offline, max_mb=5, raw_namespace=NAMESPACE)
    links = Links(); links.feed(boundary_page.read_text(encoding="utf-8-sig"))
    if not any(unquote(url) == unquote(VILLAGE_URL) for url, _ in links.links):
        raise ValueError("Official boundary catalog no longer advertises this distribution")
    archive, archive_source = fetch(root, "villages.zip", VILLAGE_URL, BOUNDARY_PAGE, {"www.tgos.tw"},
                                    offline=offline, max_mb=150, raw_namespace=NAMESPACE, raw_parent="map-data/raw")
    return roster_path, archive, [page_source, roster_source, boundary_catalog_source, archive_source]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--month", type=validate_month, required=True)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--fetch", action="store_true")
    mode.add_argument("--offline", action="store_true")
    args = parser.parse_args(argv)
    if not (args.fetch or args.offline):
        print(json.dumps({"month": args.month, "status": "plan_only", "sources": [RIS_PAGE, BOUNDARY_PAGE]})); return
    roster_path, archive, sources = acquire(args.root, args.month, args.offline)
    roster = parse_ris(roster_path.read_text(encoding="utf-8-sig"))
    boundaries, layers = archive_layers(archive)
    population_path = args.root / f"data/population/moi_village_age_{args.month}.json.gz"
    population = read_json(population_path)
    records, features, summary = reconcile(population, roster, boundaries, layers, args.month)
    meta = {"provider": ["內政部戶政司", "內政部國土測繪中心"], "license": OGDL,
            "sources": [{k: v for k, v in s.items() if k != "sha256"} for s in sources],
            "population_source": population["metadata"]["source"], "population_period": args.month,
            "roster_period": args.month, "boundary_vintage": next(layer["boundary_vintage"] for layer in layers if layer["boundary_vintage"]),
            "generated_at": sources[-1]["downloaded_at"], "national_audit_summary": summary,
            "independent_review_status": "pending", "same_month_geometry_verified": False,
            "limitations": ["清冊與人口逐碼／父碼／原名雙向核對；里界版本與人口月份不同，未證明同月幾何。",
                            "附層 Sanhe 日期未知且與六個主層村里重疊；相關人口保留原值，排除面積分攤。",
                            "名稱未審、無人口及未編定區塊不補零；全國保留遠島，地圖／站圈只使用北北基。",
                            "面積分攤假設村內人口均勻分布，不代表日間活動人口或實際公車搭乘需求。"]}
    from ..build_age_catchments import AGE_GROUPS, AGE_GROUP_SETS
    meta.update({"age_groups": AGE_GROUPS, "age_group_sets": AGE_GROUP_SETS})
    roster_output = f"data/population/ris_village_registry_{args.month}.json.gz"
    crosswalk_output = f"data/population/national_village_crosswalk_{args.month}.json.gz"
    geometry_output = f"map-data/population/moi_village_age_{args.month}.geojson.gz"
    write_gzip(args.root / roster_output, {"schema_version": 1, "dataset_id": "ris_national_village_registry",
               "metadata": {**meta, "grain": "one official monthly village identity"}, "records": list(roster.values())})
    write_gzip(args.root / crosswalk_output, {"schema_version": 1, "dataset_id": "national_village_population_boundary_crosswalk",
               "metadata": {**meta, "grain": "union of population, RIS and all NLSC layer identities"}, "records": records})
    write_gzip(args.root / geometry_output, {"type": "FeatureCollection", "schema_version": 1,
               "dataset_id": "northern_official_village_age_geometry", "metadata": {**meta, "crs": "EPSG:4326",
               "title": "北北基官方村里逐歲戶籍人口與完整里界",
               "coordinate_order": ["longitude", "latitude"], "source_crs": "EPSG:3824",
               "coordinate_operation": "pyproj Transformer.from_crs(EPSG:3824, EPSG:4326, always_xy=True)",
               "map_scope": "北北基（既有地圖範圍）", "geometry_simplified": False}, "features": features})
    geometry_path = args.root / geometry_output
    write_json(geometry_path.parent / "manifest.json", {"schema_version": 1,
               "dataset_id": "northern_village_age_map_delivery",
               "metadata": {"title": "北北基村里年齡圖層", "coordinate_reference_system": "EPSG:4326"},
               "records": [{"dataset_id": "northern_official_village_age_geometry", "outputs": [
                   {"path": geometry_output, "record_count": len(features), "encoding": "gzip",
                    "bytes": geometry_path.stat().st_size,
                    "uncompressed_bytes": len(gzip.decompress(geometry_path.read_bytes()))}]}]})
    write_json(args.root / f"raw_data/{NAMESPACE}/manifest.json", {"schema_version": 1,
               "dataset_id": "national_village_age_sources", "metadata": {"period": args.month, "license": OGDL}, "records": meta["sources"]})
    write_json(args.root / f"raw_data/{NAMESPACE}/delivery.json", {"group": NAMESPACE, "datasets": [
               {"dataset_id": "national_village_population_boundary_crosswalk", "status": "normalized_with_explicit_exclusions",
                "period": args.month, "output_files": [roster_output, crosswalk_output, geometry_output], "limitations": meta["limitations"]}]})
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()
