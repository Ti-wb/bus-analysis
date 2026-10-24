"""Independent negative guards for regional source evidence review."""
import unittest

from scripts import review_regional_service_evidence as review

try:
    from shapely.geometry import box
except ModuleNotFoundError:
    box = None


class RegionalServiceReviewTests(unittest.TestCase):
    def test_schedule_limitations_follow_actual_acquisition_support(self):
        raw = {(scope, "Schedule"): [] for scope in review.SCOPES}
        text = review.schedule_support_limitation(raw)
        self.assertIn("All four configured Schedule endpoints", text)
        self.assertNotIn("was not acquired", text)
        raw["NewTaipei", "Schedule"] = None
        text = review.schedule_support_limitation(raw)
        self.assertIn("NewTaipei", text)
        self.assertNotIn("Taipei,", text)
        self.assertIn("remain indeterminate", text)

    def test_five_dimensions_keep_authority_operator_and_direction_separate(self):
        row = {"RouteUID": "THB001", "SubRouteUID": "THB0010", "Direction": 0, "OperatorID": "007"}
        wanted = review.five_key("InterCity", row)
        for scope, changed in [("Keelung", row), ("InterCity", {**row, "OperatorID": "008"}),
                               ("InterCity", {**row, "Direction": 1}), ("InterCity", {**row, "SubRouteUID": "THB0011"})]:
            with self.subTest(scope=scope, row=changed):
                self.assertNotEqual(wanted, review.five_key(scope, changed))
        self.assertEqual(review.five_key("InterCity", row, "007"), wanted)

    def test_unacquired_or_unknown_operator_never_becomes_no_published_service(self):
        self.assertEqual(review.publication_status(0, False), "indeterminate_partial_or_not_requested")
        self.assertEqual(review.publication_status(0, True, False), "indeterminate_partial_or_not_requested")
        self.assertEqual(review.publication_status(0, True), "not_observed_in_complete_published_endpoint")
        self.assertEqual(review.publication_status(1, False), "published")

    def test_missing_operator_candidates_never_become_assigned_calendar_support(self):
        model = {"RouteUID": "TPE001", "SubRouteUID": "TPE0010", "Direction": 0}
        candidate = {"schedule_evidence_id": "raw_schedule_1", "service_day_object_count": 4}
        exact = {"schedule_evidence_id": "raw_schedule_2", "service_day_object_count": 2}
        prefix = ("Taipei", "TPE001", "TPE0010", 0)
        index = {(*prefix, None): [candidate]}
        assigned, unassigned, uncertain = review.resolve_schedule_relations("Taipei", model, "007", index)
        self.assertEqual(assigned, [])
        self.assertEqual(unassigned, [candidate])
        self.assertTrue(uncertain)
        self.assertEqual(review.publication_status(0, True, True, uncertain), "indeterminate_missing_schedule_operator")
        index[(*prefix, "007")] = [exact]
        assigned, unassigned, uncertain = review.resolve_schedule_relations("Taipei", model, "007", index)
        self.assertEqual(sum(row["service_day_object_count"] for row in assigned), 2)
        self.assertEqual(unassigned, [candidate])
        self.assertFalse(uncertain)
        self.assertEqual(review.publication_status(0, True, True, uncertain), "not_observed_in_complete_published_endpoint")
        self.assertEqual(review.resolve_schedule_relations("Taipei", model, None, index)[0], [])

    def test_unassigned_calendar_candidates_remain_within_scope_and_direction(self):
        model = {"RouteUID": "TPE001", "SubRouteUID": "TPE0010", "Direction": 0}
        index = {("NewTaipei", "TPE001", "TPE0010", 0, None): [{}], ("Taipei", "TPE001", "TPE0010", 1, None): [{}],
                 ("Taipei", "TPE001", "TPE0010", 0, "008"): [{}]}
        self.assertEqual(review.resolve_schedule_relations("Taipei", model, "007", index), ([], [], False))

    def test_stop_uid_variants_are_not_collapsed_by_uid_or_station_name(self):
        stop = {"StopUID": "THB0001", "StopID": "0001", "StationID": "0002", "StopPosition": {"PositionLon": 121.5, "PositionLat": 25}}
        keys = {review.variant_key(stop), review.variant_key({**stop, "StationID": "0003"}),
                review.variant_key({**stop, "StopPosition": {"PositionLon": 121.50001, "PositionLat": 25}})}
        self.assertEqual(len(keys), 3)

    def test_missing_zero_and_nonfinite_coordinates_are_not_classified_as_outside(self):
        for pos, expected in [(None, "missing_coordinates"), ({"PositionLon": None, "PositionLat": 25}, "missing_coordinates"),
                              ({"PositionLon": 0, "PositionLat": 25}, "zero_coordinate_requires_source_review"),
                              ({"PositionLon": True, "PositionLat": 25}, "invalid_coordinates"),
                              ({"PositionLon": 121, "PositionLat": float("nan")}, "invalid_coordinates")]:
            with self.subTest(pos=pos):
                status, coordinates = review.point_state(pos)
                self.assertEqual(status, expected)
                self.assertIsNone(coordinates)

    def test_calendar_zero_flags_dates_and_special_status_remain_literal(self):
        day = dict.fromkeys(review.WEEK, 0)
        special = [{"Dates": ["2026-10-24"], "DatePeriod": {"StartDate": "2026-10-24", "EndDate": "2026-10-26"}, "ServiceStatus": 0}]
        result = review.calendars({"Timetables": [{"TripID": "0001", "ServiceDay": day, "SpecialDays": special}]})[0]
        self.assertEqual(result["weekly_calendar_content_status"], "valid_published_flags")
        self.assertEqual(result["trip_id"], "0001")
        self.assertEqual(set(result["service_day"].values()), {0})
        self.assertEqual(result["special_days"][0]["service_status"], 0)
        self.assertEqual(result["special_days"][0]["dates"], ["2026-10-24"])
        self.assertEqual(result["special_days"][0]["date_period"]["end_date"], "2026-10-26")
        invalid = review.calendars({"Frequencys": [{"ServiceDay": {"Monday": True}}]})[0]
        self.assertEqual(invalid["weekly_calendar_content_status"], "requires_source_review")
        self.assertEqual(invalid["service_day"], {"monday": True})
        self.assertIsNone(invalid["special_days"])

    def test_operator_expansion_cannot_replace_original_source_population(self):
        geography = [{"geographic_status": "regional_member", "source_membership_count": 2}]
        metadata = {"source_model_count": 1, "operator_relation_model_count": 2, "stop_geography_variant_count": 1,
                    "source_stop_membership_count": 2, "stop_geography_status_counts": {"regional_member": 1}}
        review.verify_population_totals(metadata, 1, 2, geography, 2)
        with self.assertRaises(ValueError):
            review.verify_population_totals({**metadata, "source_model_count": 2}, 1, 2, geography, 2)
        with self.assertRaises(ValueError):
            review.verify_population_totals({**metadata, "source_stop_membership_count": 4}, 1, 2, geography, 2)

    def test_original_ids_preserve_leading_zeroes_and_ordinals_remain_numbers(self):
        value = review.source_projection({"StopID": "0001", "StopSequence": 2, "VersionID": 3})
        self.assertEqual(value, {"stop_id": "0001", "stop_sequence": 2, "version_id": "3"})
        with self.assertRaises(ValueError):
            review.source_projection({"UnexpectedID": "new"})
        self.assertIsNone(review.source_projection({"OperatorCode": ""})["operator_code"])

    def test_boolean_or_string_ordinals_cannot_pass_numeric_source_comparison(self):
        for actual in [{"source_ordinal": True}, {"source_ordinal": "1"}, {"source_ordinal": 1.0}]:
            with self.subTest(actual=actual), self.assertRaises(ValueError):
                review.compare_records([actual], [{"source_ordinal": 1}], "ordinal")

    @unittest.skipIf(box is None, "Optional GIS boundary predicate requires Shapely")
    def test_boundary_covers_retains_two_city_memberships(self):
        class Identity:
            def transform(self, x, y):
                return x, y
        masks = {"63000": box(121, 25, 122, 26), "65000": box(122, 25, 123, 26)}
        self.assertEqual(review.classify((122, 25.5), masks, Identity()), ["63000", "65000"])
        self.assertEqual(review.classify((120, 25.5), masks, Identity()), [])


if __name__ == "__main__":
    unittest.main()
