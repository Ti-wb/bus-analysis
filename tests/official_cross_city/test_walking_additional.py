"""Prevent a retained address constant from silently outliving its source."""
import unittest

from scripts.official_cross_city.walking_additional import require_address_in_source


class WalkingAdditionalTests(unittest.TestCase):
    def test_explicit_address_survives_only_width_and_whitespace_normalization(self):
        require_address_in_source("地址：新北市新莊區中華路１段\n１號２樓", "新北市新莊區中華路1段1號2樓")

    def test_changed_building_number_cannot_retain_previous_destination(self):
        with self.assertRaisesRegex(ValueError, "no longer explicit"):
            require_address_in_source("地址：新莊區中華路一段8號", "新莊區中華路一段3號")


if __name__ == "__main__":
    unittest.main()
