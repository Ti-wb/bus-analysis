"""Protect the evidence and unknown boundaries of the consolidated review."""
import unittest

from scripts import review_remaining_validation as review


class RemainingValidationReviewTests(unittest.TestCase):
    def test_same_count_new_generation_does_not_reuse_old_review(self):
        doc = {"metadata": {"record_count": 1, "generated_at": "new"}, "records": [{}]}
        entry = {"record_count": 1, "status": "passed"}
        with self.assertRaisesRegex(ValueError, "generation"):
            review.require_review(doc, entry, "example.json", generated_at="old")

    def test_unacquired_schedule_cannot_become_known_missing(self):
        row = {"source_scope": "Taipei", "schedule_publication_status": "not_observed_in_complete_published_endpoint",
               "service_day_publication_status": "indeterminate_partial_or_not_requested",
               "special_day_publication_status": "indeterminate_partial_or_not_requested", "operator_id": "001",
               "schedule_evidence_ids": [], "service_day_object_count": 0, "special_day_object_count": 0}
        with self.assertRaisesRegex(ValueError, "conflicts"):
            review.require_schedule_support([row], {"Taipei": "not_acquired"})

    def test_partial_published_rows_remain_evidence_but_absence_stays_unknown(self):
        row = {"source_scope": "NewTaipei", "operator_id": "001", "schedule_evidence_ids": ["source_1"],
               "service_day_object_count": 1, "special_day_object_count": 0,
               "schedule_publication_status": "published", "service_day_publication_status": "published",
               "special_day_publication_status": "indeterminate_partial_or_not_requested"}
        review.require_schedule_support([row], {"NewTaipei": "partial_published_endpoint"})
        row["special_day_publication_status"] = "not_observed_in_complete_published_endpoint"
        with self.assertRaisesRegex(ValueError, "conflicts"):
            review.require_schedule_support([row], {"NewTaipei": "partial_published_endpoint"})

    def test_complete_endpoint_still_needs_operator_identity_for_known_absence(self):
        row = {"source_scope": "Taipei", "operator_id": None, "schedule_evidence_ids": [],
               "service_day_object_count": 0, "special_day_object_count": 0,
               "schedule_publication_status": "indeterminate_partial_or_not_requested",
               "service_day_publication_status": "indeterminate_partial_or_not_requested",
               "special_day_publication_status": "indeterminate_partial_or_not_requested"}
        review.require_schedule_support([row], {"Taipei": "complete_published_endpoint"})
        row["schedule_publication_status"] = "not_observed_in_complete_published_endpoint"
        with self.assertRaisesRegex(ValueError, "conflicts"):
            review.require_schedule_support([row], {"Taipei": "complete_published_endpoint"})

    def test_schedule_without_operator_is_a_candidate_not_a_five_key_match(self):
        row = {"source_scope": "Taipei", "operator_id": "100", "schedule_evidence_ids": [],
               "unassigned_operator_schedule_evidence_ids": ["three_key_source"],
               "service_day_object_count": 0, "special_day_object_count": 0,
               "schedule_publication_status": "indeterminate_missing_schedule_operator",
               "service_day_publication_status": "indeterminate_missing_schedule_operator",
               "special_day_publication_status": "indeterminate_missing_schedule_operator"}
        review.require_schedule_support([row], {"Taipei": "complete_published_endpoint"})
        row["schedule_publication_status"] = "not_observed_in_complete_published_endpoint"
        with self.assertRaisesRegex(ValueError, "conflicts"):
            review.require_schedule_support([row], {"Taipei": "complete_published_endpoint"})

    def test_partial_edge_clarification_does_not_resolve_other_crossings(self):
        rows = [
            {"status": "requires_source_review_unverified_lane_crossing", "review_location_id": "lane", "osm_edge_id": "1:0", "legal_transfer_verified": False},
            {"status": "requires_source_review_unverified_lane_crossing", "review_location_id": "lane", "osm_edge_id": "1:1", "legal_transfer_verified": False},
            {"status": "full_edge_intersections_outside_used_partial_geometry", "review_location_id": "partial", "osm_edge_id": "2:0", "legal_transfer_verified": False},
        ]
        result = review.further_summary(rows)
        self.assertEqual(result["unresolved_crossing_edge_count"], 2)
        self.assertEqual(result["unresolved_crossing_location_ids"], ["lane"])
        self.assertEqual(result["legal_transfers_verified"], 0)


if __name__ == "__main__":
    unittest.main()
