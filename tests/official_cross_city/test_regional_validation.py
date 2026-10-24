"""Synthetic evidence contracts, separate from independent source inspection."""
import io
from unittest.mock import patch

from test_collectors import FixtureCase
from scripts.official_cross_city import regional_coverage as regional, population_boundary as boundary


class RegionalCoverageTests(FixtureCase):
    def body(self):
        return {'Count': 1, 'StartDate': '2026-10-20', 'EndDate': '2026-10-23', 'VersionID': 1,
                'Items': [{'RouteUID': 'KEE1', 'RouteID': 1, 'City': 'Keelung', 'RouteName': {'Zh_tw': '測試'}}]}

    def test_route_master_requires_response_count(self):
        body = self.body()
        body['Count'] = 2
        with self.assertRaises(ValueError):
            regional.parse(body, self.source, 'Keelung', '2026-10-23')

    def test_duplicate_or_missing_route_uid_is_rejected(self):
        body = self.body()
        body['Items'] *= 2
        body['Count'] = 2
        with self.assertRaises(ValueError):
            regional.parse(body, self.source, 'Keelung', '2026-10-23')

    def test_city_mismatch_rejected(self):
        with self.assertRaises(ValueError):
            regional.parse(self.body(), self.source, 'NewTaipei', '2026-10-23')

    def test_typed_ids_period_and_scope_preserved(self):
        output = regional.parse(self.body(), self.source, 'Keelung', '2026-10-23')
        self.assertEqual(output['records'][0]['route_id'], '1')
        self.assertEqual(output['metadata']['version_start'], '2026-10-20')
        self.assertEqual(output['metadata']['source_count'], 1)

    def test_plan_does_not_authenticate(self):
        with patch.object(regional, 'token', side_effect=AssertionError('no API')), patch('sys.stdout', new=io.StringIO()):
            regional.main(['--date', '2026-10-23', '--root', str(self.root)])
        self.assertFalse((self.root / 'raw_data').exists())

    def test_intercity_has_its_own_verified_endpoint(self):
        self.assertIn('/Route/Date/2026-10-23/InterCity?', regional.request_url('InterCity', '2026-10-23'))
        with self.assertRaises(ValueError):
            regional.request_url('Unverified', '2026-10-23')


class PopulationBoundaryTests(FixtureCase):
    def pop(self):
        return {'records': [{'source_area_code': '10017010001', 'county_code': '10017', 'county_name': '基隆市',
                             'area_name': '基隆市中正區', 'village_name': '德義里', 'period': '2026-09',
                             'population': 645, 'source_row_number': 1}]}

    def boundaries(self):
        return [{'village_code': '10017010001', 'county_code': '10017', 'county_name': '基隆市',
                 'district_name': '中正區', 'village_name': '德義里', 'source_note': None}]

    def test_code_and_names_match_with_distinct_vintages(self):
        records, extras = boundary.match(self.pop(), self.boundaries(), '2026-08-17')
        self.assertEqual(records[0]['status'], 'matched_code_and_names')
        self.assertFalse(records[0]['same_month_geometry_verified'])
        self.assertEqual(extras, [])

    def test_name_variant_is_not_silently_changed(self):
        rows = self.boundaries()
        rows[0]['village_name'] = '[造字]里'
        records, _ = boundary.match(self.pop(), rows, '2026-08-17')
        self.assertEqual(records[0]['status'], 'matched_code_name_variant_requires_review')
        self.assertEqual(records[0]['boundary_village_name'], '[造字]里')

    def test_missing_boundaries_remain_null(self):
        records, _ = boundary.match(self.pop(), [], '2026-08-17')
        self.assertIsNone(records[0]['boundary_village_code'])

    def test_boundary_only_areas_are_accounted_for(self):
        rows = self.boundaries()
        rows.append({**rows[0], 'village_code': '10017010S01', 'village_name': None, 'source_note': '未編定村里'})
        _, extras = boundary.match(self.pop(), rows, '2026-08-17')
        self.assertEqual(extras[0]['source_note'], '未編定村里')

    def test_duplicate_codes_and_hierarchy_conflicts_rejected(self):
        rows = self.boundaries()
        with self.assertRaises(ValueError):
            boundary.match(self.pop(), rows * 2, '2026-08-17')
        rows[0]['district_name'] = '錯誤區'
        with self.assertRaises(ValueError):
            boundary.match(self.pop(), rows, '2026-08-17')


if __name__ == '__main__':
    import unittest
    unittest.main()
