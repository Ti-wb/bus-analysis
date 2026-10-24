"""Negative guards for context-only walking source review, without live sources."""
import unittest

from scripts import review_walking_additional_evidence as review


class AdditionalWalkingReviewTests(unittest.TestCase):
    def context(self):
        return {"legal_transfer_verified": False, "usable_transfer_cost_minutes": None, "requires_source_review": True,
                "evidence_status": "official_context_only_requires_independent_review",
                "case_links": [{"verified_stop_id_identity": False, "verified_exit_to_stop_connector": False}]}

    def test_approximate_destination_minutes_cannot_become_transfer_cost(self):
        row = {**self.context(), "source_stated_walk_minutes": 2, "source_stated_walk_is_approximate": True}
        review.context_only(row)
        for minutes in [0, 2, 13.7, 200]:
            with self.assertRaises(ValueError):
                review.context_only({**row, "usable_transfer_cost_minutes": minutes})

    def test_parking_or_award_context_cannot_certify_identity_or_connector(self):
        for field in ["verified_stop_id_identity", "verified_exit_to_stop_connector"]:
            row = self.context()
            row["case_links"][0][field] = True
            with self.assertRaises(ValueError):
                review.context_only(row)
        with self.assertRaises(ValueError):
            review.context_only({**self.context(), "legal_transfer_verified": True})
        with self.assertRaises(ValueError):
            review.context_only({**self.context(), "requires_source_review": False})

    def test_pdf_cells_require_same_visual_baseline_and_unique_code(self):
        cells = [{"text": text, "x": index * 100, "y": 20} for index, text in enumerate(["莊-3-11", "社區", "中正路265號(新莊郵局)", "13.7", "111.05.04"])]
        self.assertEqual(review.restriction_cells(cells, "莊-3-11")[-2:], ["13.7", "111.05.04"])
        with self.assertRaises(ValueError):
            review.restriction_cells(cells + [cells[0]], "莊-3-11")
        with self.assertRaises(ValueError):
            review.restriction_cells(cells[:-1] + [{**cells[-1], "y": 30}], "莊-3-11")

    def test_source_dates_and_numeric_types_remain_distinct(self):
        self.assertEqual(review.roc_date("111", "05", "04"), "2022-05-04")
        self.assertEqual(review.roc_date("102", "09", "01"), "2013-09-01")
        self.assertNotEqual(review.roc_date("115", "10", "01"), "2026-10-24")
        self.assertFalse(review.strict_equal(16, "16"))
        self.assertFalse(review.strict_equal(False, 0))
        self.assertFalse(review.strict_equal(None, 0))

    def test_html_parser_keeps_columns_and_excludes_script_dates(self):
        source = review.VisibleHTML('<script>更新日期：2099-01-01</script><table><tr><th>長度(m)</th><th>面積(平方公尺)</th></tr><tr><td>395</td><td>2,711</td></tr></table>'.encode())
        self.assertEqual(source.rows, [["長度(m)", "面積(平方公尺)"], ["395", "2,711"]])
        self.assertNotIn("2099", source.text)

    def test_address_normalization_does_not_invent_same_side_or_door_number(self):
        self.assertIn(review.compact("中華路一段３號"), review.compact("目的地： 中華路一段 3 號"))
        self.assertNotIn(review.compact("中華路一段3號"), review.compact("站牌中華路一段8號(向北)"))
        self.assertNotEqual(review.compact("中華路一段1號"), review.compact("中華路1段1號"))


if __name__ == "__main__":
    unittest.main()
