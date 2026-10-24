"""Route focus must preserve source identity independently of labels and available shapes."""

import unittest

from scripts.build_web_data import stop_route_properties


class StopRouteIdentityTests(unittest.TestCase):
    def test_same_name_routes_at_different_stops_do_not_share_highlights(self):
        names = {"001": "同名路線", "002": "同名路線"}
        first = stop_route_properties(["001"], names)
        second = stop_route_properties(["002"], names)
        shapes = [{"route_id": "001", "name": "同名路線"},
                  {"route_id": "002", "name": "同名路線"}]
        focused = [shape["route_id"] for shape in shapes if shape["route_id"] in first["route_ids"]]
        self.assertEqual(focused, ["001"])
        self.assertEqual(first["routes"], second["routes"])
        self.assertNotEqual(first["route_ids"], second["route_ids"])

    def test_name_lookup_does_not_depend_on_display_sort_order(self):
        stop = stop_route_properties(["002", "001"], {"001": "Z", "002": "A"})
        self.assertEqual(stop["routes"], ["A", "Z"])
        self.assertEqual(stop["route_names_by_id"][stop["route_ids"][0]], "Z")

    def test_service_without_geometry_or_name_is_preserved_and_counted_once(self):
        stop = stop_route_properties(["001", "002", "003", "002"], {"001": "一般線", "002": "預備線"})
        shape_route_ids = {"001"}
        unmapped = [route_id for route_id in stop["route_ids"] if route_id not in shape_route_ids]
        self.assertEqual(unmapped, ["002", "003"])
        self.assertEqual(stop["route_names_by_id"]["002"], "預備線")
        self.assertIsNone(stop["route_names_by_id"]["003"])
        self.assertEqual(stop["route_count"], 3)
        self.assertEqual(stop["route_count"], len(stop["route_ids"]))
        self.assertEqual(set(stop["route_ids"]), set(stop["route_names_by_id"]))


if __name__ == "__main__":
    unittest.main()
