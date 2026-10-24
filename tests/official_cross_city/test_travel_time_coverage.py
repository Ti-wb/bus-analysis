"""Protect explicit identity and unknown/time boundaries in coverage evidence."""
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from scripts.official_cross_city import travel_time_coverage as coverage


class TravelTimeCoverageTests(unittest.TestCase):
    def test_sequence_offset_cannot_change_explicit_stop_pair(self):
        stops = [{"stop_id": "001"}, {"stop_id": "002"}, {"stop_id": "003"}]
        a = {"sequence": 99, "from_stop_id": "001", "to_stop_id": "002"}
        self.assertEqual(coverage.pair_match(a, stops, {"001", "002", "003"}), ("matched_unique_adjacent_pair", [0]))
        a.update(sequence=2, to_stop_id="003")
        self.assertEqual(coverage.pair_match(a, stops, {"001", "002", "003"})[0], "same_model_stop_ids_not_adjacent")

    def test_loop_repeated_pair_is_not_assigned_by_sequence(self):
        stops = [{"stop_id": s} for s in ["a", "b", "a", "b"]]
        status, candidates = coverage.pair_match({"sequence": 2, "from_stop_id": "a", "to_stop_id": "b"}, stops, {"a", "b"})
        self.assertEqual(status, "ambiguous_repeated_adjacent_pair")
        self.assertEqual(candidates, [0, 2])

    def test_missing_origin_and_literal_zero_are_distinct(self):
        stops = [{"stop_id": "a"}, {"stop_id": "b"}]
        self.assertEqual(coverage.pair_match({"from_stop_id": None, "to_stop_id": "a"}, stops, {"a", "b"})[0], "null_origin_at_current_first_stop")
        self.assertEqual(coverage.pair_match({"from_stop_id": "0", "to_stop_id": "a"}, stops, {"a", "b"})[0], "unsupported_literal_zero_origin")
        self.assertEqual(coverage.runtime_status(None), "missing")
        self.assertEqual(coverage.runtime_status(0), "source_zero")

    def test_station_id_cannot_replace_stop_id(self):
        stops = [{"stop_id": "010", "station_id": "a"}, {"stop_id": "020", "station_id": "b"}]
        self.assertEqual(coverage.pair_match({"from_stop_id": "a", "to_stop_id": "b"}, stops, {"010", "020"})[0], "stop_id_absent_from_current_snapshot")

    def test_civil_clock_keeps_24_hour_endpoint_and_rejects_reversal(self):
        self.assertEqual(coverage.window_status("23:00", "24:00"), "valid_source_clock_window")
        self.assertEqual(coverage.window_status("24:00", "1:00"), "unsupported_source_clock_window")
        self.assertIsNone(coverage.clock_minute("24:10"))

    def test_source_no_data_is_not_unrequested_or_technical_failure(self):
        ref = {"download_url": "official-request"}
        self.assertEqual(coverage.acquisition_status(ref, {}), "unrequested")
        self.assertEqual(coverage.acquisition_status(ref, {"official-request": {"status": "failed", "availability_status": "published_no_data_message"}}), "source_published_no_data")
        self.assertEqual(coverage.acquisition_status(ref, {"official-request": {"status": "failed"}}), "request_technical_failure")

    def test_weekend_is_not_an_invented_public_holiday_calendar(self):
        self.assertEqual(coverage.TAGS, {"0": "weekend", "1": "weekdays"})

    def test_non_json_numeric_source_cannot_overwrite_existing_output(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "old.json").write_text('{"retained":true}\n')
            with self.assertRaises(ValueError):
                coverage.write(root, "old.json", {"bad": float("nan")})
            self.assertEqual((root / "old.json").read_text(), '{"retained":true}\n')
            self.assertFalse((root / "old.json.part").exists())

    def test_compact_pair_evidence_preserves_instance_and_raw_row_order(self):
        rows = [
            {"premium_instance_id": "a", "source_row_number": 12, "runtime_status": "source_positive", "clock_window_status": "valid_source_clock_window"},
            {"premium_instance_id": "a", "source_row_number": 3, "runtime_status": "missing", "clock_window_status": "valid_source_clock_window"},
            {"premium_instance_id": "b", "source_row_number": 12, "runtime_status": "source_zero", "clock_window_status": "valid_source_clock_window"},
            {"premium_instance_id": "b", "source_row_number": 4, "runtime_status": "source_positive", "clock_window_status": "unsupported_source_clock_window"},
        ]
        self.assertEqual(coverage.pack_pair_evidence(rows), [
            {"premium_instance_id": "a", "source_row_numbers": [12, 3], "usable_numeric_window_row_numbers": [12]},
            {"premium_instance_id": "b", "source_row_numbers": [12, 4], "usable_numeric_window_row_numbers": [12]},
        ])


if __name__ == "__main__":
    unittest.main()
