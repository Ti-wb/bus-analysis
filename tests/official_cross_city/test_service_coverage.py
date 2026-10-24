"""Synthetic service pagination/calendar fixtures, no external requests."""
from contextlib import redirect_stdout
from datetime import datetime, timezone
import gzip
from http.client import IncompleteRead
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from scripts.official_cross_city import service_coverage as service


class Response:
    status = 200

    def __init__(self, body, headers=None, error=None):
        self.body = body
        self.headers = headers or {'Content-Length': str(len(body)), 'Content-Type': 'application/json'}
        self.error = error

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, limit):
        if self.error:
            raise self.error
        return self.body[:limit]


class Clock:
    def __init__(self):
        self.value = datetime(2026, 10, 24, tzinfo=timezone.utc).timestamp()

    def time(self):
        return self.value

    def sleep(self, duration):
        self.value += duration

    def now(self):
        return datetime.fromtimestamp(self.value, timezone.utc).isoformat(timespec='seconds')


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

    def args(self, **changes):
        values = {'scope': ['Keelung'], 'kind': ['Schedule'], 'stops_page_size': 2, 'schedule_page_size': 2,
                  'max_requests': 4, 'max_response_mb': 1, 'max_total_mb': 4, 'min_request_interval': 15}
        return SimpleNamespace(**(values | changes))

    def specification(self):
        paths = {}
        for scope in service.SCOPES:
            for kind in service.KINDS:
                path = service.request(scope, kind, 0, 1)['path']
                if scope != 'InterCity':
                    path = path.replace(scope, '{City}')
                parameters = [{'name': value} for value in ('$top', '$skip', '$orderby', '$format')]
                if scope != 'InterCity':
                    parameters.append({'name': 'City', 'schema': {'enum': list(service.SCOPES)}})
                paths[path] = {'get': {'parameters': parameters}}
        return json.dumps({'servers': [{'url': service.HOST + '/api/basic'}], 'paths': paths}).encode()

    def run_fetch(self, responses, args=None, clock=None, main=False, store=None):
        clock = clock or Clock()
        store = store or self.store
        calls, pending = [], iter(responses)

        def open_response(req, **kwargs):
            if req.full_url == service.SPEC_URL:
                return Response(self.specification())
            calls.append((req.full_url, clock.time()))
            response = next(pending)
            if isinstance(response, BaseException):
                raise response
            return response

        with patch.object(service, 'urlopen', side_effect=open_response), patch.object(service, 'token', return_value='fake-token'), \
                patch.object(service.time, 'time', side_effect=clock.time), patch.object(service.time, 'sleep', side_effect=clock.sleep), \
                patch.object(service, 'now', side_effect=clock.now), redirect_stdout(io.StringIO()):
            if main:
                service.main(['--root', str(store.root), '--fetch', '--scope', 'Keelung', '--kind', 'Schedule', '--schedule-page-size', '2'])
            else:
                service.fetch(store, args or self.args())
        return calls

    def test_nonterminal_http_backoff_delays_the_next_scope(self):
        for headers, minimum in [({'Retry-After': '120'}, 120), ({'X-RateLimit-Remaining-Minute': '0'}, 60)]:
            with self.subTest(headers=headers), tempfile.TemporaryDirectory() as temporary:
                store = service.Store(Path(temporary))
                error = HTTPError('https://official.example', 503, 'unavailable', headers, io.BytesIO())
                calls = self.run_fetch([error, Response(b'[]')], self.args(scope=['Keelung', 'InterCity']), store=store)
                self.assertGreaterEqual(calls[1][1] - calls[0][1], minimum)
                self.assertIn('finished_at', store.manifest['metadata']['last_batch'])

    def test_http_backoff_survives_budget_stop_and_resume(self):
        clock = Clock()
        failure = HTTPError('https://official.example', 503, 'unavailable', {'Retry-After': '120'}, io.BytesIO())
        first = self.run_fetch([failure], self.args(scope=['Keelung', 'InterCity'], max_requests=1), clock)
        self.assertIn('finished_at', self.store.manifest['metadata']['last_batch'])
        clock.sleep(15)
        resumed = service.Store(self.root)
        later = self.run_fetch([Response(b'[]')], self.args(scope=['InterCity']), clock, store=resumed)
        self.assertGreaterEqual(later[0][1] - first[0][1], 120)

    def test_invalid_normalization_or_nonfinite_json_never_installs_a_successful_page(self):
        for label in ('nan', 'nan_id', 'collision', 'surrogate'):
            with self.subTest(label=label), tempfile.TemporaryDirectory() as temporary:
                store = service.Store(Path(temporary))
                row = self.model()
                if label == 'nan_id':
                    row['Timetables'][0]['StopTimes'][0]['StopID'] = float('nan')
                elif label == 'surrogate':
                    row['Name'] = '\ud800'
                else:
                    row['UnknownValue' if label == 'nan' else 'route_id'] = float('nan') if label == 'nan' else 'collision'
                req = service.request('Keelung', 'Schedule', 0, 2)
                raw = store.raw / 'responses' / (req['source_id'] + '.json.gz')
                raw.parent.mkdir(parents=True)
                previous = gzip.compress(json.dumps([self.model('KEE999')]).encode(), mtime=0)
                raw.write_bytes(previous)
                self.run_fetch([Response(json.dumps([row]).encode())], store=store)
                self.assertEqual(store.receipt(req['source_id'])['status'], 'failed')
                self.assertEqual(raw.read_bytes(), previous)
                self.assertEqual(store.manifest['metadata']['last_batch']['successful_source_ids'], [])
                self.assertFalse(list(raw.parent.glob('*.part')))
                self.run_fetch([Response(json.dumps([self.model()]).encode())], store=store)
                self.assertEqual(store.receipt(req['source_id'])['status'], 'downloaded')
                self.assertEqual(store.rebuild()[0]['record_count'], 1)

    def test_main_rebuilds_failed_first_page_after_transport_errors(self):
        for label, error in [('timeout', TimeoutError()), ('connection', URLError('offline')), ('short_read', IncompleteRead(b'[', 100))]:
            with self.subTest(label=label), tempfile.TemporaryDirectory() as temporary:
                store = service.Store(Path(temporary))
                clock = Clock()
                started = clock.time()
                response = Response(b'', headers={'X-RateLimit-Remaining-Minute': '0'}, error=error) if label == 'short_read' else error
                with self.assertRaises(RuntimeError):
                    self.run_fetch([response], clock=clock, main=True, store=store)
                saved = service.Store(store.root)
                req = service.request('Keelung', 'Schedule', 0, 2)
                self.assertEqual(saved.receipt(req['source_id'])['status'], 'failed')
                batch = saved.manifest['metadata']['last_batch']
                self.assertEqual(batch['new_business_request_count'], 1)
                self.assertEqual(batch['successful_source_ids'], [])
                self.assertIn('finished_at', batch)
                coverage = json.loads((saved.out / 'coverage.json').read_bytes())['records'][0]
                self.assertEqual(coverage['scope_status'], 'partial_contiguous_pages')
                self.assertEqual(coverage['next_pages_or_source_gaps'][0]['status'], 'failed')
                if label == 'short_read':
                    clock.sleep(15)
                    retry = self.run_fetch([Response(b'[]')], clock=clock, store=saved)
                    self.assertGreaterEqual(retry[0][1] - started, 60)

    def test_main_rebuilds_invalid_first_page_without_installing_poisoned_raw(self):
        row = {**self.model(), 'route_id': 'collision'}
        self.run_fetch([Response(json.dumps([row]).encode())], main=True)
        saved = service.Store(self.root)
        req = service.request('Keelung', 'Schedule', 0, 2)
        self.assertEqual(saved.receipt(req['source_id'])['status'], 'failed')
        self.assertFalse((saved.raw / 'responses' / (req['source_id'] + '.json.gz')).exists())
        coverage = json.loads((saved.out / 'coverage.json').read_bytes())['records'][0]
        self.assertEqual(coverage['record_count'], 0)
        self.assertFalse(coverage['empty_endpoint_response'])

    def test_terminal_provider_error_still_finalizes_attempted_batch(self):
        failure = HTTPError('https://official.example', 429, 'limited', {'Retry-After': '90'}, io.BytesIO())
        with self.assertRaises(RuntimeError):
            self.run_fetch([failure], main=True)
        saved = service.Store(self.root)
        self.assertIn('finished_at', saved.manifest['metadata']['last_batch'])
        self.assertEqual(saved.manifest['metadata']['last_batch']['successful_source_ids'], [])

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
