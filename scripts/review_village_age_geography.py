"""Independently audit monthly RIS identities, every NLSC layer and age geometry.

No collector, normalizer, matcher or station estimator is imported. The audit
reads the retained TXT/DBF/SHP/PRJ and the delivered snapshots independently.
"""
from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
import csv
from datetime import date, datetime
import gzip
import hashlib
from io import BytesIO
import json
from pathlib import Path
import re
import xml.etree.ElementTree as ET
from zipfile import ZipFile
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
NORTH = {"63000", "65000", "10017"}
GROUP_INTERVALS = {"age_0_14": (0, 14), "age_15_64": (15, 64), "age_65_plus": (65, 100),
                   "age_0_5": (0, 5), "age_6_17": (6, 17), "age_18_64": (18, 64),
                   "age_65_74": (65, 74), "age_75_plus": (75, 100)}
# Evidence policy: only these exact tuples were verified in the official 2022
# report. A name table cannot silently authorize another code or replacement.
ALIASES = {
    "65000030017": ("中和區", "瓦\U000fffa8里", "瓦[磘]里"),
    "65000030054": ("中和區", "灰\U000fffa8里", "灰[磘]里"),
    "65000070007": ("樹林區", "\U000fffb5寮里", "[獇]寮里"),
    "65000120027": ("瑞芳區", "濓新里", "濂新里"),
    "65000120028": ("瑞芳區", "濓洞里", "濂洞里"),
    "65000200004": ("坪林區", "石𥕢里", "石[曹]里"),
}


def read(path):
    with (gzip.open(path, "rt", encoding="utf-8") if path.suffix == ".gz" else path.open(encoding="utf-8")) as stream:
        return json.load(stream)


def exact(a, b):
    if type(a) is not type(b):
        return False
    if isinstance(a, dict):
        return a.keys() == b.keys() and all(exact(a[k], b[k]) for k in a)
    if isinstance(a, list):
        return len(a) == len(b) and all(exact(x, y) for x, y in zip(a, b))
    return a == b


def population_source_snapshot(metadata):
    """Retain the reviewed document's receipt, including valid nested nulls."""
    return deepcopy(metadata.get("population_source"))


def registry_rows(path):
    result = {}
    with path.open(encoding="utf-8-sig", newline="") as stream:
        for number, cells in enumerate(csv.reader(stream, strict=True), 1):
            if (len(cells) != 3 or not re.fullmatch(r"[0-9]{11}", cells[0])
                    or not re.fullmatch(r"[0-9]{8}", cells[1]) or cells[0][:8] != cells[1]
                    or not cells[2] or cells[0] in result):
                raise ValueError(f"Invalid raw RIS identity at row {number}")
            result[cells[0]] = {"village_code": cells[0], "district_code": cells[1],
                                "village_name": cells[2], "source_row_number": number}
    if not result:
        raise ValueError("Empty raw RIS roster")
    return result


def boundary_rows(path):
    import shapefile
    from pyproj import CRS
    from shapely.geometry import shape

    result, layers, revisions = {}, [], []
    with ZipFile(path) as archive:
        stems = sorted(name[:-4] for name in archive.namelist() if name.lower().endswith(".shp"))
        if not stems:
            raise ValueError("No source SHP layers")
        for name in archive.namelist():
            if name.lower().endswith(".xml"):
                xml = ET.fromstring(archive.read(name))
                for record in xml.findall(".//{*}CI_Date"):
                    kind = record.find(".//{*}CI_DateTypeCode")
                    value = record.find(".//{*}Date")
                    if kind is not None and kind.get("codeListValue") == "revision" and value is not None:
                        revisions.append(value.text)
        for stem in stems:
            matched = re.fullmatch(r"VILLAGE_NLSC_([0-9]{3})([0-9]{2})([0-9]{2})", stem)
            vintage = date(int(matched[1]) + 1911, int(matched[2]), int(matched[3])).isoformat() if matched else None
            crs = CRS.from_wkt(archive.read(stem + ".prj").decode("utf-8-sig"))
            if crs.to_epsg() != 3824:
                raise ValueError(f"Unexpected raw source CRS: {stem}")
            reader = shapefile.Reader(**{ext: BytesIO(archive.read(stem + "." + ext)) for ext in ("shp", "shx", "dbf")}, encoding="utf-8")
            count = 0
            for item in reader.iterShapeRecords():
                row = item.record.as_dict()
                code = row["VILLCODE"]
                unassigned = row["NOTE"] == "未編定村里" and not row["VILLNAME"]
                if (not (re.fullmatch(r"[0-9]{11}", code) or (unassigned and re.fullmatch(r"[0-9]{8}[A-Z][0-9]{2}", code)))
                        or code in result or row["COUNTYCODE"] != code[:5] or row["TOWNCODE"] != code[:8]):
                    raise ValueError(f"Invalid or duplicate raw boundary identity: {stem}/{code}")
                geom = shape(item.shape.__geo_interface__)
                if geom.geom_type not in {"Polygon", "MultiPolygon"} or geom.is_empty or not geom.is_valid:
                    raise ValueError(f"Invalid raw boundary geometry: {stem}/{code}")
                result[code] = {"properties": row, "geometry": geom, "layer": stem, "vintage": vintage}
                count += 1
            layers.append({"layer": stem, "feature_count": count, "source_crs": "EPSG:3824",
                           "boundary_vintage": vintage, "same_archive_distribution": True,
                           "vintage_evidence": "dated SHP filename" if matched else "unknown; ZIP member timestamps are not geometry dates"})
    if any(layer["boundary_vintage"] and layer["boundary_vintage"] not in revisions for layer in layers):
        raise ValueError("Dated SHP vintage lacks independent archive XML revision evidence")
    return result, layers


def supplemental_overlaps(boundaries):
    """Use an independently chosen projected CRS; compare pair sets, not rounding."""
    from pyproj import Transformer
    from shapely import STRtree, transform

    main = [(code, row) for code, row in boundaries.items() if row["vintage"] is not None]
    tree = STRtree([row["geometry"] for _, row in main])
    pairs = []
    for code, row in boundaries.items():
        if row["vintage"] is not None:
            continue
        centre = row["geometry"].centroid
        # UTM is local to this polygon. This differs from the producer's LAEA.
        zone = max(1, min(60, int((centre.x + 180) // 6) + 1))
        converter = Transformer.from_crs(3824, 32600 + zone, always_xy=True)
        for index in tree.query(row["geometry"], predicate="intersects"):
            other_code, other = main[int(index)]
            intersection = row["geometry"].intersection(other["geometry"])
            area = transform(intersection, converter.transform, interleaved=False).area
            if area > .01:
                pairs.append({"supplement_area_id": code, "main_area_id": other_code,
                              "independent_overlap_area_m2": round(area, 2)})
    return pairs


def inspect_outputs(roster, boundaries, layers, population, registry, crosswalk, geo, month):
    """Return field-level errors and source facts without importing the producer."""
    from pyproj import Transformer
    from shapely import transform
    from shapely.geometry import shape

    errors, checks = [], {}
    def check(condition, message):
        if not condition:
            errors.append(message)
    def indexed(rows, key, label):
        result = {row[key]: row for row in rows}
        check(len(result) == len(rows), label + ": duplicate IDs")
        return result

    check(population["metadata"]["period"] == month, "population month differs")
    population_source = population["metadata"].get("source")
    check(isinstance(population_source, dict) and bool(population_source),
          "reviewed population input source receipt is missing or invalid")
    pop = indexed([row for row in population["records"] if row["sex"] == "total"], "area_id", "population")
    check(set(pop) == set(roster), "population/RIS code sets differ")
    for code, row in pop.items():
        check(row["period"] == month and len(row["age_counts"]) == 101, code + ": population age/month shape differs")
        check(row["population"] is None or (type(row["population"]) is int and row["population"] >= 0), code + ": invalid source population")
        check(all(value is None or (type(value) is int and value >= 0) for value in row["age_counts"]), code + ": invalid source age cell")
        if code in roster:
            check((row["district_id"], row["village_name"]) == (roster[code]["district_code"], roster[code]["village_name"]), code + ": population/RIS parent or literal name differs")
    delivered_roster = indexed(registry["records"], "village_code", "registry")
    check(exact(roster, delivered_roster), "registry output differs from complete raw roster, including parents/names/rows")
    delivered_crosswalk = indexed(crosswalk["records"], "area_id", "crosswalk")
    check(set(delivered_crosswalk) == (set(pop) | set(roster) | set(boundaries)), "crosswalk complete ID union differs")
    overlaps = supplemental_overlaps(boundaries)
    conflict = {record[key] for record in overlaps for key in ("supplement_area_id", "main_area_id")}
    names, aliases, expected_statuses = [], [], {}
    for code in sorted(set(pop) | set(roster) | set(boundaries)):
        p, r, b = pop.get(code), roster.get(code), boundaries.get(code)
        props = b["properties"] if b else None
        identity = "missing_boundary"
        if props is not None:
            if p is None:
                identity = "unassigned" if props["NOTE"] == "未編定村里" else "missing_population"
            elif (props["TOWNCODE"] != p["district_id"] or props["COUNTYNAME"] != p["county_name"]
                  or props["COUNTYNAME"] + props["TOWNNAME"] != p["area_name"]):
                identity = "parent_mismatch"
            elif props["VILLNAME"] == p["village_name"]:
                identity = "verified"
            else:
                names.append(code)
                allowed = ALIASES.get(code) == (props["TOWNNAME"], p["village_name"], props["VILLNAME"])
                identity = "verified_alias" if allowed else "name_mismatch"
                if allowed:
                    aliases.append(code)
        roster_status = ("missing_population" if p is None else "missing_roster" if r is None else
                         "matched_code_parent_and_name" if (p["district_id"], p["village_name"]) == (r["district_code"], r["village_name"]) else "roster_identity_mismatch")
        status = "roster_identity_mismatch" if p is not None and roster_status != "matched_code_parent_and_name" else identity
        if b and (code in conflict or b["vintage"] is None):
            status = "geometry_conflict"
        expected_statuses[code] = status
        expected = {"area_id": code, "county_code": code[:5], "population_present": p is not None,
                    "roster_present": r is not None, "boundary_present": b is not None,
                    "population_name": p["village_name"] if p else None, "roster_name": r["village_name"] if r else None,
                    "boundary_name": props["VILLNAME"] or None if props else None,
                    "district_id": r["district_code"] if r else (props["TOWNCODE"] if props else p["district_id"]),
                    "roster_match_status": roster_status, "boundary_identity_status": identity,
                    "boundary_join_status": status, "allocation_eligible": status in {"verified", "verified_alias"},
                    "source_population": p["population"] if p else None, "population_period": month, "roster_period": month,
                    "boundary_layer": b["layer"] if b else None, "boundary_vintage": b["vintage"] if b else None,
                    "same_month_geometry_verified": False}
        actual = delivered_crosswalk.get(code, {})
        for key, value in expected.items():
            check(exact(value, actual.get(key)), f"{code}: crosswalk {key} differs")
        check(("name_alias_evidence" in actual) == (identity == "verified_alias"), code + ": alias evidence eligibility differs")
    northern = {code: row for code, row in boundaries.items() if row["properties"]["COUNTYCODE"] in NORTH}
    features = indexed(geo["features"], "id", "geometry")
    check(set(features) == set(northern), "northern geometry IDs differ from all raw northern features")
    converter = Transformer.from_crs(3824, 4326, always_xy=True)
    maximum_distance, geometry_differences = 0.0, []
    for code in sorted(set(features) & set(northern)):
        actual, raw = features[code], northern[code]
        p, props = pop.get(code), raw["properties"]
        expected_properties = {"area_id": code, "boundary_id": code, "county_name": props["COUNTYNAME"],
                               "district_name": props["TOWNNAME"], "area_name": props["COUNTYNAME"] + props["TOWNNAME"],
                               "village_name": p["village_name"] if p else props["VILLNAME"] or None,
                               "boundary_village_name": props["VILLNAME"] or None,
                               "boundary_join_status": expected_statuses[code],
                               "population": p["population"] if p else None,
                               "age_counts": p["age_counts"] if p else None,
                               "boundary_vintage": raw["vintage"], "source_note": props["NOTE"] or None}
        expected_properties["age_groups"] = {}
        for key, (lower, upper) in GROUP_INTERVALS.items():
            cells = p["age_counts"][lower:upper + 1] if p else [None]
            expected_properties["age_groups"][key] = None if None in cells else sum(cells)
        check(exact(actual["properties"], expected_properties), code + ": geometry properties or 101 source age cells differ")
        expected_geom = transform(raw["geometry"], converter.transform, interleaved=False)
        observed = shape(actual["geometry"])
        distance = expected_geom.hausdorff_distance(observed)
        maximum_distance = max(maximum_distance, distance)
        if (not observed.is_valid or observed.geom_type not in {"Polygon", "MultiPolygon"}
                or not expected_geom.equals_exact(observed, tolerance=1e-12)):
            geometry_differences.append(code)
    check(not geometry_differences, "source/output full geometry coordinates differ: " + ",".join(geometry_differences[:10]))
    summary = crosswalk["metadata"].get("national_audit_summary", {})
    for key, value in {"population_village_count": len(pop), "roster_village_count": len(roster),
                       "boundary_feature_count": len(boundaries), "boundary_layers": layers,
                       "population_missing_from_roster": sorted(set(pop) - set(roster)),
                       "roster_missing_from_population": sorted(set(roster) - set(pop)),
                       "population_missing_boundary": sorted(set(pop) - set(boundaries)),
                       "boundary_status_counts": dict(Counter(expected_statuses.values())),
                       "same_month_geometry_verified": False}.items():
        check(exact(summary.get(key), value), "national summary " + key + " differs")
    observed_pairs = {(row["supplement_area_id"], row["main_area_id"]) for row in summary.get("supplemental_overlap_records", [])}
    check(observed_pairs == {(row["supplement_area_id"], row["main_area_id"]) for row in overlaps}, "supplemental overlap pair set differs")
    check(all(row.get("geometry_eligible") is False for row in summary.get("supplemental_overlap_records", [])), "supplemental overlaps are not excluded")
    for label, document in (("registry", registry), ("crosswalk", crosswalk), ("geometry", geo)):
        meta = document["metadata"]
        delivered_population_source = meta.get("population_source")
        check(isinstance(delivered_population_source, dict) and bool(delivered_population_source),
              label + ": population_source receipt is missing or invalid")
        check(exact(delivered_population_source, population_source),
              label + ": population source snapshot differs from the reviewed input")
        check(meta.get("population_period") == month and meta.get("roster_period") == month
              and meta.get("same_month_geometry_verified") is False, label + ": temporal claims differ")
        check(exact(meta.get("national_audit_summary"), summary), label + ": national summary snapshot differs")
        check(meta.get("boundary_vintage") in {layer["boundary_vintage"] for layer in layers if layer["boundary_vintage"]}, label + ": main boundary vintage differs")
    checks.update({"raw_roster_count": len(roster), "raw_boundary_count": len(boundaries),
                   "population_village_count": len(pop), "raw_layers": layers,
                   "boundary_only_unassigned_count": sum(row["properties"]["NOTE"] == "未編定村里" for row in boundaries.values()),
                   "name_variant_count": len(names), "explicit_alias_count": len(aliases),
                   "unreviewed_name_variant_codes": sorted(set(names) - set(aliases)),
                   "supplemental_overlap_pairs": overlaps, "excluded_conflicting_codes": sorted(conflict),
                   "northern_full_geometry_count": len(northern), "maximum_geometry_distance_degrees": maximum_distance,
                   "geometry_difference_codes": geometry_differences, "failure_count": len(errors), "failures": errors})
    return checks


def review(root=ROOT, month="2026-09"):
    root = Path(root)
    if not re.fullmatch(r"20[0-9]{2}-(?:0[1-9]|1[0-2])", month):
        raise ValueError("month must be YYYY-MM")
    roc = f"{int(month[:4]) - 1911:03d}{month[5:]}"
    paths = [f"data/population/ris_village_registry_{month}.json.gz",
             f"data/population/national_village_crosswalk_{month}.json.gz",
             f"map-data/population/moi_village_age_{month}.geojson.gz"]
    documents, errors, checks = [], [], {}
    try:
        documents = [read(root / path) for path in paths]
        metadata = documents[1]["metadata"]
        for receipt in metadata["sources"]:
            path = root / receipt["raw_file"]
            raw_receipt = read(path.with_name(path.name + ".source.json"))
            if ({key: value for key, value in raw_receipt.items() if key != "sha256"} != receipt
                    or raw_receipt["http_status"] != 200 or path.stat().st_size != raw_receipt["bytes"]
                    or hashlib.sha256(path.read_bytes()).hexdigest() != raw_receipt["sha256"]):
                errors.append("raw source content/receipt snapshot differs: " + receipt["raw_file"])
        for document in documents:
            if not exact(document["metadata"]["sources"], metadata["sources"]):
                errors.append("delivered source receipt arrays differ")
        roster = registry_rows(root / f"raw_data/national_village_age/{roc}VillageUTF8.txt")
        boundaries, layers = boundary_rows(root / "map-data/raw/national_village_age/villages.zip")
        population = read(root / f"data/population/moi_village_age_{month}.json.gz")
        for document in documents:
            if document["metadata"].get("generated_at") != metadata["sources"][-1].get("downloaded_at"):
                errors.append("delivered generation does not identify the retained boundary archive snapshot")
        checks = inspect_outputs(roster, boundaries, layers, population, *documents, month)
        errors.extend(checks.pop("failures"))
    except (OSError, ValueError, KeyError, TypeError, AttributeError, csv.Error, ET.ParseError) as exc:
        errors.append(f"independent review could not complete: {type(exc).__name__}: {exc}")
    checks.update({"failure_count": len(errors), "failures": errors})
    status = "needs_correction" if errors else "pass_with_limitations"
    records = []
    for path, doc in zip(paths, documents):
        records.append({"file": path, "output_path": path, "review_status": status,
                        "record_count": len(doc.get("records", doc.get("features", []))),
                        "reviewed_generated_at": doc["metadata"].get("generated_at"),
                        "population_source": population_source_snapshot(doc["metadata"]),
                        "sources": doc["metadata"].get("sources", []), "checks": checks})
    return {"schema_version": 1, "dataset_id": "independent_national_village_age_geography_review",
            "metadata": {"reviewer_agent": "/root/national_reference", "producer_imported": False,
                         "reviewed_at": datetime.now(ZoneInfo("Asia/Taipei")).isoformat(timespec="seconds"),
                         "period": month, "status": status,
                         "method": "Independent raw TXT/DBF/SHP/PRJ/XML parsing, complete identity sets and literal fields, all northern coordinates and 101 source age cells; supplemental intersections independently projected to local UTM.",
                         "limitations": [f"人口{month}與主里界版本分開記錄；未核實同月幾何。無日期附層及其主層交疊村里均排除分攤。",
                                         f"本次另保留{len(checks.get('unreviewed_name_variant_codes', []))}筆未審名稱差異與{checks.get('boundary_only_unassigned_count')}個未編定區塊，不補零人口。",
                                         "本報告驗證清冊、里界與年齡串接快照；站圈數值另以抽站重算查核，人口不代表真實乘車需求。"]},
            "records": records}


def recompute_circle(rows, point, radius):
    """Independent scalar density arithmetic and explicitly constructed circle."""
    import math
    from shapely import union_all
    from shapely.geometry import Polygon

    circle = Polygon([(point[0] + radius * math.cos(2 * math.pi * i / 256),
                       point[1] + radius * math.sin(2 * math.pi * i / 256)) for i in range(256)])
    parts = []
    for row in rows:
        if row["geometry"].intersects(circle):
            clipped = row["geometry"].intersection(circle)
            if clipped.area > 0:
                parts.append((row, clipped))
    good = [(row, piece) for row, piece in parts if row["verified"] and not row["conflict"]]
    counted = [(row, piece) for row, piece in good if row["population"] is not None]
    def share(items):
        return min(1., max(0., union_all([piece for _, piece in items]).area / circle.area)) if items else 0.
    def weighted(items, field):
        return sum(row[field] * piece.area / row["geometry"].area for row, piece in items) if items else None
    def rounded(value):
        return round(value, 2) if value is not None else None
    coverage = share(counted)
    known = weighted(counted, "population")
    result = {"population_estimate": rounded(known) if coverage >= 1 - 1e-8 else None,
              "population_known_estimate": rounded(known), "boundary_share": round(share(parts), 6),
              "data_share": round(share(good), 6), "population_data_share": round(coverage, 6),
              "outside_boundary_share": round(1 - share(parts), 6), "village_count": len(parts),
              "unmatched_village_count": sum(not row["verified"] for row, _ in parts),
              "geometry_conflict_village_count": sum(row["conflict"] for row, _ in parts),
              "missing_population_village_count": sum(row["verified"] and row["population"] is None for row, _ in parts),
              "unknown_village_count": sum(not row["verified"] or row["conflict"] or row["population"] is None
                                            or any(row[key] is None for key in GROUP_INTERVALS) for row, _ in parts),
              "age_group_estimates": {}, "age_group_known_estimates": {},
              "age_group_data_shares": {}, "age_group_unknown_village_counts": {}}
    for key in GROUP_INTERVALS:
        available = [(row, piece) for row, piece in good if row[key] is not None]
        complete = [(row, piece) for row, piece in available if row["population"] is not None]
        value = weighted(available, key)
        group_share = share(complete)
        result["age_group_estimates"][key] = rounded(value) if group_share >= 1 - 1e-8 else None
        result["age_group_known_estimates"][key] = rounded(value)
        result["age_group_data_shares"][key] = round(group_share, 6)
        result["age_group_unknown_village_counts"][key] = sum(not row["verified"] or row["conflict"]
            or row["population"] is None or row[key] is None for row, _ in parts)
    complete = result["population_estimate"] is not None and all(value is not None for value in result["age_group_estimates"].values())
    result["status"] = ("no_boundary" if not parts else "geometry_conflict" if result["geometry_conflict_village_count"]
                        else "complete" if complete else "partial")
    return result


def station_points_from_layers(layers):
    """Independently retain displayed IDs; source collisions are audit failures."""
    import math

    points = {"bus": {}, "mrt": {}, "youbike": {}}
    source_counts, source_ids, snapshots, errors = {}, {}, {}, []
    for kind, name, filename, id_key in (
        ("bus", "bus_stops", "bus_stops.geojson", "id"),
        ("bus", "regional_bus_stops", "regional_bus_stops.geojson", "id"),
        ("mrt", "mrt", "mrt_stations.geojson", "id"),
        ("youbike", "youbike", "youbike_stations.geojson", "station_id"),
    ):
        layer = layers[filename]
        source_counts[name] = len(layer["features"])
        source_ids[name] = set()
        snapshots[filename] = layer["metadata"]
        for feature in layer["features"]:
            code = feature.get("properties", {}).get(id_key)
            geometry = feature.get("geometry") or {}
            coordinate = geometry.get("coordinates")
            if not isinstance(code, str) or not code:
                errors.append(name + ": missing original string station ID")
                continue
            if code in source_ids[name]:
                errors.append(name + ": duplicate displayed original station ID: " + code)
            source_ids[name].add(code)
            if code in points[kind]:
                errors.append(name + ": station ID collision within namespace " + kind + ": " + code)
                continue
            if (geometry.get("type") != "Point" or not isinstance(coordinate, (list, tuple)) or len(coordinate) != 2
                    or any(type(value) not in {int, float} or not math.isfinite(value) for value in coordinate)):
                errors.append(name + ": invalid displayed WGS84 station coordinate: " + code)
                continue
            points[kind][code] = list(coordinate)
    return {"points": points, "source_counts": source_counts, "source_ids": source_ids,
            "layer_metadata": snapshots, "errors": errors}


def fnv1a32_utf8(value):
    """FNV-1a operates on UTF-8 bytes, with unsigned overflow after each byte."""
    result = 2166136261
    for byte in value.encode("utf-8"):
        result = ((result ^ byte) * 16777619) & 0xffffffff
    return result


def gzip_copy_matches(path):
    """Compare large plain/gzip copies without allocating another full body."""
    try:
        with path.open("rb") as plain, gzip.open(path.with_name(path.name + ".gz"), "rb") as compressed:
            while block := plain.read(1024 ** 2):
                if compressed.read(len(block)) != block:
                    return False
            return not compressed.read(1)
    except (OSError, EOFError):
        return False


def inspect_station_shards(canonical, parts):
    """Compare a streaming part inventory against all canonical ID/value pairs."""
    expected = {(kind, code) for kind, stations in canonical["records"].items() for code in stations}
    names, seen, errors, counts = set(), set(), [], {}
    expected_names = {f"part-{index:02d}.json" for index in range(32)}
    for filename, document in parts:
        if filename in names:
            errors.append("duplicate shard file: " + filename)
        names.add(filename)
        matched = re.fullmatch(r"part-([0-9]{2})\.json", filename)
        if not matched or int(matched[1]) >= 32:
            errors.append("unexpected shard file: " + filename)
            continue
        index = int(matched[1])
        shard = document.get("metadata", {}).get("shard", {})
        if (shard.get("algorithm") != "fnv1a32-utf8-mod32" or type(shard.get("index")) is not int
                or shard.get("index") != index or type(shard.get("count")) is not int or shard.get("count") != 32):
            errors.append(filename + ": shard routing metadata differs")
        for key in ("schema_version", "dataset_id", "radii"):
            if not exact(document.get(key), canonical.get(key)):
                errors.append(filename + ": canonical envelope differs: " + key)
        metadata = {key: value for key, value in document.get("metadata", {}).items() if key != "shard"}
        if not exact(metadata, canonical.get("metadata")):
            errors.append(filename + ": complete canonical metadata/source snapshot differs")
        if set(document.get("records", {})) != set(canonical["records"]):
            errors.append(filename + ": station namespace set differs")
        count = 0
        for kind, stations in document.get("records", {}).items():
            for code, entries in stations.items():
                count += 1
                key = (kind, code)
                if key in seen:
                    errors.append(filename + ": station repeated across shards: " + kind + "/" + code)
                seen.add(key)
                if fnv1a32_utf8(kind + ":" + code) % 32 != index:
                    errors.append(filename + ": original station ID routed to the wrong shard: " + kind + "/" + code)
                if key not in expected or not exact(entries, canonical["records"][kind][code]):
                    errors.append(filename + ": station entries differ from canonical: " + kind + "/" + code)
        counts[filename] = count
        if type(shard.get("station_count")) is not int or shard.get("station_count") != count:
            errors.append(filename + ": shard station_count differs")
    if names != expected_names:
        errors.append("complete set of 32 shard filenames differs")
    if seen != expected:
        errors.append("shard station union differs from the canonical ID set")
    return {"algorithm": "fnv1a32-utf8-mod32", "shard_count": len(names),
            "canonical_station_count": len(expected), "unique_shard_station_count": len(seen),
            "station_counts_by_shard": counts, "failure_count": len(errors), "failures": errors}


def station_review(root=ROOT, month="2026-09"):
    import math
    from pyproj import Transformer
    from shapely import STRtree, intersection, area, transform
    from shapely.geometry import shape

    root = Path(root)
    relative = "web/public/data/station_age_catchments.json"
    doc = read(root / relative)
    full = read(root / f"map-data/population/moi_village_age_{month}.geojson.gz")
    errors, checks, samples = [], {}, []
    def check(condition, message):
        if not condition:
            errors.append(message)
    check(gzip_copy_matches(root / relative), "canonical station gzip/plain contents differ")
    for label, document in (("station", doc), ("village geometry", full)):
        receipt = document["metadata"].get("population_source")
        check(isinstance(receipt, dict) and bool(receipt), label + ": population_source receipt is missing or invalid")
    inventory = station_points_from_layers({name: read(root / "web/public/data" / name) for name in (
        "bus_stops.geojson", "regional_bus_stops.geojson", "mrt_stations.geojson", "youbike_stations.geojson")})
    expected_points = inventory["points"]
    errors.extend(inventory["errors"])
    for kind in expected_points:
        check(set(doc["records"].get(kind, {})) == set(expected_points[kind]), kind + ": displayed station/catchment ID sets differ")
    check(set(doc["records"]) == set(expected_points), "station namespaces differ")
    check(exact(doc["radii"], [200, 350, 500]), "station radii differ")
    status_counts = Counter()
    for kind, stations in doc["records"].items():
        for code, entries in stations.items():
            check([entry["radius_m"] for entry in entries] == doc["radii"], kind + "/" + code + ": radius entry IDs differ")
            for entry in entries:
                status_counts[entry["status"]] += 1
                check(set(entry["age_group_estimates"]) == set(GROUP_INTERVALS), kind + "/" + code + ": age group keys differ")
                present = entry["population_estimate"] is not None and all(value is not None for value in entry["age_group_estimates"].values())
                check((entry["status"] == "complete") == present, kind + "/" + code + ": complete status/null contract differs")
                for key in ("population_estimate", "population_known_estimate"):
                    value = entry[key]
                    check(value is None or (type(value) in {int, float} and math.isfinite(value) and value >= 0), kind + "/" + code + ": invalid numeric estimate")
    quality = doc["metadata"]["quality"]
    check(quality["station_count"] == sum(len(points) for points in expected_points.values()), "quality station count differs")
    check(exact(doc["metadata"].get("station_source_counts"), inventory["source_counts"]), "station per-source counts differ")
    check(exact(quality["station_radius_status_counts"], dict(status_counts)), "quality status counts differ from complete record traversal")
    for key in ("sources", "generated_at", "population_period", "roster_period", "boundary_vintage", "population_source"):
        check(exact(doc["metadata"].get(key), full["metadata"].get(key)), "station source snapshot differs: " + key)
    check(doc["metadata"].get("same_month_geometry_verified") is False, "station output claims same-month geometry")
    projection = doc["metadata"].get("projection", {})
    check(projection.get("source") == "EPSG:4326" and projection.get("area_and_distance") == "EPSG:3826", "station projection metadata differs")
    supported = [120.9, 24.5, 122.2, 25.7]
    check(exact(projection.get("supported_bbox_wgs84"), supported), "station supported projection scope differs")
    unsupported = {kind: [code for code, (lon, lat) in points.items()
                         if not (supported[0] <= lon <= supported[2] and supported[1] <= lat <= supported[3])]
                   for kind, points in expected_points.items()}
    check(not any(unsupported.values()), "displayed stations outside supported projection scope are not explicitly handled")
    project = Transformer.from_crs(4326, 3826, always_xy=True)
    rows = []
    for feature in full["features"]:
        props = feature["properties"]
        verified = props["boundary_join_status"] in {"verified", "verified_alias"}
        row = {"id": feature["id"], "geometry": transform(shape(feature["geometry"]), project.transform, interleaved=False),
               "population": props["population"] if verified else None, "verified": verified, "conflict": False}
        for key, (lower, upper) in GROUP_INTERVALS.items():
            cells = props["age_counts"][lower:upper + 1] if verified and props["age_counts"] is not None else [None]
            row[key] = None if None in cells else sum(cells)
        rows.append(row)
    # Vectorized whole-layer pair query, independently from producer's row loop.
    geometries = [row["geometry"] for row in rows]
    indices = STRtree(geometries).query(geometries, predicate="intersects")
    pairs = [(int(i), int(j)) for i, j in zip(*indices) if i < j]
    overlaps = []
    if pairs:
        sizes = area(intersection([geometries[i] for i, _ in pairs], [geometries[j] for _, j in pairs]))
        for (i, j), size in zip(pairs, sizes):
            if size > .01:
                rows[i]["conflict"] = rows[j]["conflict"] = True
                overlaps.append((rows[i]["id"], rows[j]["id"]))
    check(quality["overlap_pair_count"] == len(overlaps), "station topology overlap pair count differs")
    check(quality["conflicting_boundary_count"] == sum(row["conflict"] for row in rows), "station topology conflicting boundary count differs")
    check(quality["boundary_count"] == len(rows), "station topology boundary count differs")
    # Retain the three existing local categories, then independently check the
    # new regional source using its complete original ID and displayed point.
    selections = (("complete", "youbike", "youbike", lambda entries: all(row["status"] == "complete" for row in entries)),
                  ("partial_with_unmatched_boundary", "bus", "bus_stops", lambda entries: any(row["status"] == "partial" and row["unmatched_village_count"] > 0 and row["outside_boundary_share"] == 0 for row in entries)),
                  ("outside_boundary_edge", "mrt", "mrt", lambda entries: any(row["outside_boundary_share"] > 0 for row in entries)),
                  ("regional_original_station", "bus", "regional_bus_stops", lambda entries: any(row["population_known_estimate"] is not None for row in entries)))
    for category, kind, source_name, predicate in selections:
        chosen = next((code for code, entries in doc["records"].get(kind, {}).items()
                       if code in inventory["source_ids"][source_name] and predicate(entries)), None)
        if chosen is None:
            errors.append("No independent station sample for acceptance category: " + category)
            continue
        coordinate = expected_points[kind][chosen]
        centre = project.transform(*coordinate)
        for actual in doc["records"][kind][chosen]:
            expected = recompute_circle(rows, centre, actual["radius_m"])
            mismatches = []
            def compare(a, b, key):
                if isinstance(a, dict) and isinstance(b, dict):
                    for child in a:
                        compare(a[child], b.get(child), key + "." + child)
                elif a is None or b is None:
                    if a is not b:
                        mismatches.append(key)
                elif type(a) is float:
                    tolerance = .011 if "estimate" in key else 1.1e-6
                    if not isinstance(b, (int, float)) or abs(a - b) > tolerance:
                        mismatches.append(key)
                elif not exact(a, b):
                    mismatches.append(key)
            for key, value in expected.items():
                compare(value, actual.get(key), key)
            check(not mismatches, f"{kind}/{chosen}/{actual['radius_m']}: independent sample differs: {mismatches}")
            samples.append({"category": category, "namespace": kind, "station_source": source_name, "station_id": chosen,
                            "displayed_coordinate_wgs84": coordinate, "radius_m": actual["radius_m"],
                            "expected": expected, "status": "passed" if not mismatches else "failed", "mismatched_fields": mismatches})
    display = read(root / "web/public/data/village_age.geojson")
    source_by_id = {feature["id"]: feature for feature in full["features"]}
    display_by_id = {feature["id"]: feature for feature in display["features"]}
    check(set(source_by_id) == set(display_by_id) and len(display_by_id) == len(display["features"]), "display village-age ID set differs")
    check(all(exact(feature["properties"], display_by_id.get(code, {}).get("properties")) for code, feature in source_by_id.items()), "display village-age source properties/age cells differ")
    check(display["metadata"].get("display_geometry_only") is True, "display geometry is not explicitly labelled")
    shard_directory = root / "web/public/data/station_age_catchments"
    sizes = []
    expected_compressed_names = {f"part-{index:02d}.json.gz" for index in range(32)}
    check({path.name for path in shard_directory.glob("*.json.gz")} == expected_compressed_names,
          "complete set of 32 compressed shard filenames differs")
    def shard_parts():
        for path in sorted(shard_directory.glob("*.json")):
            plain = path.read_bytes()
            compressed = path.with_name(path.name + ".gz")
            if not compressed.exists():
                errors.append(path.name + ": gzip copy missing")
                compressed_bytes = b""
            else:
                compressed_bytes = compressed.read_bytes()
                try:
                    check(gzip.decompress(compressed_bytes) == plain, path.name + ": gzip/plain contents differ")
                except (OSError, EOFError) as exc:
                    errors.append(path.name + ": invalid gzip copy: " + str(exc))
            sizes.append({"file": path.name, "plain_bytes": len(plain), "gzip_bytes": len(compressed_bytes)})
            yield path.name, json.loads(plain)
    shard_check = inspect_station_shards(doc, shard_parts())
    errors.extend(shard_check["failures"])
    shard_check.update({"gzip_plain_comparisons": len(sizes), "file_sizes": sizes,
                        "total_plain_bytes": sum(row["plain_bytes"] for row in sizes),
                        "total_gzip_bytes": sum(row["gzip_bytes"] for row in sizes)})
    checks.update({"station_namespace_counts": {kind: len(points) for kind, points in expected_points.items()},
                   "station_source_counts": inventory["source_counts"],
                   "unsupported_station_ids_by_namespace": unsupported,
                   "bus_id_collision_check_passed": not inventory["errors"],
                   "station_radius_status_counts": dict(status_counts), "independent_overlap_pair_count": len(overlaps),
                   "sample_station_count": len({(sample['namespace'], sample['station_id']) for sample in samples}),
                   "sample_radius_count": len(samples), "station_layer_metadata": inventory["layer_metadata"],
                   "canonical_plain_bytes": (root / relative).stat().st_size,
                   "canonical_gzip_bytes": (root / (relative + ".gz")).stat().st_size,
                   "station_shards": shard_check,
                   "display_village_count": len(display_by_id), "failure_count": len(errors), "failures": errors})
    status = "needs_correction" if errors else "pass_with_limitations"
    return {"schema_version": 1, "dataset_id": "independent_station_age_catchments_review", "metadata": {
                "reviewer_agent": "/root/national_reference", "producer_imported": False,
                "reviewed_at": datetime.now(ZoneInfo("Asia/Taipei")).isoformat(timespec="seconds"), "status": status,
                "method": "All displayed local+regional bus ID unions and source collision checks, MRT/YouBike IDs and radius/status records; independent FNV-1a UTF-8 routing and full 32-part union/value/metadata/gzip equivalence; independently projected full geometry, vectorized overlap pairs, manually constructed 256-vertex circles, scalar density arithmetic, and union areas for four stations including one regional original ID across all three radii.",
                "limitations": [f"{len(samples)}個站圈數值為分層抽樣重算（包含新增跨市站）；未逐一重算所有站圈，完整ID/半徑/狀態及拓樸集合另作全量核對。",
                                "均勻面積人口估算不等於實際乘客；相鄰站圈、兩套年齡分組不可加總。"]},
            "records": [{"file": relative, "output_path": relative, "record_count": sum(len(points) for points in expected_points.values()),
                         "review_status": status, "reviewed_generated_at": doc["metadata"].get("generated_at"),
                         "population_source": population_source_snapshot(doc["metadata"]),
                         "reviewed_built_at": doc["metadata"].get("built_at"), "sources": doc["metadata"].get("sources", []),
                         "checks": checks, "samples": samples}]}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--month", required=True)
    parser.add_argument("--stations", action="store_true", help="Independently check actual web station layers and sampled circle estimates")
    args = parser.parse_args(argv)
    result = station_review(args.root, args.month) if args.stations else review(args.root, args.month)
    names = ("data/quality/station_age_catchments_review.json",) if args.stations else (
        "data/quality/national_village_age_review.json", "map-data/quality/village_age_geography_review.json")
    for name in names:
        path = args.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(result, ensure_ascii=False, allow_nan=False, indent=2) + "\n", encoding="utf-8")
    failures = result["records"][0]["checks"]["failures"] if result["records"] else ["source/output unavailable"]
    print(json.dumps({"status": result["metadata"]["status"], "failures": failures}, ensure_ascii=False))
    return bool(failures)


if __name__ == "__main__":
    raise SystemExit(main())
