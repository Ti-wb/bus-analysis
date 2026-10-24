"""Synthetic contract tests; live responses are independently reviewed separately."""
from __future__ import annotations

from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from scripts import tdx_bus_travel_time as bus


def spec():
    return {'servers': [{'url': bus.HOST + '/api/premium'}], 'paths': {
        bus.CITY_PATH: {'get': {'parameters': [
            {'name': 'City', 'in': 'path', 'schema': {'enum': ['Taipei', 'NewTaipei', 'Keelung']}},
            {'name': 'RouteName', 'in': 'path', 'required': True}]}},
        bus.INTERCITY_PATH: {'get': {'parameters': [
            {'name': 'RouteName', 'in': 'path', 'required': True}]}}}}


def model(**values):
    return {'RouteUID': 'TPE001', 'RouteID': '001', 'SubRouteUID': 'TPE0002', 'SubRouteID': '0002',
            'RouteName': '測試線', 'Direction': 0, 'EffectiveDate': '2026-10-01',
            'ExpireDate': '2026-10-31', 'UpdateTime': '2026-10-21T15:47:11',
            'TravelTimes': [{'Sequence': 1, 'ToStopID': '0004', 'StartTime': '23:00',
                             'EndTime': '24:00', 'ServiceTag': '1'},
                            {'Sequence': 2, 'FromStopID': '0004', 'ToStopID': '0005',
                             'RunTime': 0, 'StartTime': '23:00', 'EndTime': '24:00', 'ServiceTag': '1'}], **values}


class TravelTimeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.request = bus.travel_request(spec(), 'Taipei', '測試線')
        self.source = {'downloaded_at': '2026-10-21T23:47:11+08:00', 'fixture': 'synthetic'}

    def test_missing_runtime_and_first_stop_preserved_null(self):
        result = bus.normalize_travel([model()], self.request, self.source)
        record = result['records'][0]
        self.assertEqual(record['route_id'], '001')
        self.assertIsNone(record['travel_times'][0]['from_stop_id'])
        self.assertIsNone(record['travel_times'][0]['run_time'])
        self.assertEqual(record['travel_times'][1]['run_time'], 0)
        self.assertEqual(result['metadata']['missing_run_time_count'], 1)
        self.assertEqual(result['metadata']['missing_from_stop_id_count'], 1)
        self.assertEqual(result['metadata']['travel_time_row_count'], 2)

    def test_period_source_time_and_24_clock_are_not_download_date(self):
        result = bus.normalize_travel([model()], self.request, self.source)
        self.assertEqual(result['metadata']['source_updated_at'], '2026-10-21T15:47:11')
        self.assertEqual(result['metadata']['statistical_period']['expire_dates'], ['2026-10-31'])
        self.assertEqual(result['records'][0]['travel_times'][0]['end_time'], '24:00')
        self.assertEqual(result['metadata']['sources'][0]['downloaded_at'], self.source['downloaded_at'])

    def test_different_direction_service_and_hour_are_distinct_grains(self):
        one = model()
        two = model(Direction=1)
        two['TravelTimes'][0]['ServiceTag'] = '0'
        result = bus.normalize_travel([one, two], self.request, self.source)
        self.assertEqual(result['metadata']['duplicate_grain_count'], 0)
        self.assertEqual(result['metadata']['travel_time_row_count'], 4)

    def test_duplicates_are_counted_and_preserved(self):
        one = model()
        one['TravelTimes'].append(dict(one['TravelTimes'][0]))
        result = bus.normalize_travel([one], self.request, self.source)
        self.assertEqual(result['metadata']['duplicate_grain_count'], 1)
        self.assertEqual(result['metadata']['travel_time_row_count'], 3)

    def test_bad_runtime_and_error_object_rejected(self):
        for runtime in [-1, float('nan'), float('inf'), True, '10']:
            one = model()
            one['TravelTimes'][0]['RunTime'] = runtime
            with self.subTest(runtime=runtime), self.assertRaises(ValueError):
                bus.normalize_travel([one], self.request, self.source)
        with self.assertRaises(ValueError):
            bus.normalize_travel({'Message': 'rejected'}, self.request, self.source)

    def test_empty_response_does_not_mean_citywide_no_service(self):
        result = bus.normalize_travel([], self.request, self.source)
        self.assertEqual(result['metadata']['scope_status'], 'empty_requested_endpoint_response')
        self.assertEqual(result['metadata']['citywide_coverage'], 'bounded_selected_route_requests_only')

    def test_intercity_endpoint_and_safe_route_encoding(self):
        request = bus.travel_request(spec(), 'InterCity', '1815/甲')
        self.assertIn('/InterCity/RouteName/1815%2F%E7%94%B2?', request['download_url'])
        self.assertNotIn('/City/', request['download_url'])
        self.assertNotIn('/', request['source_id'])
        with self.assertRaises(ValueError):
            bus.travel_request(spec(), 'OtherCity', '1')

    def test_date_parameter_contract_change_requires_review(self):
        current = spec()
        bus.validate_spec(current)
        current['paths'][bus.CITY_PATH]['get']['parameters'].append({'name': 'Date'})
        with self.assertRaises(ValueError):
            bus.validate_spec(current)

    def test_default_plan_has_no_network_or_writes(self):
        path = self.root / 'raw_data/tdx_bus_travel_time/specifications/travel_time.json'
        bus.write(path, spec())
        before = sorted(str(x.relative_to(self.root)) for x in self.root.rglob('*'))
        with patch.object(bus, 'urlopen', side_effect=AssertionError('no network')), patch.object(bus, 'token', side_effect=AssertionError('no auth')), redirect_stdout(io.StringIO()) as stdout:
            bus.main(['--root', str(self.root), '--route', 'Taipei:測試線', '--as-of', '2026-10-24'])
        result = json.loads(stdout.getvalue())
        self.assertEqual(result['metadata']['planned_this_batch_count'], 2)
        self.assertEqual(result['records'][0]['reference_date'], '2026-10-23')
        self.assertEqual(before, sorted(str(x.relative_to(self.root)) for x in self.root.rglob('*')))

    def test_reference_count_mismatch_is_not_complete_catalog(self):
        store = bus.Store(self.root)
        path = store.raw / 'responses/routes.json'
        bus.write(path, {'Count': 3, 'StartDate': '2026-10-01', 'BusRoutes': [{'RouteUID': 'TPE001'}]})
        store.upsert({**bus.route_request('2026-10-23'), 'status': 'downloaded',
                      'raw_file': str(path.relative_to(self.root)), 'bytes': path.stat().st_size})
        with self.assertRaises(ValueError):
            store.rebuild(bus.date(2026, 10, 24))

    def test_offline_receipt_detects_missing_and_truncated_raw(self):
        store = bus.Store(self.root)
        source = {'source_id': 'synthetic', 'raw_file': 'raw_data/source.json', 'bytes': 2}
        with self.assertRaises(ValueError):
            store.raw_body(source)
        path = self.root / source['raw_file']
        path.parent.mkdir(parents=True)
        path.write_text('[{}]')
        with self.assertRaises(ValueError):
            store.raw_body(source)

    def test_failed_refresh_preserves_last_usable_receipt(self):
        store = bus.Store(self.root)
        store.upsert({'source_id': 'same', 'status': 'downloaded', 'raw_file': 'last-good.json'})
        store.upsert({'source_id': 'same', 'status': 'failed', 'http_status': 500})
        source = store.successful('same')
        self.assertEqual(source['raw_file'], 'last-good.json')
        self.assertEqual(source['last_failed_attempt']['http_status'], 500)

    def test_declared_length_mismatch_with_valid_json_preserves_cached_response(self):
        store = bus.Store(self.root)
        path = store.raw / 'responses' / (self.request['source_id'] + '.json')
        bus.write(path, [model(RouteName='前次可用版本')])
        old_bytes = path.read_bytes()
        old_receipt = {**self.request, 'status': 'downloaded', 'raw_file': str(path.relative_to(self.root)),
                       'bytes': len(old_bytes), 'downloaded_at': '2026-10-21T10:00:00+08:00'}
        store.upsert(old_receipt)
        payload = json.dumps([model()]).encode()
        # The transport returns syntactically valid JSON with a larger declared
        # length, distinguishing this failure from a JSON decoding failure.
        response = io.BytesIO(payload)
        response.status = 200
        response.headers = {'Content-Type': 'application/json', 'Content-Length': str(len(payload) + 17)}
        args = SimpleNamespace(max_requests=1, max_response_mb=8, max_total_mb=20,
                               min_request_interval=13, refresh=True)
        with patch.object(bus, 'token', return_value='synthetic-token'), patch.object(bus, 'urlopen', return_value=response), redirect_stdout(io.StringIO()):
            result = bus.fetch_requests(store, [self.request], args)
        self.assertEqual(result['requests_used'], 1)
        self.assertEqual(path.read_bytes(), old_bytes)
        source = bus.Store(self.root).successful(self.request['source_id'])
        self.assertEqual({k: source[k] for k in old_receipt}, old_receipt)
        self.assertEqual(source['last_failed_attempt']['failure_reason'], 'response_length_mismatch')
        self.assertFalse(path.with_suffix('.part').exists())

    def test_http_429_retains_safe_retry_headers_and_stops_before_next_scope(self):
        store = bus.Store(self.root)
        args = SimpleNamespace(max_requests=2, max_response_mb=8, max_total_mb=20,
                               min_request_interval=13, refresh=False)
        headers = {'Retry-After': '47', 'X-RateLimit-Limit-Minute': '5',
                   'X-RateLimit-Remaining-Minute': '0', 'X-Rate-Limit-Reset': '123456',
                   'Authorization': 'Bearer synthetic-private', 'Set-Cookie': 'synthetic-private'}
        error = HTTPError(self.request['download_url'], 429, 'Too many requests', headers, None)
        next_request = bus.travel_request(spec(), 'Taipei', '下一條')
        with patch.object(bus, 'token', return_value='synthetic-private'), patch.object(bus, 'urlopen', side_effect=error) as transport, redirect_stdout(io.StringIO()), self.assertRaises(RuntimeError):
            bus.fetch_requests(store, [self.request, next_request], args)
        self.assertEqual(transport.call_count, 1)
        source = bus.Store(self.root).manifest['records'][0]
        self.assertEqual(source['http_status'], 429)
        self.assertEqual(source['usage_headers'], {'retry-after': '47', 'x-ratelimit-limit-minute': '5',
                         'x-ratelimit-remaining-minute': '0', 'x-rate-limit-reset': '123456'})
        self.assertNotIn('synthetic-private', store.path.read_text())
        self.assertFalse((store.raw / 'responses' / (self.request['source_id'] + '.part')).exists())

    def test_resumed_batch_records_only_new_http_attempt_not_cached_receipt(self):
        store = bus.Store(self.root)
        cached = bus.travel_request(spec(), 'Taipei', '先前已取得')
        store.upsert({**cached, 'status': 'downloaded', 'downloaded_at': '2026-10-21T10:00:00+08:00'})
        store.manifest['metadata']['last_batch'] = {'planned_requests': [cached, self.request],
            'attempted_source_ids': [], 'successful_source_ids': [], 'new_business_request_count': 0}
        store.save()
        response = io.BytesIO(json.dumps([model()]).encode())
        response.status = 200
        response.headers = {'Content-Type': 'application/json'}
        args = SimpleNamespace(max_requests=1, max_response_mb=8, max_total_mb=20,
                               min_request_interval=13, refresh=False)

        def transport(request, **kwargs):
            saved = bus.Store(self.root).manifest['metadata']['last_batch']
            self.assertEqual(saved['attempted_source_ids'], [self.request['source_id']])
            self.assertEqual(saved['new_business_request_count'], 1)
            self.assertEqual(saved['successful_source_ids'], [])
            return response

        with patch.object(bus, 'token', return_value='synthetic-token'), patch.object(bus, 'urlopen', side_effect=transport) as opener, redirect_stdout(io.StringIO()):
            result = bus.fetch_requests(store, [cached, self.request], args)
        self.assertEqual(opener.call_count, 1)
        self.assertEqual(result['requests_used'], 1)
        saved = bus.Store(self.root).manifest
        self.assertEqual(saved['metadata']['last_batch']['attempted_source_ids'], [self.request['source_id']])
        self.assertEqual(saved['metadata']['last_batch']['successful_source_ids'], [self.request['source_id']])
        self.assertEqual(len([r for r in saved['records'] if r['status'] == 'downloaded']), 2)

    def test_cached_main_rerun_initializes_zero_request_batch(self):
        store = bus.Store(self.root)
        bus.write(store.raw / 'specifications/travel_time.json', spec())
        reference = bus.route_request('2026-10-23')
        for request in [reference, self.request]:
            store.upsert({**request, 'status': 'downloaded', 'downloaded_at': '2026-10-21T10:00:00+08:00'})
        store.manifest['metadata']['last_batch'] = {'planned_requests': [reference, self.request],
            'attempted_source_ids': ['previous-request'], 'successful_source_ids': ['previous-request'],
            'new_business_request_count': 1}
        store.save()
        response = io.BytesIO(json.dumps(spec()).encode())
        response.status = 200
        response.headers = {'Content-Type': 'application/json'}
        with patch.object(bus, 'token', return_value='synthetic-token'), patch.object(bus, 'urlopen', return_value=response) as opener, patch.object(bus.Store, 'rebuild', return_value=[]), redirect_stdout(io.StringIO()):
            bus.main(['--root', str(self.root), '--fetch', '--route', 'Taipei:測試線', '--as-of', '2026-10-24'])
        # The public specification request is separate from business requests.
        self.assertEqual(opener.call_count, 1)
        saved = bus.Store(self.root).manifest
        batch = saved['metadata']['last_batch']
        self.assertEqual(batch['attempted_source_ids'], [])
        self.assertEqual(batch['successful_source_ids'], [])
        self.assertEqual(batch['new_business_request_count'], 0)
        self.assertEqual(batch['status'], 'bounded_batch_finished')
        self.assertEqual({r['source_id'] for r in batch['planned_requests']}, {reference['source_id'], self.request['source_id']})

    def test_failed_http_attempt_is_counted_without_successful_id(self):
        store = bus.Store(self.root)
        store.manifest['metadata']['last_batch'] = {'planned_requests': [self.request],
            'attempted_source_ids': [], 'successful_source_ids': [], 'new_business_request_count': 0}
        args = SimpleNamespace(max_requests=1, max_response_mb=8, max_total_mb=20,
                               min_request_interval=13, refresh=False)
        error = HTTPError(self.request['download_url'], 429, 'Too many requests', {'Retry-After': '47'}, None)
        with patch.object(bus, 'token', return_value='synthetic-token'), patch.object(bus, 'urlopen', side_effect=error), redirect_stdout(io.StringIO()), self.assertRaises(RuntimeError):
            bus.fetch_requests(store, [self.request], args)
        batch = bus.Store(self.root).manifest['metadata']['last_batch']
        self.assertEqual(batch['attempted_source_ids'], [self.request['source_id']])
        self.assertEqual(batch['successful_source_ids'], [])
        self.assertEqual(batch['new_business_request_count'], 1)


if __name__ == '__main__':
    unittest.main()
