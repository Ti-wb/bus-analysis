"""Reject broad aliasing and distinguish identity from geometry evidence."""
import unittest

try:
    from shapely.geometry import box
except ImportError:
    box = None

from scripts.official_cross_city import boundary_evidence as evidence


class OfficialBoundaryEvidenceTests(unittest.TestCase):
    def test_ris_requires_literal_complete_code_and_parent(self):
        rows = evidence.parse_ris("\ufeff65000120027,65000120,濓新里\n")
        self.assertEqual(rows["65000120027"]["village_name"], "濓新里")
        for text in ("65000120027,65000121,濓新里\n", "6500012002,65000120,濓新里\n",
                     "65000120027,65000120,濓新里\n65000120027,65000120,濓新里\n"):
            with self.subTest(text=text), self.assertRaises(ValueError):
                evidence.parse_ris(text)

    def test_source_row_movement_is_not_an_administrative_change(self):
        a = evidence.parse_ris("65000120027,65000120,濓新里\n65000120028,65000120,濓洞里\n")
        b = evidence.parse_ris("65000120028,65000120,濓洞里\n65000120027,65000120,濓新里\n")
        self.assertEqual(evidence.compare_code_versions(a, b), [])

    def test_stable_counts_do_not_hide_a_deleted_and_added_code(self):
        a = evidence.parse_ris("65000120027,65000120,濓新里\n")
        b = evidence.parse_ris("65000120028,65000120,濓洞里\n")
        changes = evidence.compare_code_versions(a, b)
        self.assertEqual([r["change_kind"] for r in changes], ["deleted", "added"])

    def test_alias_is_bounded_to_exact_code_names_and_district(self):
        result = evidence.verify_alias("65000120027", "濓新里", "濂新里", "瑞芳區")
        self.assertEqual(result["status"], "verified_official_code_scoped_name_alias")
        self.assertFalse(result["same_month_geometry_verified"])
        for args in (("65000120028", "濓新里", "濂新里", "瑞芳區"),
                     ("65000120027", "濂新里", "濂新里", "瑞芳區"),
                     ("65000120027", "濓新里", "濂新里", "中和區")):
            with self.subTest(args=args):
                self.assertIsNone(evidence.verify_alias(*args))

    @unittest.skipIf(box is None, "Optional Shapely geometry comparison; run with project dependencies locally")
    def test_equal_ids_and_counts_cannot_prove_equal_geometry(self):
        props = {"COUNTYCODE": "63000", "VILLNAME": "甲里"}
        before = {"63000010001": (props, box(121, 25, 122, 26))}
        after = {"63000010001": (props, box(121, 25, 122.1, 26))}
        result = evidence.compare_boundary_versions(before, after)[0]
        self.assertEqual(result["geometry_changed_codes"], ["63000010001"])
        self.assertFalse(result["current_distribution_matches_baseline"])

    @unittest.skipIf(box is None, "Optional Shapely geometry comparison; run with project dependencies locally")
    def test_current_snapshot_match_does_not_assert_month_geometry(self):
        props = {"COUNTYCODE": "63000", "VILLNAME": "甲里"}
        features = {"63000010001": (props, box(121, 25, 122, 26))}
        result = evidence.compare_boundary_versions(features, features)[0]
        self.assertTrue(result["current_distribution_matches_baseline"])
        self.assertFalse(result["same_month_geometry_verified"])


if __name__ == "__main__":
    unittest.main()
