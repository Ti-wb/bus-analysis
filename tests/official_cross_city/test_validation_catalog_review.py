"""Independent catalog audit guards; does not import the catalog producer."""
import unittest
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory

from scripts import review_validation_catalog as review


class ValidationCatalogReviewTests(unittest.TestCase):
    def test_new_schedule_does_not_inherit_old_endpoint_aggregate_pass(self):
        report = {"metadata": {"status": "pass_with_source_limitations"}, "records": [
            {"path": "data/official_cross_city/service_coverage/Keelung_Schedule.json.gz", "status": "pass_with_source_limitations"}]}
        status, matched, basis = review.quality_status(report, "data/official_cross_city/service_coverage/Taipei_Schedule.json.gz", "taipei_schedule",
                                                      "data/quality/service_coverage_review.json")
        self.assertEqual(status, "pending_missing_dataset_review")
        self.assertEqual(matched, {})
        self.assertEqual(basis, "missing_dataset_record")

    def test_reusing_old_dataset_id_does_not_certify_a_different_explicit_path(self):
        report = {"metadata": {"status": "passed"}, "records": [{"path": "old.json", "dataset_id": "same_id", "status": "passed"}]}
        self.assertEqual(review.quality_status(report, "new.json", "same_id")[0], "pending_missing_dataset_review")

    def test_aggregate_exception_is_exact_path_and_report(self):
        report = {"metadata": {"status": "passed"}, "records": []}
        path = "data/official_cross_city/service_coverage/coverage.json"
        self.assertEqual(review.quality_status(report, path, "coverage", "data/quality/service_coverage_review.json")[0], "passed")
        self.assertEqual(review.quality_status(report, path, "coverage", "unrelated_report.json")[0], "pending_missing_dataset_review")
        self.assertEqual(review.quality_status(report, path.replace("coverage.json", "new_coverage.json"), "coverage")[0], "pending_missing_dataset_review")

    def test_present_file_row_without_status_does_not_fall_back_to_aggregate(self):
        path = "data/official_cross_city/service_coverage/coverage.json"
        report = {"metadata": {"status": "passed"}, "records": [{"path": path, "record_count": 8}]}
        self.assertEqual(review.quality_status(report, path, "coverage")[0], "pending_missing_dataset_review")

    def test_same_counts_cannot_hide_changed_receipt_downloads_or_ids(self):
        receipts = [{"source_id": "page_0", "downloaded_at": "2026-10-24T12:00:00+08:00", "bytes": 20},
                    {"source_id": "page_100", "downloaded_at": "2026-10-24T12:00:15+08:00", "bytes": 22}]
        metadata = {"sources": deepcopy(receipts)}
        matched = {"reviewed_source_ids": ["page_0", "page_100"], "source_downloaded_at": [row["downloaded_at"] for row in receipts]}
        self.assertEqual(review.review_snapshot_errors(metadata, {}, matched), [])
        metadata["sources"][0]["downloaded_at"] = "2026-10-25T12:00:00+08:00"
        self.assertTrue(review.review_snapshot_errors(metadata, {}, matched))
        metadata["sources"] = list(reversed(receipts))
        self.assertTrue(review.review_snapshot_errors(metadata, {}, matched))

    def test_complete_receipt_requires_exact_source_snapshot(self):
        source = {"source_id": "route_A", "download_url": "https://official.example/route/A", "raw_file": "raw_data/a.json", "bytes": 10,
                  "downloaded_at": "2026-10-24T12:00:00+08:00", "http_status": 200}
        self.assertEqual(review.review_snapshot_errors({"sources": [source]}, {}, {"source": source}), [])
        changed = dict(source, bytes=11)
        self.assertTrue(review.review_snapshot_errors({"sources": [changed]}, {}, {"source": source}))
        self.assertTrue(review.review_snapshot_errors({"sources": [source, source]}, {}, {"source": source}))

    def test_generation_and_structured_walking_generation_are_checked(self):
        matched = {"reviewed_generated_at": "2026-10-24T12:00:00+08:00"}
        self.assertTrue(review.review_snapshot_errors({"generated_at": "2026-10-24T13:00:00+08:00"}, {}, matched))
        report = {"metadata": {"reviewed_generated_at": {"graphs": "2026-10-24T12:00:00+08:00", "transfers": "2026-10-24T12:00:00+08:00"}}}
        self.assertEqual(review.review_snapshot_errors({"generated_at": "2026-10-24T12:00:00+08:00"}, report, {}, "folder/graphs.json.gz"), [])
        self.assertTrue(review.review_snapshot_errors({"generated_at": "2026-10-24T13:00:00+08:00"}, report, {}, "folder/graphs.json.gz"))

    def test_catalog_claim_of_pass_is_rejected_for_changed_generation(self):
        document = {"dataset_id": "sample", "metadata": {"generated_at": "2026-10-24T13:00:00+08:00"}, "records": [{}]}
        entry = {"path": "sample.json", "dataset_id": "sample", "record_count": 1, "kind": "normalized_records", "grain": None,
                 "source_updated_at": None, "period": None, "scope_status": None, "units": None, "requires_source_review": False,
                 "data_acceptance_status": "within_documented_scope_only", "independent_review": {"status": "passed", "report": "review.json"}, "limitations": []}
        report = {"metadata": {"status": "passed"}, "records": [{"path": "sample.json", "status": "passed", "reviewed_generated_at": "2026-10-24T12:00:00+08:00"}]}
        result = review.inspect_entry(entry, document, report, "review.json")
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["independent_review_status"], "pending_source_snapshot_review")

    def test_frozen_inventory_is_excluded_but_derived_coverage_is_required(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            folder = root / "data/official_cross_city/travel_time_coverage"
            folder.mkdir(parents=True)
            for name in ("source_inventory.json", "summary.json", "reference_models.json.gz"):
                (folder / name).touch()
            delivered = review.delivered_paths(root)
            self.assertNotIn("data/official_cross_city/travel_time_coverage/source_inventory.json", delivered)
            self.assertIn("data/official_cross_city/travel_time_coverage/summary.json", delivered)
            self.assertIn("data/official_cross_city/travel_time_coverage/reference_models.json.gz", delivered)

    def test_dataset_specific_diagnostic_status_overrides_aggregate(self):
        report = {"metadata": {"status": "passed_with_temporal_limitations"}, "records": [
            {"dataset": "municipal_source_diagnostics", "status": "passed_as_diagnostics_only"}]}
        result = review.quality_status(report, "data/official_cross_city/boundary_evidence/municipal_source_diagnostics.json", "other_id")
        self.assertEqual(result[0], "passed_as_diagnostics_only")
        self.assertEqual(result[2], "dataset_record")

    def test_conflicting_report_matches_are_rejected(self):
        report = {"records": [{"path": "one.json", "status": "pass"}, {"output_path": "one.json", "status": "failed"}]}
        with self.assertRaises(ValueError):
            review.quality_status(report, "one.json", "one")

    def test_flags_and_unverified_status_cannot_disappear_even_when_review_passes(self):
        document = {"dataset_id": "sample", "metadata": {"period": "2026-10"}, "records": [{"source_review_flags": ["position_conflict"]}]}
        entry = {"path": "sample.json", "dataset_id": "sample", "record_count": 1, "kind": "normalized_records", "grain": None,
                 "source_updated_at": None, "period": "2026-10", "scope_status": None, "units": None,
                 "requires_source_review": False, "data_acceptance_status": "within_documented_scope_only",
                 "independent_review": {"status": "passed", "report": "review.json"}, "limitations": []}
        result = review.inspect_entry(entry, document, {"metadata": {"status": "passed"}}, "review.json")
        self.assertEqual(result["status"], "failed")
        self.assertTrue(result["requires_source_review"])
        for row in [{"requires_source_review": True}, {"status": "unverified"}, {"linkage_status": "unresolved_id"}]:
            self.assertTrue(review.review_signals({}, [row])[0])

    def test_geojson_property_and_metadata_review_flags_are_checked(self):
        self.assertTrue(review.review_signals({"source_review_flags": ["source_version_unknown"]}, [])[0])
        feature = {"type": "Feature", "properties": {"requires_source_review": True}, "geometry": None}
        self.assertEqual(review.rows_of({"type": "FeatureCollection", "features": [feature]}), [feature])
        self.assertEqual(review.review_signals({}, [feature])[1], 1)

    def test_null_source_time_does_not_fall_back_to_another_time_field(self):
        self.assertIsNone(review.source_time({"source_updated_at": None, "source_data_time": "2026-10-24"}))
        self.assertEqual(review.source_time({"source_data_time": "2026-10-24"}), "2026-10-24")
        self.assertEqual(review.period_of({"requested_date": "2026-10-23", "version_start": "2026-10-23", "version_end": None}),
                         {"requested_date": "2026-10-23", "version_start": "2026-10-23", "version_end": None})

    def test_boolean_row_count_cannot_match_numeric_count(self):
        self.assertFalse(review.strict_equal(True, 1))
        self.assertFalse(review.strict_equal({"record_count": "1"}, {"record_count": 1}))

    def test_travel_catalog_counts_models_and_checks_pair_rows_as_separate_grain(self):
        document = {"dataset_id": "travel", "metadata": {}, "records": [{"travel_times": [{"run_time": None}, {"run_time": 20}]}]}
        entry = {"path": "travel.json", "dataset_id": "travel", "record_count": 1, "kind": "normalized_records", "grain": None,
                 "source_updated_at": None, "period": None, "scope_status": None, "units": None,
                 "requires_source_review": False, "data_acceptance_status": "within_documented_scope_only",
                 "independent_review": {"status": "passed", "report": "review.json"}, "limitations": []}
        report = {"records": [{"output_file": "travel.json", "status": "passed", "source_model_count": 1, "source_station_time_row_count": 2}]}
        result = review.inspect_entry(entry, document, report, "review.json")
        self.assertEqual(result["status"], "pass")
        self.assertEqual(result["nested_travel_time_row_count"], 2)
        report["records"][0]["source_station_time_row_count"] = 1
        self.assertEqual(review.inspect_entry(entry, document, report, "review.json")["status"], "failed")


if __name__ == "__main__":
    unittest.main()
