"""Independent great-circle diagnostics remain geometry-free in CI."""
import unittest

from scripts.review_taipei_id_evidence import separation_metres


class TaipeiIdReviewTests(unittest.TestCase):
    def test_identical_points_are_zero_and_distance_is_symmetric(self):
        a, b = [121.5, 25.0], [121.501, 25.001]
        self.assertEqual(separation_metres(a, a), 0)
        self.assertAlmostEqual(separation_metres(a, b), separation_metres(b, a), places=8)
        self.assertGreater(separation_metres(a, b), 100)

    def test_equatorial_degree_matches_declared_spherical_radius(self):
        self.assertAlmostEqual(separation_metres([0, 0], [1, 0]), 111195.0802335329, places=6)


if __name__ == "__main__":
    unittest.main()
