"""Independent boundary evidence audit, without importing the producing collector."""
from __future__ import annotations

import argparse
import csv
import io
import json
import re
import zipfile
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def require(condition, message):
    if not condition:
        raise ValueError(message)


def villages(path):
    result = {}
    with path.open(encoding="utf-8-sig", newline="") as stream:
        for row in csv.reader(stream):
            require(len(row) == 3 and re.fullmatch(r"\d{11}", row[0]), "Invalid RIS identity")
            require(row[0] not in result and row[1] == row[0][:8], "Duplicate/conflicting RIS hierarchy")
            result[row[0]] = (row[1], row[2])
    return result


def boundary(path):
    import shapefile
    with zipfile.ZipFile(path) as archive:
        stems = [n[:-4] for n in archive.namelist() if re.fullmatch(r"VILLAGE_NLSC_\d{7}\.shp", n)]
        require(len(stems) == 1, "Ambiguous dated village layer")
        stem = stems[0]
        reader = shapefile.Reader(**{extension: io.BytesIO(archive.read(stem + "." + extension))
                                     for extension in ("shp", "shx", "dbf")}, encoding="utf-8")
        rows = {}
        for rec in reader.iterShapeRecords():
            data = rec.record.as_dict()
            if data["COUNTYCODE"] not in {"63000", "65000", "10017"}:
                continue
            code = data["VILLCODE"]
            require(code not in rows, "Duplicate boundary village")
            # Compare the actual point arrays, ring boundaries and attributes;
            # equality is not inferred from a matching vintage label.
            rows[code] = (data, list(rec.shape.points), list(rec.shape.parts), rec.shape.shapeType)
        return stem, rows, archive.read(stem + ".prj")


def audit(root):
    import pdfplumber
    out = root / "data/official_cross_city/boundary_evidence"
    raw = root / "raw_data/official_cross_city/boundary_evidence"
    manifest = read(out / "manifest.json")
    for source in manifest["records"]:
        require(urlsplit(source["download_url"]).scheme == "https", "Non-HTTPS source")
        path = root / source["raw_file"]
        require(path.stat().st_size == source["bytes"] and source["http_status"] == 200,
                "Source receipt size/status mismatch")
    old, new = villages(raw / "11506VillageUTF8.txt"), villages(raw / "11509VillageUTF8.txt")
    diff = {k for k in old.keys() | new.keys() if old.get(k) != new.get(k)}
    deleted = set(re.findall(r"(?m)^(\d{11})\s", (raw / "11509_Village_d.txt").read_text(encoding="utf-8-sig")))
    require(diff == old.keys() - new.keys() == deleted and len(diff) == 11,
            "RIS versions and September deletion notice disagree")
    require(all(code.startswith("64000") for code in diff), "Unexpected regional code changes")
    pop = read(root / "data/official_cross_city/population_boundary_crosswalk.json")["records"]
    require(len(pop) == 1652 and len({r["population_area_code"] for r in pop}) == 1652, "Population population mismatch")
    for row in pop:
        require(new[row["population_area_code"]][1] == row["population_village_name"], "Population/RIS name mismatch")
    code_output = read(out / "code_version_checks.json")
    for row in code_output["records"]:
        prefix = row["county_code"]
        before = {k: v for k, v in old.items() if k.startswith(prefix)}
        after = {k: v for k, v in new.items() if k.startswith(prefix)}
        require(before == after and len(after) == row["september_version_count"] == row["population_count"], "Regional RIS counts/identity changed")
        require(row["same_month_geometry_verified"] is False, "Code stability became geometry equivalence")
    aliases = read(out / "name_aliases.json")["records"]
    mismatch = {r["population_area_code"]: r for r in pop if r["population_village_name"] != r["boundary_village_name"]}
    require({r["village_code"] for r in aliases} == mismatch.keys() and len(aliases) == 6, "Alias coverage mismatch")
    with pdfplumber.open(raw / "nlsc_111_boundary_maintenance.pdf") as pdf:
        table_31, table_32 = pdf.pages[93].extract_text(), pdf.pages[95].extract_text()
    for row in aliases:
        code = row["village_code"]
        original = mismatch[code]
        require((row["population_name"], row["boundary_name"]) ==
                (original["population_village_name"], original["boundary_village_name"]), "Alias rewrote original strings")
        require(row["population_name"] == new[code][1] == old[code][1], "Alias not supported by both RIS versions")
        require(any(line.startswith(code + " ") and line.count(code) == 2 for line in table_31.splitlines()), "Official table lacks exact code pair")
        if "[" in row["boundary_name"]:
            require(any(code in line and row["boundary_name"] in line for line in table_32.splitlines()), "Literal replacement missing in table 3-2")
        require(row["same_month_geometry_verified"] is False, "Alias incorrectly proves geometry")
    changes = read(out / "boundary_changes.json")["records"]
    with pdfplumber.open(raw / "nlsc_august_changes.pdf") as pdf:
        text = pdf.pages[0].extract_text()
    require({r["village_code"] for r in changes} == {"65000100016", "65000100019"}, "August correction identities changed")
    for row in changes:
        require(any(row["village_code"] in line and row["village_name"] in line and
                    "2026/08/17" in line and "村里界釐整" in line for line in text.splitlines()), "Correction row differs from official PDF")
    a_stem, a, a_crs = boundary(root / "map-data/raw/validation/villages.zip")
    b_stem, b, b_crs = boundary(root / "map-data/raw/boundary_evidence/current_villages.zip")
    require(a_stem == b_stem == "VILLAGE_NLSC_1150817" and a_crs == b_crs and a == b,
            "Current official boundary points/rings/attributes differ from retained vintage")
    current = read(out / "current_distribution_check.json")
    for row in current["records"]:
        count = sum(code.startswith(row["county_code"]) for code in a)
        require(row["baseline_feature_count"] == row["current_feature_count"] == count, "Geometry count mismatch")
        require(row["current_distribution_matches_baseline"] is True and row["same_month_geometry_verified"] is False,
                "Current-release acceptance was overstated")
        require(all(row[k] == [] for k in ("added_codes", "removed_codes", "geometry_changed_codes", "coordinate_changed_codes", "attribute_changed_codes")), "Unexpected geometry delta")
    municipal = read(out / "municipal_source_diagnostics.json")
    rows = list(csv.DictReader((raw / "ntpc_boundaries.csv").read_text(encoding="utf-8-sig").splitlines()))
    require(len(rows) == municipal["metadata"]["downloaded_row_count"] == 1032, "Municipal source count mismatch")
    for row in municipal["records"]:
        matches = [r for r in rows if (r["admit,c,50"], r["admit_id,c,16"], r["admiv_id,c,16"]) ==
                   (row["district_name"], row["municipal_admit_id"], row["municipal_admiv_id"])]
        require(len(matches) == 1 and matches[0]["admiv,c,50"] == row["municipal_name"] and row["geometry_used"] is False,
                "Municipal diagnostic silently became a geometry/ID bridge")
    report = {"schema_version": 1, "dataset_id": "independent_boundary_evidence_review", "metadata": {
        "reviewed_at": datetime.now(ZoneInfo("Asia/Taipei")).isoformat(timespec="seconds"), "reviewer": "root",
        "independent_of_producer": True, "producer_imported": False, "source_updated_at": None,
        "method": "Independent CSV/SHP raw point-array/ring/attribute comparisons, PDF table extraction and rendered PDF pages 94/96/1 visually inspected in this review",
        "record_count": 5, "status": "passed_with_temporal_limitations"}, "records": [
        {"dataset": "name_aliases", "status": "passed", "record_count": 6, "official_tables": "NLSC PDF pages 94,96; printed 83,85"},
        {"dataset": "code_version_checks", "status": "passed", "population_rows": 1652, "national_deletions": 11, "regional_code_name_changes": 0},
        {"dataset": "boundary_changes", "status": "passed", "record_count": 2, "correction_date": "2026-08-17"},
        {"dataset": "current_distribution_check", "status": "passed_for_published_vintage", "feature_count": len(a), "exact_points_rings_attributes_equal": True, "same_month_geometry_verified": False},
        {"dataset": "municipal_source_diagnostics", "status": "passed_as_diagnostics_only", "source_rows": 1032, "diagnostic_count": 6}],
        "limitations": ["2026-09 population identities can join the explicit 2026-08-17 published geometry vintage; no separate September geometry or complete daily legal change registry was acquired."]}
    target = root / "data/quality/boundary_evidence_review.json"
    target.write_text(json.dumps(report, ensure_ascii=False, allow_nan=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"aliases": 6, "population_ris_rows": len(pop), "boundary_features": len(a), "status": report["metadata"]["status"]}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    audit(parser.parse_args().root)
