"""Publication support and missing-position semantics; GIS cases are optional."""
import unittest

from scripts.official_cross_city import regional_service_evidence as evidence

try:
    from shapely.geometry import box
except ImportError:
    box = None


class RegionalServiceEvidenceTests(unittest.TestCase):
    def test_complete_schedule_without_operator_does_not_prove_five_key_absence(self):
        self.assertEqual(evidence.published_status(0, True, missing_schedule_operator=True), "indeterminate_missing_schedule_operator")
        self.assertEqual(evidence.published_status(1, True, missing_schedule_operator=True), "published")
    def test_missing_zero_and_invalid_positions_remain_distinct(self):
        self.assertEqual(evidence.position_status({"position_lon": None, "position_lat": 25})[0], "missing_coordinates")
        self.assertEqual(evidence.position_status({"position_lon": 0, "position_lat": 25})[0], "zero_coordinate_requires_source_review")
        for lon, lat in ((181, 25), (121, float("nan")), ("121.5", 25), (True, 25)):
            with self.subTest(lon=lon, lat=lat):
                self.assertEqual(evidence.position_status({"position_lon": lon, "position_lat": lat})[0], "invalid_coordinates")

    def test_missing_schedule_is_unknown_until_endpoint_is_complete(self):
        self.assertEqual(evidence.published_status(0, False), "indeterminate_partial_or_not_requested")
        self.assertEqual(evidence.published_status(0, True), "not_observed_in_complete_published_endpoint")
        self.assertEqual(evidence.published_status(1, False), "published")

    def test_weekly_flags_and_special_dates_are_preserved_without_operating_inference(self):
        days = dict.fromkeys(evidence.WEEK, 0)
        special = [{"dates": ["2026-10-24"], "service_status": 0}]
        rows = evidence.calendar_nodes({"timetables": [{"trip_id": "0001", "service_day": days, "special_days": special}]})
        self.assertEqual(rows[0]["service_day"], days)
        self.assertEqual(rows[0]["special_days"], special)
        self.assertEqual(rows[0]["weekly_calendar_content_status"], "valid_published_flags")
        self.assertEqual(rows[0]["trip_id"], "0001")

    def test_incomplete_calendar_is_not_repaired_with_zeroes(self):
        row = evidence.calendar_nodes({"frequencys": [{"service_day": {"monday": 1}}]})[0]
        self.assertEqual(row["weekly_calendar_content_status"], "requires_source_review")
        self.assertEqual(row["service_day"], {"monday": 1})
        self.assertIsNone(row["special_days"])

    @unittest.skipIf(box is None, "Optional Shapely covers check; full GIS environment runs this locally")
    def test_boundary_point_is_included_and_multiple_city_memberships_are_preserved(self):
        masks = {"63000": box(121, 25, 122, 26), "65000": box(122, 25, 123, 26)}
        self.assertEqual(evidence.covering_cities((122, 25.5), masks), ["63000", "65000"])
        self.assertEqual(evidence.covering_cities((120, 25.5), masks), [])


if __name__ == "__main__":
    unittest.main()
