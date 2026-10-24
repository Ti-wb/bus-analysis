"""Estimate registered village age populations inside northern Taiwan station circles.

This module accepts only the independently joined village GeoJSON. It never joins
station IDs to village IDs or guesses population matches from names.
"""
from __future__ import annotations

import math
import re
from copy import deepcopy
from dataclasses import dataclass

from pyproj import Transformer
from shapely import STRtree, transform
from shapely.geometry import Point, shape
from shapely.ops import unary_union

RADII = (200, 350, 500)
# Include the two officially supplied Keelung offshore boundary blocks while
# rejecting distant-island/national station use of this northern-only pipeline.
SUPPORTED_BBOX = (120.9, 24.5, 122.2, 25.7)
OVERLAP_TOLERANCE_M2 = 0.01
COMPLETE_SHARE_TOLERANCE = 1e-8
CIRCLE_QUAD_SEGS = 64
VERIFIED_STATUSES = {"verified", "verified_alias"}
AGE_GROUPS = [
    {"key": "age_0_14", "label": "0–14 歲", "min_age": 0, "max_age": 14, "set": "three_stage"},
    {"key": "age_15_64", "label": "15–64 歲", "min_age": 15, "max_age": 64, "set": "three_stage"},
    {"key": "age_65_plus", "label": "65 歲以上", "min_age": 65, "max_age": 100, "set": "three_stage"},
    {"key": "age_0_5", "label": "0–5 歲", "min_age": 0, "max_age": 5, "set": "planning"},
    {"key": "age_6_17", "label": "6–17 歲", "min_age": 6, "max_age": 17, "set": "planning"},
    {"key": "age_18_64", "label": "18–64 歲", "min_age": 18, "max_age": 64, "set": "planning"},
    {"key": "age_65_74", "label": "65–74 歲", "min_age": 65, "max_age": 74, "set": "planning"},
    {"key": "age_75_plus", "label": "75 歲以上", "min_age": 75, "max_age": 100, "set": "planning"},
]
AGE_GROUP_SETS = {name: [group["key"] for group in AGE_GROUPS if group["set"] == name]
                  for name in ("three_stage", "planning")}


def _number(value, field):
    if value is not None and (type(value) is not int or value < 0):
        raise ValueError(f"{field} must be a nonnegative integer or null")
    return value


def _in_scope(lon, lat):
    west, south, east, north = SUPPORTED_BBOX
    return west <= lon <= east and south <= lat <= north


@dataclass
class Village:
    boundary_id: str
    area_id: str
    geometry: object
    population: int | None
    groups: dict[str, int | None]
    verified: bool
    conflict: bool = False


def _villages(boundaries):
    if (not isinstance(boundaries, dict) or boundaries.get("type") != "FeatureCollection"
            or not isinstance(boundaries.get("metadata"), dict)
            or not isinstance(boundaries.get("features"), list)):
        raise ValueError("Expected the verified village FeatureCollection and metadata")
    crs = boundaries.get("crs")
    if crs is not None and crs.get("properties", {}).get("name") not in {
        "EPSG:4326", "urn:ogc:def:crs:OGC:1.3:CRS84", "urn:ogc:def:crs:EPSG::4326",
    }:
        raise ValueError("Village GeoJSON must use WGS84 / EPSG:4326")
    to_tm = Transformer.from_crs(4326, 3826, always_xy=True)
    records, seen, verified_area_ids = [], set(), set()
    for feature in boundaries["features"]:
        props = feature.get("properties", {})
        boundary_id = props.get("boundary_id")
        area_id = props.get("area_id")
        if not isinstance(boundary_id, str) or not boundary_id or boundary_id in seen:
            raise ValueError("Every boundary_id must be a unique nonempty string")
        seen.add(boundary_id)
        if not isinstance(area_id, str) or not area_id:
            raise ValueError(f"Missing original boundary area_id for {boundary_id}")
        verified = props.get("boundary_join_status") in VERIFIED_STATUSES
        if verified and not re.fullmatch(r"[0-9]{11}", area_id):
            raise ValueError(f"Verified population area_id must be 11 digits: {area_id}")
        if verified and area_id in verified_area_ids:
            raise ValueError(f"Repeated verified village population code: {area_id}")
        if verified:
            verified_area_ids.add(area_id)
        if verified and props.get("population_area_id", area_id) != area_id:
            raise ValueError(f"Verified population code disagrees with boundary code: {area_id}")
        geometry = shape(feature.get("geometry"))
        if geometry.geom_type not in {"Polygon", "MultiPolygon"} or geometry.is_empty or not geometry.is_valid:
            raise ValueError(f"Invalid polygon for boundary {boundary_id}")
        west, south, east, north = geometry.bounds
        if not all(math.isfinite(value) for value in geometry.bounds) or not (
            _in_scope(west, south) and _in_scope(east, north)
        ):
            raise ValueError(f"Unsupported projection scope for boundary {boundary_id}; northern Taiwan only")
        geometry = transform(geometry, to_tm.transform, interleaved=False)
        if not math.isfinite(geometry.area) or geometry.area <= 0:
            raise ValueError(f"Boundary has no measurable area: {boundary_id}")
        # Unverified joins may carry a candidate population. Never use that candidate.
        population = _number(props.get("population"), "population") if verified else None
        ages = props.get("age_counts") if verified else None
        if ages is not None:
            if not isinstance(ages, list) or len(ages) != 101:
                raise ValueError(f"Expected 101 total-sex age bins for {area_id}")
            ages = [_number(value, "age_counts") for value in ages]
            known = sum(value for value in ages if value is not None)
            if population is not None and (known > population or (
                all(value is not None for value in ages) and known != population
            )):
                raise ValueError(f"Age bins disagree with population for {area_id}")
        groups = {}
        for group in AGE_GROUPS:
            cells = ages[group["min_age"]:group["max_age"] + 1] if ages is not None else [None]
            groups[group["key"]] = None if any(value is None for value in cells) else sum(cells)
        records.append(Village(boundary_id, area_id, geometry, population, groups, verified))
    return records, to_tm


def _topology(villages):
    """Quarantine both sides of a positive-area overlap; shared edges are valid."""
    geometries = [village.geometry for village in villages]
    tree = STRtree(geometries)
    pair_count, samples = 0, []
    for i, geometry in enumerate(geometries):
        for candidate in tree.query(geometry, predicate="intersects"):
            j = int(candidate)
            if j <= i:
                continue
            area = geometry.intersection(geometries[j]).area
            if area <= OVERLAP_TOLERANCE_M2:
                continue
            villages[i].conflict = villages[j].conflict = True
            pair_count += 1
            if len(samples) < 30:
                samples.append({"boundary_ids": [villages[i].boundary_id, villages[j].boundary_id],
                                "overlap_m2": round(area, 3)})
    return tree, {"overlap_pair_count": pair_count,
                  "conflicting_boundary_count": sum(village.conflict for village in villages),
                  "overlap_samples": samples}


def _union_area(geometries):
    return unary_union(geometries).area if geometries else 0.0


def _share(geometries, circle_area):
    return min(1.0, max(0.0, _union_area(geometries) / circle_area))


def _complete(share):
    return share >= 1 - COMPLETE_SHARE_TOLERANCE


def _value(value):
    return round(value, 2) if value is not None else None


def _entry(villages, candidates, centre, radius):
    circle = centre.buffer(radius, quad_segs=CIRCLE_QUAD_SEGS)
    intersections = []
    for i in candidates:
        village = villages[int(i)]
        clipped = village.geometry.intersection(circle)
        if clipped.area > 0:
            intersections.append((village, clipped))
    usable = [(village, clipped) for village, clipped in intersections if village.verified and not village.conflict]
    population_rows = [(village, clipped) for village, clipped in usable if village.population is not None]
    share_cache = {}

    def coverage(rows):
        key = tuple(village.boundary_id for village, _ in rows)
        if key not in share_cache:
            share_cache[key] = _share([clipped for _, clipped in rows], circle.area)
        return share_cache[key]

    # The eight groups usually share the same complete village geometries.
    # Reuse their union rather than repeating identical geometric operations.
    boundary_share = coverage(intersections)
    data_share = coverage(usable)
    population_share = coverage(population_rows)
    known_population = (sum(village.population * clipped.area / village.geometry.area
                            for village, clipped in population_rows) if population_rows else None)
    estimates, known_estimates, group_shares, group_unknown = {}, {}, {}, {}
    for group in AGE_GROUPS:
        key = group["key"]
        known_rows = [(village, clipped) for village, clipped in usable if village.groups[key] is not None]
        # A missing published population prevents a claim of a complete estimate,
        # even when all cells in this particular age interval happen to be known.
        complete_rows = [(village, clipped) for village, clipped in known_rows if village.population is not None]
        share = coverage(complete_rows)
        known_estimate = (sum(village.groups[key] * clipped.area / village.geometry.area
                              for village, clipped in known_rows) if known_rows else None)
        estimates[key] = _value(known_estimate) if _complete(share) else None
        known_estimates[key] = _value(known_estimate)
        group_shares[key] = round(share, 6)
        group_unknown[key] = sum(not village.verified or village.conflict or village.population is None
                                 or village.groups[key] is None for village, _ in intersections)
    conflict_count = sum(village.conflict for village, _ in intersections)
    complete = _complete(population_share) and all(value is not None for value in estimates.values())
    status = ("no_boundary" if not intersections else "geometry_conflict" if conflict_count else
              "complete" if complete else "partial")
    return {
        "radius_m": radius, "status": status,
        "population_estimate": _value(known_population) if _complete(population_share) else None,
        "population_known_estimate": _value(known_population),
        "age_group_estimates": estimates, "age_group_known_estimates": known_estimates,
        "boundary_share": round(boundary_share, 6), "data_share": round(data_share, 6),
        "population_data_share": round(population_share, 6), "age_group_data_shares": group_shares,
        "outside_boundary_share": round(1 - boundary_share, 6),
        "village_count": len(intersections),
        "unknown_village_count": sum(not village.verified or village.conflict or village.population is None
                                     or any(value is None for value in village.groups.values())
                                     for village, _ in intersections),
        "unmatched_village_count": sum(not village.verified for village, _ in intersections),
        "missing_population_village_count": sum(village.verified and village.population is None
                                                for village, _ in intersections),
        "geometry_conflict_village_count": conflict_count,
        "age_group_unknown_village_counts": group_unknown,
    }


def build_age_catchments(station_points, boundaries, radii=RADII):
    """Return the web envelope for kind → original station ID → radii entries.

    All coordinates are WGS84. Only the northern Taiwan scope stated in metadata
    is supported. Counts are from sex=total; groups in different sets overlap.
    """
    if (not radii or any(type(radius) not in {int, float} or not math.isfinite(radius) or radius <= 0
                         for radius in radii) or len(set(radii)) != len(radii)):
        raise ValueError("radii must be distinct positive metre distances")
    if not isinstance(station_points, dict):
        raise ValueError("station_points must be a kind → station ID → WGS84 coordinate mapping")
    villages, to_tm = _villages(boundaries)
    tree, topology = _topology(villages)
    records = {}
    statuses = {}
    for kind, points in station_points.items():
        if not isinstance(kind, str) or not isinstance(points, dict):
            raise ValueError("Station kinds must contain ID → coordinate mappings")
        records[kind] = {}
        for station_id, coordinate in points.items():
            if not isinstance(station_id, str) or not station_id:
                raise ValueError("Station IDs must be original nonempty strings")
            if (not isinstance(coordinate, (list, tuple)) or len(coordinate) != 2
                    or any(type(value) not in {int, float} or not math.isfinite(value) for value in coordinate)):
                raise ValueError(f"Invalid WGS84 station point: {kind}/{station_id}")
            if not _in_scope(*coordinate):
                raise ValueError(f"Unsupported projection scope for station {kind}/{station_id}; northern Taiwan only")
            centre = Point(to_tm.transform(*coordinate))
            # Query the bounding square, not a coarser circle approximation whose
            # shorter chords could omit a small polygon near the outer radius.
            candidates = tree.query(centre.buffer(max(radii)).envelope, predicate="intersects")
            entries = [_entry(villages, candidates, centre, radius) for radius in radii]
            records[kind][station_id] = entries
            for entry in entries:
                statuses[entry["status"]] = statuses.get(entry["status"], 0) + 1
    meta = deepcopy(boundaries["metadata"])
    source_limitations = meta.get("limitations", [])
    if not isinstance(source_limitations, list):
        source_limitations = [source_limitations]
    meta.update({
        "source_dataset": "verified village-age boundary join; no station-to-village ID join",
        "units": {"population_estimate": "estimated persons", "age_group_estimates": "estimated persons",
                  "area_shares": "fraction of station circle area"},
        "age_groups": deepcopy(AGE_GROUPS), "age_group_sets": deepcopy(AGE_GROUP_SETS),
        "age_counts_definition": "Indices 0–99 are exact ages; index/max_age 100 includes all ages 100 and older.",
        "method": "各村里面積中位於站圈內的比例 × 該里戶籍人口；假設每個村里內各年齡人口均勻分布。",
        "projection": {"source": "EPSG:4326", "area_and_distance": "EPSG:3826", "unit": "metre",
                       "supported_bbox_wgs84": list(SUPPORTED_BBOX), "scope": "北北基地圖與鄰近站位；不支援全國站點或遠島。"},
        "circle": {"quad_segs": CIRCLE_QUAD_SEGS, "definition": "256-sided approximation of a straight-line circle"},
        "coverage_definition": {
            "boundary_share": "Union of all supplied polygons intersecting the circle / circle area.",
            "data_share": "Union of verified/verified_alias polygons without overlap conflicts / circle area.",
            "population_data_share": "Usable polygon union with known published population / circle area.",
            "age_group_data_shares": "Usable polygon union with known published population and every age cell in the group / circle area.",
            "outside_boundary_share": "Area outside the supplied polygon union; never presumed to have zero residents.",
        },
        "missing_value_policy": "Full estimates are null unless the relevant data cover the complete circle. Known partial estimates are separate; no known contributing source means null, while an observed zero stays 0. Missing age cells propagate only to groups containing those cells. A null published population prevents complete estimates.",
        "topology_policy": {"overlap_tolerance_m2": OVERLAP_TOLERANCE_M2,
                            "action": "Both polygons in each larger positive-area overlap are excluded from estimates and counted as geometry conflicts; coverage always uses union area.",
                            "complete_share_tolerance": COMPLETE_SHARE_TOLERANCE},
        "quality": {**topology, "boundary_count": len(villages),
                    "station_count": sum(len(points) for points in station_points.values()),
                    "station_radius_status_counts": statuses},
        "limitations": source_limitations + [
            "站圈年齡人口為均勻密度面積分攤估算，不是實測居住位置、搭乘人次、通勤 OD 或真實乘車需求。",
            "直線圓未使用步行路網、河川、高架阻隔或捷運出入口；站位與人口、里界的來源及期別各自獨立。",
            "相鄰站圈會重疊，各站估算不可加總；three_stage 與 planning 是兩套重疊分組，不可把八組一起加總。",
            "缺少里界、未匹配、名稱不符、人口缺值與幾何衝突均不補零；海岸外或未編定地亦不假定零人口。",
            "EPSG:3826 計算限定北北基及鄰近站點；本輸出不宣稱全國站圈或遠島投影適用性。",
        ],
    })
    return {"schema_version": 1, "dataset_id": "web_station_age_catchments", "radii": list(radii),
            "metadata": meta, "records": records}
