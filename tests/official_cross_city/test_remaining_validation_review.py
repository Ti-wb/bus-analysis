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
               "special_day_publication_status": "indeterminate_partial_or_not_requested"}
        with self.assertRaisesRegex(ValueError, "treated as known"):
            review.require_unknown_unacquired([row], {"Taipei"})

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
