"""Small independent safeguards for the raw travel-pair reviewer."""
import importlib.util
from pathlib import Path
import unittest

PATH = Path(__file__).resolve().parents[2] / "scripts/review_travel_time_coverage.py"
SPEC = importlib.util.spec_from_file_location("review_travel_time_coverage", PATH)
REVIEW = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(REVIEW)


def model(stops):
    return {"RouteUID": "TPE0001", "SubRouteUID": "TPE00010", "Direction": 0,
            "Stops": [{"StopID": stop, "StopSequence": 99 - index} for index, stop in enumerate(stops)]}


class RawPairReviewTests(unittest.TestCase):
    def test_complete_http_200_index_error_stays_technical_without_observations(self):
        body = {"message": "Index was out of range. Must be non-negative and less than the size of the collection. (Parameter 'index')"}
        source = {"status": "failed", "availability_status": "technical_failure", "failure_reason": "unusable_response_format_or_contract"}
        receipt = {"status": "failed", "http_status": 200, "availability_status": "technical_failure",
                   "complete_unparsed_response_retained": True, "raw_parse_error": "Expected the documented array of travel-time models"}
        self.assertEqual(REVIEW.audit_failed_response(source, receipt, body), body["message"])
        self.assertNotIn("output_file", source)
        with self.subTest("upstream error is not an official absence message"), self.assertRaises(ValueError):
            REVIEW.audit_failed_response({**source, "availability_status": "published_no_data_message"},
                                         {**receipt, "availability_status": "published_no_data_message"}, body)
        with self.subTest("failed endpoint cannot supply observation output"), self.assertRaises(ValueError):
            REVIEW.audit_failed_response({**source, "output_file": "observations/907.json.gz"}, receipt, body)
        with self.subTest("incomplete raw body cannot certify failure class"), self.assertRaises(ValueError):
            REVIEW.audit_failed_response(source, {**receipt, "complete_unparsed_response_retained": False}, body)

    def test_missing_start_is_never_filled_from_sequence(self):
        row = {"Sequence": 2, "FromStopID": None, "ToStopID": "02", "RunTime": None}
        self.assertEqual(REVIEW.classify_raw_pair(model(["01", "02"]), row), ("missing_from_stop_id", []))

    def test_reverse_and_non_adjacent_pairs_are_not_matches(self):
        reference = model(["01", "02", "03"])
        for start, end in [("01", "03"), ("02", "01")]:
            self.assertEqual(REVIEW.classify_raw_pair(reference, {"FromStopID": start, "ToStopID": end}),
                             ("explicit_stop_ids_not_adjacent", []))

    def test_array_order_and_duplicate_occurrences_are_preserved(self):
        reference = model(["01", "02", "01", "02"])
        self.assertEqual(REVIEW.classify_raw_pair(reference, {"FromStopID": "01", "ToStopID": "02"}),
                         ("adjacent_explicit_stop_ids", [1, 3]))

    def test_absent_id_and_leading_zero_stay_distinct(self):
        self.assertEqual(REVIEW.classify_raw_pair(model(["01", "02"]), {"FromStopID": "1", "ToStopID": "02"}),
                         ("stop_id_absent_from_reference_model", []))

    def test_model_key_includes_route_subroute_and_direction(self):
        reference = model(["01", "02"])
        original = REVIEW.raw_model_key(reference)
        for field, value in [("RouteUID", "TPE9999"), ("SubRouteUID", "TPE99990"), ("Direction", 1)]:
            self.assertNotEqual(original, REVIEW.raw_model_key(dict(reference, **{field: value})))
        with self.assertRaises(ValueError):
            REVIEW.raw_model_key(dict(reference, Direction=True))

    def test_null_runtime_cannot_be_replaced_with_zero(self):
        self.assertFalse(REVIEW.strict_equal({"RunTime": None}, {"RunTime": 0}))
        self.assertFalse(REVIEW.strict_equal({"Sequence": 1}, {"Sequence": "1"}))

    def test_end_of_day_is_preserved_without_next_day_assumption(self):
        self.assertEqual(REVIEW.clock_minutes("24:00"), 1440)
        self.assertEqual(REVIEW.clock_minutes("0:00"), 0)
        for invalid in ["24:01", "25:00", "1:60", "01:00:00", None]:
            with self.assertRaises(ValueError):
                REVIEW.clock_minutes(invalid)

    def test_raw_field_map_keeps_ids_and_missing_values(self):
        result = REVIEW.normalize_raw_model({"RouteID": "001", "RouteUID": "TPE001", "SubRouteID": "0010", "Direction": 0,
                                             "TravelTimes": [{"Sequence": 1, "ToStopID": "020", "ServiceTag": "0"}]})
        self.assertEqual(result["route_id"], "001")
        self.assertEqual(result["direction"], 0)
        self.assertEqual(result["travel_times"][0]["to_stop_id"], "020")
        self.assertIsNone(result["travel_times"][0]["from_stop_id"])
        self.assertIsNone(result["travel_times"][0]["run_time"])
        self.assertEqual(REVIEW.source_id(8264), "8264")

    def test_unresolved_marker_cannot_be_removed_or_downgraded(self):
        expected = [{"status": "same_model_stop_ids_not_adjacent", "requires_source_review": True,
                     "source_review_flags": ["same_model_stop_ids_not_adjacent"]}]
        for actual in [[{"status": "same_model_stop_ids_not_adjacent"}],
                       [{**expected[0], "requires_source_review": False}]]:
            with self.assertRaises(ValueError):
                REVIEW.compare_rows(actual, expected, "unmatched rows")

    def test_fine_classification_preserves_placeholder_and_other_model_boundaries(self):
        reference = model(["01", "02", "03"])
        self.assertEqual(REVIEW.full_pair_status(reference, {"ToStopID": "01"}, {"01", "02", "03", "04"})[0],
                         "null_origin_at_current_first_stop")
        self.assertEqual(REVIEW.full_pair_status(reference, {"ToStopID": "02"}, {"01", "02", "03", "04"})[0],
                         "null_origin_at_current_nonfirst_stop")
        self.assertEqual(REVIEW.full_pair_status(reference, {"FromStopID": "03", "ToStopID": "04"}, {"01", "02", "03", "04"})[0],
                         "stop_ids_known_only_in_other_models")

    def test_unsupported_values_and_windows_never_become_usable(self):
        for runtime in [None, True, "0", -1, float("nan")]:
            observation = {"runtime_status": REVIEW.value_status(runtime), "clock_window_status": "valid_source_clock_window"}
            self.assertFalse(REVIEW.usable(observation))
        self.assertTrue(REVIEW.usable({"runtime_status": REVIEW.value_status(0), "clock_window_status": "valid_source_clock_window"}))
        self.assertFalse(REVIEW.usable({"runtime_status": REVIEW.value_status(12), "clock_window_status": REVIEW.window_status("23:00", "1:00")}))

    def test_canonical_row_references_keep_instance_and_original_ordinals(self):
        observations = [{"premium_instance_id": "source_A:model:1", "source_row_number": 9, "runtime_status": "source_positive", "clock_window_status": "valid_source_clock_window"},
                        {"premium_instance_id": "source_A:model:1", "source_row_number": 12, "runtime_status": "missing", "clock_window_status": "valid_source_clock_window"},
                        {"premium_instance_id": "source_B:model:1", "source_row_number": 4, "runtime_status": "source_zero", "clock_window_status": "valid_source_clock_window"}]
        self.assertEqual(REVIEW.source_row_references(observations), [
            {"premium_instance_id": "source_A:model:1", "source_row_numbers": [9, 12], "usable_numeric_window_row_numbers": [9]},
            {"premium_instance_id": "source_B:model:1", "source_row_numbers": [4], "usable_numeric_window_row_numbers": [4]}])
        with self.assertRaises(ValueError):
            REVIEW.source_row_references(observations + [observations[0]])
        for ordinal in [0, True, "9"]:
            with self.assertRaises(ValueError):
                REVIEW.source_row_references([{**observations[0], "source_row_number": ordinal}])


if __name__ == "__main__":
    unittest.main()
