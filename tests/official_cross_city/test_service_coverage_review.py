"""Independent review must catch lost nested data, fabricated zeroes and IDs."""
import unittest

from scripts import review_service_coverage as review


class ServiceCoverageReviewTests(unittest.TestCase):
    def test_distinct_operator_models_are_not_collapsed_by_three_code_prefix(self):
        a = {"RouteUID": "THB0001", "SubRouteUID": "THB000101", "Direction": 0, "OperatorID": "16"}
        b = {**a, "OperatorID": "35"}
        self.assertNotEqual(review.model_identity(a), review.model_identity(b))
        self.assertEqual(review.model_identity(a), review.model_identity({k: v for k, v in reversed(list(a.items()))}))

    def test_nested_calendars_and_special_days_cannot_be_dropped(self):
        source = {"RouteUID": "KEE001", "Timetables": [{"ServiceDay": {"Monday": 1},
                  "SpecialDays": [{"Dates": ["2026-10-24"], "ServiceStatus": 2}]}]}
        result = {"route_uid": "KEE001", "timetables": [{"service_day": {"monday": 1}, "special_days": []}]}
        self.assertTrue(review.compare_values(source, result))

    def test_missing_headway_cannot_become_zero(self):
        self.assertTrue(review.compare_values({"MinHeadwayMins": None}, {"min_headway_mins": 0}))
        self.assertEqual(review.compare_values({"MinHeadwayMins": 0}, {"min_headway_mins": 0}), [])

    def test_leading_zero_ids_and_extended_clock_are_preserved(self):
        source = {"TripID": "0001", "ArrivalTime": "24:10"}
        self.assertTrue(review.compare_values(source, {"trip_id": "1", "arrival_time": "00:10"}))
        self.assertEqual(review.compare_values(source, {"trip_id": "0001", "arrival_time": "24:10"}), [])

    def test_only_non_data_hash_fields_may_be_omitted(self):
        self.assertEqual(review.compare_values({"PositionLon": 121.5, "GeoHash": "unused"}, {"position_lon": 121.5}), [])
        self.assertTrue(review.compare_values({"PositionLon": 121.5, "StopID": "01"}, {"position_lon": 121.5}))


if __name__ == "__main__":
    unittest.main()
