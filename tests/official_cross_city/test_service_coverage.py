"""Synthetic service pagination/calendar fixtures, no external requests."""
from contextlib import redirect_stdout
import gzip
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts.official_cross_city import service_coverage as service


class ServiceCoverageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name); self.store = service.Store(self.root)

    def page(self, rows, skip=0, top=2, kind='Schedule'):
        req = service.request('Keelung', kind, skip, top)
        payload = json.dumps(rows).encode(); compressed = gzip.compress(payload, mtime=0)
        path = self.store.raw / 'responses' / (req['source_id'] + '.json.gz')
        path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(compressed)
        receipt = {**req, 'status': 'downloaded', 'raw_file': str(path.relative_to(self.root)),
                   'bytes': len(payload), 'stored_bytes': len(compressed), 'downloaded_at': '2026-10-24T10:00:00+08:00'}
        self.store.upsert(receipt)
        return receipt

    def model(self, uid='KEE001'):
        return {'RouteUID': uid, 'RouteID': uid[3:], 'SubRouteUID': uid + '01', 'SubRouteID': uid[3:] + '01', 'Direction': 0,
                'Timetables': [{'TripID': '0001', 'ServiceDay': {'Sunday': 0, 'Monday': 1},
                                'SpecialDays': [{'Dates': ['2026-10-24'], 'ServiceStatus': 2}],
                                'StopTimes': [{'StopID': '0002', 'ArrivalTime': '24:10'}]}],
                'Frequencys': [{'StartTime': '08:00', 'EndTime': '10:00', 'MinHeadwayMins': 0, 'MaxHeadwayMins': None,
                               'ServiceDay': {'Sunday': 1, 'Monday': 0}}]}

    def test_exact_page_requires_tail_before_claiming_complete(self):
        self.page([self.model(), self.model('KEE002')])
        coverage = self.store.rebuild()[0]
        self.assertEqual(coverage['scope_status'], 'partial_contiguous_pages')
        self.assertEqual(coverage['next_pages_or_source_gaps'], [{'skip': 2, 'status': 'not_requested'}])
        self.page([], skip=2)
        coverage = self.store.rebuild()[0]
        self.assertEqual(coverage['scope_status'], 'complete_published_endpoint_by_exhausted_page')
        self.assertEqual(coverage['record_count'], 2)
        self.assertEqual(coverage['page_count'], 2)

    def test_calendar_special_days_ids_nulls_and_clock_are_preserved(self):
        self.page([self.model()])
        coverage = self.store.rebuild()[0]
        with gzip.open(self.root / coverage['output_file'], 'rt') as f:
            record = json.load(f)['records'][0]
        self.assertEqual(record['route_id'], '001')
        self.assertEqual(record['timetables'][0]['trip_id'], '0001')
        self.assertEqual(record['timetables'][0]['service_day'], {'sunday': 0, 'monday': 1})
        self.assertEqual(record['timetables'][0]['special_days'][0]['service_status'], 2)
        self.assertEqual(record['timetables'][0]['stop_times'][0]['arrival_time'], '24:10')
        self.assertIsNone(record['frequencys'][0]['max_headway_mins'])
        self.assertEqual(coverage['service_day_object_count'], 2)
        self.assertEqual(coverage['special_day_object_count'], 1)

    def test_empty_endpoint_is_explicit_not_no_operating_service_claim(self):
        self.page([])
        coverage = self.store.rebuild()[0]
        self.assertTrue(coverage['empty_endpoint_response'])
        self.assertEqual(coverage['record_count'], 0)

    def test_missing_schedule_details_are_retained_as_unknown(self):
        source = {'RouteUID': 'KEE001', 'SubRouteUID': 'KEE00101', 'Timetables': None}
        self.page([source])
        coverage = self.store.rebuild()[0]
        with gzip.open(self.root / coverage['output_file'], 'rt') as f:
            result = json.load(f)
        self.assertIsNone(result['records'][0]['timetables'])
        self.assertEqual(result['metadata']['models_without_timetables_or_frequencies'], 1)

    def test_cached_page_needs_matching_stored_and_http_byte_counts(self):
        receipt = self.page([self.model()])
        with self.assertRaises(ValueError): self.store.rows({**receipt, 'bytes': receipt['bytes'] + 1})
        with self.assertRaises(ValueError): self.store.rows({**receipt, 'stored_bytes': receipt['stored_bytes'] + 1})

    def test_plan_no_network_or_writes_and_intercity_path(self):
        with patch.object(service, 'urlopen', side_effect=AssertionError('no network')), redirect_stdout(io.StringIO()) as output:
            service.main(['--root', str(self.root)])
        plan = json.loads(output.getvalue())
        self.assertEqual(len(plan['records']), 4)
        self.assertFalse((self.root / 'data').exists())
        req = service.request('InterCity', 'StopOfRoute', 500, 500)
        self.assertTrue(req['path'].endswith('/InterCity'))
        self.assertIn('%24skip=500', req['download_url'])

    def test_changed_page_size_rebuild_uses_selected_contiguous_partition(self):
        self.page([self.model(), self.model('KEE002')], top=2)
        self.page([], skip=2, top=2)
        self.page([self.model()], top=1)
        self.store.manifest['metadata']['active_page_sizes'] = {'Keelung_Schedule': 1}
        self.store.save()
        coverage = self.store.rebuild()
        self.assertEqual(len(coverage), 1)
        self.assertEqual(coverage[0]['record_count'], 1)
        self.assertEqual(coverage[0]['scope_status'], 'partial_contiguous_pages')

    def test_same_route_subroute_direction_different_operators_remain_models(self):
        one = {**self.model(), 'OperatorID': '01'}
        two = {**self.model(), 'OperatorID': '02'}
        self.page([one, two], top=3)
        coverage = self.store.rebuild()[0]
        with gzip.open(self.root / coverage['output_file'], 'rt') as handle:
            result = json.load(handle)
        self.assertEqual([r['operator_id'] for r in result['records']], ['01', '02'])
        self.assertEqual(result['metadata']['record_count'], 2)
        self.assertIn('operator', result['metadata']['grain'])


if __name__ == '__main__': unittest.main()
