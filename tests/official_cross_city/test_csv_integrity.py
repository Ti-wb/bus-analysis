"""Malformed quotes must never publish an incomplete official export."""
import io
import json
from unittest.mock import patch

from test_collectors import FixtureCase
from scripts.official_cross_city import common, ntpc


class CsvIntegrityTests(FixtureCase):
    def test_unterminated_final_field_with_valid_width_is_rejected(self):
        path = self.root / 'bad.csv'
        path.write_text('id,latitude\n1,"25.0', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'Malformed CSV'):
            list(common.csv_rows(path, {'id', 'latitude'}))

    def test_malformed_quoted_header_is_rejected(self):
        path = self.root / 'bad.csv'
        path.write_text('id,"latitude', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'Malformed CSV'):
            list(common.csv_rows(path, {'id'}))

    def test_valid_multiline_and_escaped_quotes_are_preserved(self):
        path = self.root / 'good.csv'
        path.write_text('id,name\n1,"first\nsecond ""quoted"""\n', encoding='utf-8')
        rows = list(common.csv_rows(path, {'id', 'name'}))
        self.assertEqual(rows[0][1]['name'], 'first\nsecond "quoted"')

    def test_failed_live_export_preserves_previous_normalized_snapshot(self):
        path = self.root / 'stops.csv'
        path.write_text('id,routeid,namezh,seqno,pgp,goback,longitude,latitude,stoplocationid\n'
                        '1,2,測試站,1,0,0,121.5,25.0,"3', encoding='utf-8')
        output = self.root / 'data/official_cross_city/ntpc_stops.json'
        common.write_json(output, {'previous': 'verified'})
        with patch.object(ntpc, 'fetch', return_value=(path, self.source)):
            with self.assertRaisesRegex(ValueError, 'Malformed CSV'):
                ntpc.main(['--root', str(self.root), '--kind', 'stops', '--fetch'])
        self.assertEqual(json.loads(output.read_text(encoding='utf-8')), {'previous': 'verified'})


if __name__ == '__main__':
    import unittest
    unittest.main()
