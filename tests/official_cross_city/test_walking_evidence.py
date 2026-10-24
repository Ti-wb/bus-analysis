"""Safety regressions for pedestrian evidence; GIS cases require project dependencies."""
import unittest

try:
    from scripts.official_cross_city import walking_evidence as walking
    from shapely.geometry import LineString, Point, Polygon
except ModuleNotFoundError as exc:
    if exc.name not in {"pyproj", "shapely"}:
        raise
    walking = None


def node(nid, lon, lat, **tags):
    return {"type": "node", "id": nid, "lon": lon, "lat": lat, "tags": tags}


def way(wid, nodes, **tags):
    return {"type": "way", "id": wid, "nodes": nodes, "tags": tags}


@unittest.skipIf(walking is None, "GIS dependencies are installed by uv sync; stdlib CI omits GIS cases")
class WalkingEvidenceTests(unittest.TestCase):
    def test_unmarked_or_conflicting_crossing_nodes_do_not_authorize_crossing(self):
        for tags in [{"highway": "crossing"}, {"highway": "crossing", "crossing": "unmarked"},
                     {"highway": "crossing", "crossing": "traffic_signals", "crossing:markings": "no"}]:
            with self.subTest(tags=tags):
                self.assertFalse(walking.marked_crossing(tags))
        self.assertTrue(walking.marked_crossing({"highway": "crossing", "crossing:markings": "zebra"}))

    def test_restrictions_do_not_become_public_walking_edges(self):
        for tags in [{"foot": "no"}, {"access": "private"}, {"foot:conditional": "yes @ (08:00-18:00)"},
                     {"opening_hours": "Mo-Fr 08:00-18:00"}, {"oneway:foot": "reversible"}]:
            with self.subTest(tags=tags):
                self.assertIsNotNone(walking.permission({"highway": "footway", **tags}))
        self.assertIsNone(walking.permission({"highway": "footway", "access": "no", "foot": "yes"}))

    def test_car_oneway_is_ignored_but_foot_oneway_is_enforced(self):
        ns = [node(1, 121.5, 25), node(2, 121.5001, 25)]
        _, _, edges, _, _, _ = walking.graph({"elements": ns + [way(10, [1, 2], highway="footway", oneway="yes")]})
        self.assertTrue(edges[0]["reverse_allowed"])
        _, _, edges, _, _, _ = walking.graph({"elements": ns + [way(10, [1, 2], highway="footway", **{"oneway:foot": "-1"})]})
        self.assertFalse(edges[0]["forward_allowed"])
        self.assertTrue(edges[0]["reverse_allowed"])

    def test_nearby_or_crossing_geometry_does_not_connect_different_node_ids(self):
        ns = [node(1, 121.5, 25), node(2, 121.5001, 25), node(3, 121.5001, 25), node(4, 121.5002, 25)]
        _, points, edges, _, _, _ = walking.graph({"elements": ns + [way(10, [1, 2], highway="footway"), way(20, [3, 4], highway="footway")]})
        c = {"edge_id": "20:1", "target": points["4"], "length_m": 0}
        self.assertIsNone(walking.shortest_path("1", points, edges, [c]))

    def test_barrier_without_permission_interrupts_the_path(self):
        ns = [node(1, 121.5, 25), node(2, 121.5001, 25, barrier="gate"), node(3, 121.5002, 25)]
        _, _, edges, _, rejected, _ = walking.graph({"elements": ns + [way(10, [1, 2, 3], highway="footway")]})
        self.assertEqual(edges, [])
        self.assertEqual(rejected["node_permission"], 2)

    def test_unmarked_main_road_intersection_is_rejected(self):
        ns = [node(1, 121.5, 25), node(2, 121.5002, 25), node(3, 121.5001, 24.9999), node(4, 121.5001, 25.0001)]
        src = {"elements": ns + [way(10, [1, 2], highway="footway"), way(20, [3, 4], highway="primary")]}
        self.assertEqual(walking.graph(src)[2], [])
        src["elements"][-2]["tags"]["footway"] = "crossing"
        self.assertEqual(walking.graph(src)[2], [])
        crossing = walking.projected(Polygon([(121.50009,24.99999),(121.50011,24.99999),(121.50011,25.00001),(121.50009,25.00001)]))
        self.assertEqual(len(walking.graph(src, [('wheelroute:13:1', crossing)])[2]), 1)

    def test_minor_driveway_does_not_infer_rule_134_exception(self):
        ns = [node(1, 121.5, 25), node(2, 121.5002, 25), node(3, 121.5001, 24.9999), node(4, 121.5001, 25.0001)]
        src = {"elements": ns + [way(10, [1, 2], highway="footway", footway="sidewalk"), way(20, [3, 4], highway="service")]}
        self.assertEqual(walking.graph(src)[2], [])
        polygon = walking.projected(Polygon([(121.4999,24.99999),(121.5003,24.99999),(121.5003,25.00001),(121.4999,25.00001)]))
        edges = walking.graph(src, [("official-sidewalk", polygon)])[2]
        self.assertEqual(edges, [])

    def test_connector_must_be_wholly_inside_official_facility(self):
        points = {"1": Point(0, 0), "2": Point(10, 0)}
        edge = {"edge_id": "10:1", "from_node_id": "1", "to_node_id": "2", "tags": {"highway": "footway"}}
        p = Point(5, 2)
        good = Polygon([(-1,-1),(11,-1),(11,3),(-1,3)])
        # Both endpoints may be covered by separate facilities while the middle is not.
        ends = [("a", Point(5,0).buffer(.4)), ("b", p.buffer(.4))]
        self.assertIsNone(walking.connector(p, edge, points, ends, []))
        self.assertIsNotNone(walking.connector(p, edge, points, [("all", good)], []))
        self.assertIsNone(walking.connector(p, edge, points, [("all", good)], [({"highway":"primary"}, LineString([(0,1),(10,1)]))]))

    def test_elevated_walkway_is_not_connected_to_a_ground_stop(self):
        points = {"1": Point(0, 0), "2": Point(10, 0)}
        edge = {"edge_id": "10:1", "from_node_id": "1", "to_node_id": "2", "tags": {"highway": "footway", "layer": "1"}}
        polygon = Polygon([(-1,-1),(11,-1),(11,3),(-1,3)])
        self.assertIsNone(walking.connector(Point(5,2), edge, points, [("ground-sidewalk", polygon)], []))


@unittest.skipIf(walking is None, "GIS project dependencies required")
class WalkingBarrierTests(unittest.TestCase):
    def test_connector_rejects_fence_and_unknown_grade(self):
        points = {"1": Point(0, 0), "2": Point(10, 0)}
        edge = {"edge_id": "10:1", "from_node_id": "1", "to_node_id": "2", "tags": {"highway": "footway"}}
        polygon = Polygon([(-1,-1),(11,-1),(11,3),(-1,3)])
        fence = LineString([(0,1),(10,1)])
        self.assertIsNone(walking.connector(Point(5,2), edge, points, [("all",polygon)], [], [fence]))
        for kind in ["bridge", "tunnel", "covered", "indoor"]:
            with self.subTest(kind=kind):
                edge["tags"] = {"highway":"footway", kind:"yes"}
                self.assertIsNone(walking.connector(Point(5,2), edge, points, [("all",polygon)], []))


if __name__ == "__main__":
    unittest.main()
