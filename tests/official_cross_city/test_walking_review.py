"""Independent review guard regressions; no producer dependency."""
import unittest

try:
    from scripts import review_walking_evidence as review
    from shapely.geometry import Point
except ModuleNotFoundError as exc:
    if exc.name not in {"pyproj", "shapely"}:
        raise
    review = None


@unittest.skipIf(review is None, "GIS dependencies required")
class WalkingReviewTests(unittest.TestCase):
    def test_unmarked_or_conflicting_crossing_is_not_accepted_as_marked(self):
        for tags in [{}, {"crossing": "unmarked"}, {"crossing": "traffic_signals", "crossing:markings": "no"},
                     {"crossing:signals": "unknown", "crossing:markings": "zebra"}]:
            with self.subTest(tags=tags):
                self.assertFalse(review.marked({"highway": "crossing", **tags}))
        self.assertTrue(review.marked({"highway": "crossing", "crossing:markings": "zebra"}))

    def test_partial_edge_distance_respects_foot_direction_and_keeps_connector_length(self):
        edge = {"edge_id": "e", "from_node_id": "a", "to_node_id": "b", "length_m": 10,
                "forward_allowed": True, "reverse_allowed": False}
        context = {"points": {"a": Point(0, 0), "b": Point(10, 0)}}
        links = [{"edge_id": "e", "target": Point(4, 0), "length_m": 2}]
        self.assertEqual(review.best_distance("a", {"e": edge}, context, links), 6)
        self.assertIsNone(review.best_distance("b", {"e": edge}, context, links))

    def test_geometric_overlap_does_not_join_different_node_ids(self):
        edges = {"first": {"from_node_id": "a", "to_node_id": "b", "length_m": 10, "forward_allowed": True, "reverse_allowed": True},
                 "second": {"from_node_id": "c", "to_node_id": "d", "length_m": 10, "forward_allowed": True, "reverse_allowed": True}}
        result = review.distances("a", edges)
        self.assertEqual(set(result), {"a", "b"})


if __name__ == "__main__":
    unittest.main()
