"""Independently reconcile the validation index with delivered files and reviews."""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime
import gzip
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CATALOG = Path("data/official_cross_city/validation_catalog.json")
FAMILIES = {
    "data/official_cross_city/regional_routes": "data/quality/regional_validation_review.json",
    "data/official_cross_city/boundary_evidence": "data/quality/boundary_evidence_review.json",
    "data/official_cross_city/id_evidence": "data/quality/id_evidence_review.json",
    "data/official_cross_city/service_coverage": "data/quality/service_coverage_review.json",
    "data/official_cross_city/taipei_id_evidence": "data/quality/taipei_id_evidence_review.json",
    "data/official_cross_city/regional_service_evidence": "data/quality/regional_service_evidence_review.json",
    "data/official_cross_city/walking_transfers": "map-data/quality/walking_evidence_review.json",
    "data/tdx_bus_travel_time/observations": "data/quality/tdx_bus_travel_time_review.json",
    "data/tdx_road_traffic/observations": "data/quality/tdx_road_traffic_review.json",
    "data/tdx_road_traffic/availability": "data/quality/tdx_road_traffic_review.json",
}
SPECIAL = {
    "data/official_cross_city/population_2026-09.json": "data/quality/regional_validation_review.json",
    "data/official_cross_city/population_boundary_crosswalk.json": "data/quality/regional_validation_review.json",
    "map-data/admin/keelung_villages.geojson": "map-data/quality/keelung_review.json",
    "map-data/walking/further_evidence.json": "map-data/quality/walking_further_evidence_review.json",
    "data/tdx_bus_travel_time/coverage.json": "data/quality/tdx_bus_travel_time_review.json",
    "data/tdx_bus_travel_time/delivery.json": "data/quality/tdx_bus_travel_time_review.json",
    "data/tdx_road_traffic/delivery.json": "data/quality/tdx_road_traffic_review.json",
    "data/tdx_road_traffic/reference_diagnostics.json": "data/quality/tdx_road_traffic_review.json",
}
EVIDENCE_KINDS = {
    "data/official_cross_city/service_coverage/coverage.json": "acquisition_coverage",
    "data/tdx_bus_travel_time/coverage.json": "acquisition_coverage",
    "data/tdx_bus_travel_time/delivery.json": "acquisition_delivery",
    "data/tdx_road_traffic/delivery.json": "acquisition_delivery",
    "data/tdx_road_traffic/reference_diagnostics.json": "id_reference_diagnostics",
}
ALLOWED_CONTROLS = {
    "data/official_cross_city/source_registry.json": "source registry; not delivered transport observations",
    "data/tdx_bus_travel_time/route_catalog.json": "dated route reference; not measured travel times",
    "data/tdx_road_traffic/api_catalog.json": "API operation reference",
    "data/tdx_road_traffic/historical_catalog.json": "historical retrieval reference",
    "data/tdx_road_traffic/request_plan.json": "request plan; not acquired observations",
    "data/tdx_road_traffic/field_dictionary.json": "official field dictionary",
}


def read(path):
    body = path.read_bytes()
    if path.suffix == ".gz":
        body = gzip.decompress(body)
    return json.loads(body)


def strict_equal(actual, expected):
    if type(actual) is not type(expected):
        return False
    if isinstance(expected, dict):
        return actual.keys() == expected.keys() and all(strict_equal(actual[key], value) for key, value in expected.items())
    if isinstance(expected, list):
        return len(actual) == len(expected) and all(strict_equal(a, b) for a, b in zip(actual, expected))
    return actual == expected


def rows_of(document):
    rows = document.get("features") if document.get("type") == "FeatureCollection" else document.get("records")
    if not isinstance(rows, list):
        raise ValueError("Indexed document lacks normalized records or GeoJSON features")
    return rows


def review_signals(metadata, rows):
    reasons, row_count = [], 0
    if metadata.get("requires_source_review"):
        reasons.append("metadata.requires_source_review")
    if metadata.get("source_review_flags"):
        reasons.append("metadata.source_review_flags")
    for index, value in enumerate(rows, 1):
        if not isinstance(value, dict):
            continue
        containers = [value]
        if value.get("type") == "Feature" and isinstance(value.get("properties"), dict):
            containers.append(value["properties"])
        hits = []
        for row in containers:
            if row.get("requires_source_review"):
                hits.append("requires_source_review")
            if row.get("source_review_flags"):
                hits.append("source_review_flags")
            if str(row.get("linkage_status", "")).startswith("unresolved"):
                hits.append("unresolved_linkage_status")
            status = str(row.get("status", ""))
            if "requires_review" in status or "requires_source_review" in status or status.startswith(("unverified", "unresolved", "not_validated")):
                hits.append("uncertain_status")
        if hits:
            row_count += 1
            if len(reasons) < 8:
                reasons.append({"source_record_number": index, "signals": sorted(set(hits))})
    return bool(reasons or row_count), row_count, reasons


def period_of(metadata):
    for key in ("statistical_period", "period", "source_period", "population_period"):
        if metadata.get(key):
            return metadata[key]
    if metadata.get("requested_date"):
        return {key: metadata.get(key) for key in ("requested_date", "version_start", "version_end")}
    return None


def quality_status(report, path, dataset_id):
    matches = [row for row in report.get("records", []) if any(row.get(key) == path for key in ("output_path", "path", "output_file"))]
    if not matches:
        stem = Path(path).stem
        matches = [row for row in report.get("records", []) if any(row.get(key) in {dataset_id, stem} for key in ("dataset_id", "dataset"))]
    if len(matches) > 1:
        raise ValueError("Ambiguous independent report matches for " + path)
    selected = matches[0] if matches else {}
    if selected.get("review_status") or selected.get("status"):
        return selected.get("review_status") or selected["status"], selected, "dataset_record"
    metadata = report.get("metadata", {})
    for key in ("independent_review_status", "overall_status", "status"):
        if metadata.get(key):
            return metadata[key], selected, "aggregate_report"
    return "pending", selected, "missing_report_status"


def source_time(metadata):
    return metadata["source_updated_at"] if "source_updated_at" in metadata else metadata.get("source_data_time")


def inspect_entry(entry, document, report, expected_report):
    rows, metadata = rows_of(document), document["metadata"]
    dataset_id = document.get("dataset_id") or metadata.get("dataset_id")
    path = entry["path"]
    needs_review, uncertain_rows, reasons = review_signals(metadata, rows)
    expected_status, matched, status_basis = quality_status(report, path, dataset_id)
    expected = {
        "dataset_id": dataset_id, "record_count": len(rows),
        "kind": metadata.get("data_kind") or EVIDENCE_KINDS.get(path) or ("geographic_features" if document.get("type") == "FeatureCollection" else "normalized_records"),
        "grain": metadata.get("grain"), "source_updated_at": source_time(metadata), "period": period_of(metadata),
        "scope_status": metadata.get("scope_status"), "units": metadata.get("units", metadata.get("field_units")),
        "requires_source_review": needs_review,
        "data_acceptance_status": "requires_source_review" if needs_review else "within_documented_scope_only",
        "independent_review": {"status": expected_status, "report": expected_report}, "limitations": metadata.get("limitations", []),
    }
    errors = ["catalog field mismatch: " + key for key, value in expected.items() if not strict_equal(entry.get(key), value)]
    if expected_status == "pending":
        errors.append("No completed independent report status")
    if matched.get("record_count") is not None and matched["record_count"] != len(rows):
        errors.append("Independent per-file record count differs from delivered output")
    nested_travel_rows = None
    if matched.get("source_station_time_row_count") is not None:
        if not all(isinstance(row.get("travel_times"), list) for row in rows):
            errors.append("Travel model lacks nested station-time rows")
        else:
            nested_travel_rows = sum(len(row["travel_times"]) for row in rows)
            if matched["source_station_time_row_count"] != nested_travel_rows or matched.get("source_model_count") != len(rows):
                errors.append("Travel review model/nested station-time counts differ")
    if matched.get("reviewed_generated_at") is not None and matched["reviewed_generated_at"] != metadata.get("generated_at"):
        errors.append("Independent report reviews another generation timestamp")
    unknown = Counter()
    for row in rows:
        if not isinstance(row, dict):
            continue
        for key, value in row.items():
            if (key == "status" or key.endswith("_status")) and isinstance(value, str) and (
                    value.startswith(("indeterminate", "unverified", "unresolved", "not_requested", "not_acquired", "requires_review", "requires_source_review"))):
                unknown[key + ":" + value] += 1
    if entry.get("data_acceptance_status") not in {"within_documented_scope_only", "requires_source_review"}:
        errors.append("Index claims an unqualified acceptance status")
    return {"output_path": path, "dataset_id": dataset_id, "record_count": len(rows), "storage": "gzip" if path.endswith(".gz") else "plain_json",
            "format": "GeoJSON" if document.get("type") == "FeatureCollection" else "JSON envelope", "status": "pass" if not errors else "failed",
            "independent_review_report": expected_report, "independent_review_status": expected_status, "review_status_basis": status_basis,
            "requires_source_review": needs_review, "flagged_source_record_count": uncertain_rows, "review_signal_examples": reasons,
            "nested_travel_time_row_count": nested_travel_rows,
            "bounded_unknown_source_status_counts": dict(unknown), "reviewed_generated_at": metadata.get("generated_at"), "errors": errors}


def delivered_paths(root):
    result = {}
    for directory, report in FAMILIES.items():
        if not (root / directory).exists():
            continue
        for path in (root / directory).iterdir():
            if path.name != "manifest.json" and path.name.endswith((".json", ".json.gz", ".geojson")):
                result[path.relative_to(root).as_posix()] = report
    for path, report in SPECIAL.items():
        if (root / path).is_file():
            result[path] = report
    for path in (root / "data/official_cross_city").glob("*crosswalk.json"):
        name = path.relative_to(root).as_posix()
        result.setdefault(name, "data/quality/id_crosswalk_review.json")
    for path in (root / "map-data/walking").glob("*.geojson"):
        result[path.relative_to(root).as_posix()] = "map-data/quality/walking_evidence_review.json"
    return result


def excluded_inventory(root, indexed):
    result = []
    for directory in ("data/official_cross_city", "data/tdx_bus_travel_time", "data/tdx_road_traffic", "map-data/walking"):
        for path in sorted((root / directory).rglob("*")):
            if not path.is_file() or not path.name.endswith((".json", ".json.gz", ".geojson")):
                continue
            name = path.relative_to(root).as_posix()
            if name in indexed:
                continue
            if path.name in {"manifest.json", "source_manifest.json"}:
                reason = "source manifest; acquisition receipts are not normalized observations"
            elif name == CATALOG.as_posix():
                reason = "index itself; not recursively indexed"
            else:
                reason = ALLOWED_CONTROLS.get(name)
            result.append({"path": name, "reason": reason or "unclassified nonmanifest output; must be reviewed for omission", "classified_exclusion": reason is not None})
    return result


def run(root):
    started = datetime.now().astimezone().isoformat()
    catalog_body = (root / CATALOG).read_bytes()
    catalog = json.loads(catalog_body)
    expected_files = delivered_paths(root)
    actual = catalog["records"]
    paths = [row["path"] for row in actual]
    errors = []
    if len(set(paths)) != len(paths):
        errors.append("Duplicate indexed paths")
    if catalog["metadata"]["record_count"] != len(actual):
        errors.append("Catalog metadata count mismatch")
    if catalog["dataset_id"] != "additional_transport_validation_catalog" or catalog["metadata"]["kind"] != "dataset_index_not_observations":
        errors.append("Index identity/kind mismatch")
    missing, unexpected = sorted(set(expected_files) - set(paths)), sorted(set(paths) - set(expected_files))
    if missing or unexpected:
        errors.append("Delivered population mismatch")
    reports = {name: read(root / name) for name in set(expected_files.values()) if (root / name).exists()}
    reviewed = []
    for entry in actual:
        path = Path(entry["path"])
        if path.is_absolute() or ".." in path.parts or not (root / path).is_file():
            errors.append("Missing or invalid indexed local path: " + str(path))
            continue
        report_name = expected_files.get(str(path))
        if report_name is None or report_name not in reports:
            errors.append("Wrong/missing quality report: " + str(path))
            continue
        reviewed.append(inspect_entry(entry, read(root / path), reports[report_name], report_name))
    exclusions = excluded_inventory(root, set(paths))
    if any(not row["classified_exclusion"] for row in exclusions):
        errors.append("Unclassified nonmanifest deliverable outside index")
    if (root / CATALOG).read_bytes() != catalog_body or delivered_paths(root) != expected_files:
        errors.append("Index or delivered-file population changed during review; retry a stable snapshot")
    travel = read(root / "data/tdx_bus_travel_time/coverage.json")
    report = {"schema_version": 1, "dataset_id": "validation_catalog_independent_review", "metadata": {
        "reviewer_agent": "/root/crosswalk", "review_started_at": started, "reviewed_at": datetime.now().astimezone().isoformat(),
        "status": "pass_with_documented_source_limitations" if not errors and all(row["status"] == "pass" for row in reviewed) else "failed",
        "producer_imported": False, "method": "Independent file inventory and JSON/gzip/GeoJSON parsing, exact metadata projection, row/meta source-review signals, dataset-specific quality-record status matching and omission audit. Catalog producer not imported.",
        "catalog_path": CATALOG.as_posix(), "indexed_dataset_count": len(actual), "reviewed_dataset_count": len(reviewed),
        "catalog_record_count_claim": catalog["metadata"]["record_count"], "missing_delivered_paths": missing, "unexpected_indexed_paths": unexpected,
        "global_errors": errors, "file_error_count": sum(bool(row["errors"]) for row in reviewed),
        "requires_source_review_dataset_count": sum(row["requires_source_review"] for row in reviewed),
        "kind_counts": dict(Counter(row["kind"] for row in actual)), "storage_counts": dict(Counter(row["storage"] for row in reviewed)),
        "travel_snapshot": {key: travel["metadata"].get(key) for key in ("request_count", "successful_response_count", "source_unavailable_count", "technical_failure_count", "travel_time_row_count", "missing_run_time_count", "taipei_route_uids_retained")},
        "scope": "The delivered additive transport/evidence families listed by the independent inventory; excludes manifests, API/field/retrieval references and the index itself.",
        "exclusions": exclusions,
        "limitations": ["This checks the index and its review pointers, not a second interpretation of every original transport source.",
            "within_documented_scope_only never means global data acceptance; unknown publication states, bounded acquisition and unresolved walking/ID limits remain in original records and listed limitations.",
            "Acquisition coverage/delivery and ID-reference diagnostics have separate kinds and are not counted as measured traffic or travel-time observations.",
            "Travel endpoint observations grow by batch. Counts apply to the recorded local snapshot and must be re-audited after new files or reports arrive."]}, "records": reviewed}
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args()
    report = run(args.root.resolve())
    path = args.root / "data/quality/validation_catalog_review.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps({key: report["metadata"][key] for key in ("status", "indexed_dataset_count", "reviewed_dataset_count", "file_error_count", "global_errors", "missing_delivered_paths")}, ensure_ascii=False))
    if report["metadata"]["status"] == "failed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
