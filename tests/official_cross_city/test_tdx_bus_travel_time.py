"""Synthetic contract tests; live responses are independently reviewed separately."""
from __future__ import annotations

from contextlib import contextmanager, redirect_stdout
from http.client import IncompleteRead
import io
import gzip
import json
from pathlib import Path
import ssl
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError

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

    def response(self, body):
        payload = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode()
        result = io.BytesIO(payload)
        result.status = 200
        result.headers = {'Content-Type': 'application/json', 'Content-Length': str(len(payload))}
        return result

    def reference_body(self):
        return {'Count': 1, 'StartDate': '2026-10-01', 'EndDate': '2026-10-31',
                'BusRoutes': [{'RouteUID': 'TPE001', 'RouteID': '001',
                               'RouteName': {'Zh_tw': '測試線'}, 'SubRoutes': []}]}

    def seed_snapshot(self, root, compressed=False):
        store = bus.Store(root)
        bus.write(store.raw / 'specifications/travel_time.json', spec())
        for request, body in [(bus.route_request('2026-10-23'), self.reference_body()),
                              (self.request, [model(RouteName='前次有效模型')])]:
            payload = json.dumps(body, ensure_ascii=False).encode()
            path = store.raw / 'responses' / (request['source_id'] + ('.json.gz' if compressed else '.json'))
            path.parent.mkdir(parents=True, exist_ok=True)
            stored = gzip.compress(payload, mtime=0) if compressed else payload
            path.write_bytes(stored)
            source = {**request, 'status': 'downloaded', 'raw_file': str(path.relative_to(root)),
                      'downloaded_at': '2026-10-21T10:00:00+08:00', 'bytes': len(payload), 'http_status': 200}
            if compressed:
                source.update(storage_encoding='gzip', stored_bytes=len(stored))
            store.upsert(source)
        store.rebuild(bus.date(2026, 10, 24))
        return store

    @contextmanager
    def virtual_clock(self):
        current = [bus.datetime.fromisoformat('2026-10-24T12:00:00+08:00').timestamp()]
        def sleep(seconds):
            current[0] += seconds
        with patch.object(bus.time, 'time', side_effect=lambda: current[0]), \
                patch.object(bus.time, 'monotonic', side_effect=lambda: current[0]), \
                patch.object(bus.time, 'sleep', side_effect=sleep), \
                patch.object(bus, 'now', side_effect=lambda: bus.datetime.fromtimestamp(current[0], bus.ZoneInfo('Asia/Taipei')).isoformat(timespec='seconds')):
            yield current

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

    def test_citywide_endpoint_progress_keeps_unknown_and_source_unavailable_distinct(self):
        def route(url, status, availability=None, retained=False):
            return {'download_url': url, 'endpoint_acquisition_status': status,
                    'endpoint_availability_status': availability,
                    'retained_files': ['synthetic.json'] if retained else []}
        catalog = [route('same-name', 'downloaded', 'models_returned', True),
                   route('same-name', 'downloaded', 'models_returned', True),
                   route('pending', 'not_requested'),
                   route('unavailable', 'failed', 'published_no_data_message'),
                   route('technical', 'failed', 'technical_failure'),
                   route('empty', 'downloaded', 'published_empty_array')]
        progress = bus.taipei_endpoint_acquisition(catalog)
        self.assertEqual(progress, {
            'reference_route_count': 6, 'unique_endpoint_count': 5,
            'attempted_endpoint_count': 4, 'successful_endpoint_count': 2,
            'source_unavailable_endpoint_count': 1, 'technical_failure_endpoint_count': 1,
            'not_requested_endpoint_count': 1, 'reference_route_uids_retained_count': 2,
            'all_reference_endpoints_attempted': False})
        # All requests attempted does not imply all models or runtimes exist.
        complete_attempts = bus.taipei_endpoint_acquisition([r for r in catalog if r['download_url'] != 'pending'])
        self.assertTrue(complete_attempts['all_reference_endpoints_attempted'])
        self.assertEqual(complete_attempts['source_unavailable_endpoint_count'], 1)
        self.assertEqual(complete_attempts['reference_route_uids_retained_count'], 2)

    def test_endpoint_progress_rejects_inconsistent_shared_endpoint_states(self):
        catalog = [{'download_url': 'same', 'endpoint_acquisition_status': status,
                    'endpoint_availability_status': None, 'retained_files': []}
                   for status in ['not_requested', 'downloaded']]
        with self.assertRaises(ValueError):
            bus.taipei_endpoint_acquisition(catalog)

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

    def test_http_error_headers_delay_the_next_scope_in_the_same_batch(self):
        for label, headers, expected_delay in [('retry', {'Retry-After': '90'}, 90),
                                                ('remaining', {'X-RateLimit-Remaining-Minute': '0'}, 60)]:
            with self.subTest(header=label):
                store = bus.Store(self.root / label)
                next_request = bus.travel_request(spec(), 'Taipei', '下一條')
                args = SimpleNamespace(max_requests=2, max_response_mb=8, max_total_mb=20,
                                       min_request_interval=13, refresh=False)
                calls = []
                with self.virtual_clock() as current:
                    def transport(request, **kwargs):
                        calls.append(current[0])
                        if len(calls) == 1:
                            raise HTTPError(self.request['download_url'], 503, 'Unavailable', headers, None)
                        return self.response([model()])
                    with patch.object(bus, 'token', return_value='synthetic'), patch.object(bus, 'urlopen', side_effect=transport), redirect_stdout(io.StringIO()):
                        result = bus.fetch_requests(store, [self.request, next_request], args)
                self.assertEqual(result['requests_used'], 2)
                self.assertGreaterEqual(calls[1] - calls[0], expected_delay)

    def test_resume_honors_error_headers_from_failed_refresh(self):
        store = bus.Store(self.root)
        with self.virtual_clock() as current:
            observed = current[0]
            store.upsert({**self.request, 'status': 'downloaded', 'downloaded_at': '2026-10-21T10:00:00+08:00'})
            store.upsert({**self.request, 'status': 'failed', 'checked_at': bus.now(), 'http_status': 503,
                          'usage_headers': {'retry-after': '90'}})
            current[0] += 10
            next_request = bus.travel_request(spec(), 'Taipei', '下一條')
            calls = []
            def transport(request, **kwargs):
                calls.append(current[0])
                return self.response([model()])
            args = SimpleNamespace(max_requests=1, max_response_mb=8, max_total_mb=20,
                                   min_request_interval=13, refresh=False)
            with patch.object(bus, 'token', return_value='synthetic'), patch.object(bus, 'urlopen', side_effect=transport), redirect_stdout(io.StringIO()):
                bus.fetch_requests(store, [self.request, next_request], args)
        self.assertEqual(len(calls), 1)
        self.assertGreaterEqual(calls[0] - observed, 90)

    def test_main_successful_refresh_switches_raw_and_output_encoding_without_stale_files(self):
        for compressed in [False, True]:
            with self.subTest(new_compressed=compressed):
                root = self.root / str(compressed)
                store = self.seed_snapshot(root, not compressed)
                old_source = dict(store.successful(self.request['source_id']))
                old_raw = root / old_source['raw_file']
                old_output = store.out / 'observations' / (self.request['source_id'] + ('.json' if compressed else '.json.gz'))
                responses = [self.response(spec()), self.response(self.reference_body()), self.response([model(RouteName='新模型')])]
                arguments = ['--root', str(root), '--fetch', '--refresh', '--route', 'Taipei:測試線', '--as-of', '2026-10-24']
                if compressed:
                    arguments.append('--compress')
                with self.virtual_clock(), patch.object(bus, 'token', return_value='synthetic'), patch.object(bus, 'urlopen', side_effect=responses), redirect_stdout(io.StringIO()):
                    bus.main(arguments)
                latest = bus.Store(root).successful(self.request['source_id'])
                self.assertTrue((root / latest['raw_file']).is_file())
                self.assertFalse(old_raw.exists())
                self.assertFalse(old_output.exists())
                new_output = store.out / 'observations' / (self.request['source_id'] + ('.json.gz' if compressed else '.json'))
                stored = new_output.read_bytes()
                result = json.loads(gzip.decompress(stored) if compressed else stored)
                self.assertEqual(result['records'][0]['route_name'], '新模型')
                self.assertFalse(list(root.rglob('*.part')))

    def test_main_transport_failures_finalize_progress_and_preserve_refresh_snapshot(self):
        for refresh in [False, True]:
            for label in ['timeout', 'connection', 'incomplete', 'os_read', 'tls_read']:
                with self.subTest(refresh=refresh, failure=label):
                    root = self.root / (str(refresh) + label)
                    store = self.seed_snapshot(root)
                    old_source = dict(store.successful(self.request['source_id']))
                    old_raw = (root / old_source['raw_file']).read_bytes()
                    class SocketReadError(OSError):
                        pass
                    error = {'timeout': TimeoutError('timed out'), 'connection': URLError(ConnectionResetError('reset')),
                             'incomplete': IncompleteRead(b'partial', 20), 'os_read': SocketReadError('read failed'),
                             'tls_read': ssl.SSLError('TLS read failed')}[label]
                    broken = self.request if refresh else bus.travel_request(spec(), 'Taipei', '失敗線')
                    responses = [self.response(spec())]
                    if refresh:
                        responses.append(self.response(self.reference_body()))
                    responses.append(self.response([model(RouteUID='TPE002', RouteName='本批成功')]))
                    read_failure = label in {'incomplete', 'os_read', 'tls_read'}
                    if read_failure:
                        interrupted = self.response(b'partial')
                        def read_interrupted(size):
                            raise error
                        interrupted.read = read_interrupted
                        responses.append(interrupted)
                    else:
                        responses.append(error)
                    arguments = ['--root', str(root), '--fetch', '--route', 'Taipei:新增線', '--route', 'Taipei:' + broken['route_name'], '--as-of', '2026-10-24']
                    if refresh:
                        arguments.append('--refresh')
                    with self.virtual_clock(), patch.object(bus, 'token', return_value='synthetic'), patch.object(bus, 'urlopen', side_effect=responses), redirect_stdout(io.StringIO()), self.assertRaises(type(error)):
                        bus.main(arguments)
                    saved = bus.Store(root)
                    batch = saved.manifest['metadata']['last_batch']
                    self.assertEqual(batch['status'], 'partial_stopped')
                    self.assertEqual(batch['stop_reason'], 'transport_error')
                    self.assertEqual(batch['new_business_request_count'], 3 if refresh else 2)
                    self.assertEqual(len(batch['successful_source_ids']), 2 if refresh else 1)
                    failed = next(r for r in saved.manifest['records'] if r['source_id'] == broken['source_id'])
                    failure = failed['last_failed_attempt'] if refresh else failed
                    self.assertEqual(failure['failure_reason'], 'transport_error')
                    self.assertEqual(failure['transport_error_type'], type(error).__name__)
                    self.assertEqual(failure.get('http_status'), 200 if read_failure else None)
                    self.assertEqual((root / old_source['raw_file']).read_bytes(), old_raw)
                    self.assertEqual(saved.successful(self.request['source_id'])['downloaded_at'], old_source['downloaded_at'])
                    coverage = json.loads((store.out / 'coverage.json').read_text())
                    self.assertIn('本批成功', [r['RouteName'] for r in saved.raw_body(saved.successful('Taipei_%E6%96%B0%E5%A2%9E%E7%B7%9A'))])
                    self.assertEqual(coverage['metadata']['successful_response_count'], 2)
                    self.assertFalse(list(root.rglob('*.part')))

    def test_main_strict_serialization_failure_preserves_valid_refresh_before_install(self):
        for compressed in [False, True]:
            with self.subTest(compressed=compressed):
                root = self.root / str(compressed)
                store = self.seed_snapshot(root, compressed)
                old_source = dict(store.successful(self.request['source_id']))
                old_raw = (root / old_source['raw_file']).read_bytes()
                responses = [self.response(spec()), self.response(self.reference_body()),
                             self.response([model(RouteName='不能安裝的模型', UpdateInterval=float('nan'))])]
                arguments = ['--root', str(root), '--fetch', '--refresh', '--route', 'Taipei:測試線', '--as-of', '2026-10-24']
                if compressed:
                    arguments.append('--compress')
                with self.virtual_clock(), patch.object(bus, 'token', return_value='synthetic'), patch.object(bus, 'urlopen', side_effect=responses), redirect_stdout(io.StringIO()):
                    bus.main(arguments)
                latest = bus.Store(root).successful(self.request['source_id'])
                self.assertEqual((root / old_source['raw_file']).read_bytes(), old_raw)
                self.assertEqual({key: latest[key] for key in old_source}, old_source)
                self.assertEqual(latest['last_failed_attempt']['failure_reason'], 'unusable_response_format_or_contract')
                output_path = store.out / 'observations' / (self.request['source_id'] + ('.json.gz' if compressed else '.json'))
                body = output_path.read_bytes()
                output = json.loads(gzip.decompress(body) if compressed else body)
                self.assertEqual(output['records'][0]['route_name'], '前次有效模型')
                self.assertNotIn(self.request['source_id'], bus.Store(root).manifest['metadata']['last_batch']['successful_source_ids'])

    def test_main_first_reference_timeout_finalizes_manifest_without_masking_transport_error(self):
        bus.write(bus.Store(self.root).raw / 'specifications/travel_time.json', spec())
        with self.virtual_clock(), patch.object(bus, 'token', return_value='synthetic'), \
                patch.object(bus, 'urlopen', side_effect=[self.response(spec()), TimeoutError('timed out')]), \
                redirect_stdout(io.StringIO()), self.assertRaises(TimeoutError):
            bus.main(['--root', str(self.root), '--fetch', '--route', 'Taipei:測試線', '--as-of', '2026-10-24'])
        saved = bus.Store(self.root)
        batch = saved.manifest['metadata']['last_batch']
        self.assertEqual(batch['status'], 'partial_stopped')
        self.assertEqual(batch['stop_reason'], 'transport_error')
        self.assertEqual(batch['attempted_source_ids'], [bus.route_request('2026-10-23')['source_id']])
        failure = next(r for r in saved.manifest['records'] if r['kind'] == 'route_reference')
        self.assertEqual(failure['failure_reason'], 'transport_error')
        self.assertFalse((saved.out / 'coverage.json').exists())

    def test_nonfinite_or_unencodable_source_cannot_be_hidden_by_normalization(self):
        for label, body in [('nan_id', [model(RouteID=float('nan'))]),
                            ('surrogate', [model(ValidityDescription='\ud800')])]:
            with self.subTest(source=label):
                root = self.root / label
                store = self.seed_snapshot(root, True)
                old_source = dict(store.successful(self.request['source_id']))
                old_raw = (root / old_source['raw_file']).read_bytes()
                args = SimpleNamespace(max_requests=1, max_response_mb=8, max_total_mb=20,
                                       min_request_interval=13, refresh=True, compress=True)
                payload = json.dumps(body, ensure_ascii=True).encode()
                with self.virtual_clock(), patch.object(bus, 'token', return_value='synthetic'), \
                        patch.object(bus, 'urlopen', return_value=self.response(payload)), redirect_stdout(io.StringIO()):
                    bus.fetch_requests(store, [self.request], args)
                latest = bus.Store(root).successful(self.request['source_id'])
                self.assertEqual((root / old_source['raw_file']).read_bytes(), old_raw)
                self.assertEqual({key: latest[key] for key in old_source}, old_source)
                self.assertEqual(latest['last_failed_attempt']['failure_reason'], 'unusable_response_format_or_contract')

    def test_local_part_file_error_is_not_mislabeled_as_transport_failure(self):
        store = self.seed_snapshot(self.root, True)
        old_source = dict(store.successful(self.request['source_id']))
        old_raw = (self.root / old_source['raw_file']).read_bytes()
        part = (self.root / old_source['raw_file']).with_suffix('.part')
        original_open = Path.open
        def open_file(path, *args, **kwargs):
            if path == part:
                raise PermissionError('local write denied')
            return original_open(path, *args, **kwargs)
        args = SimpleNamespace(max_requests=1, max_response_mb=8, max_total_mb=20,
                               min_request_interval=13, refresh=True, compress=True)
        with self.virtual_clock(), patch.object(bus, 'token', return_value='synthetic'), \
                patch.object(bus, 'urlopen', return_value=self.response([model()])), \
                patch.object(Path, 'open', new=open_file), redirect_stdout(io.StringIO()), self.assertRaises(PermissionError):
            bus.fetch_requests(store, [self.request], args)
        self.assertEqual(bus.Store(self.root).successful(self.request['source_id']), old_source)
        self.assertEqual((self.root / old_source['raw_file']).read_bytes(), old_raw)
        self.assertFalse(part.exists())

    def test_remaining_batch_budget_leaves_next_endpoint_unrequested(self):
        store = bus.Store(self.root)
        payload = json.dumps([model()]).encode()
        response = io.BytesIO(payload)
        response.status = 200
        response.headers = {'Content-Type': 'application/json', 'Content-Length': str(len(payload))}
        next_request = bus.travel_request(spec(), 'Taipei', '下一條')
        args = SimpleNamespace(max_requests=2, max_response_mb=len(payload) / 1024**2,
                               max_total_mb=(len(payload) + 17) / 1024**2,
                               min_request_interval=0, refresh=False)
        with patch.object(bus, 'token', return_value='synthetic'), patch.object(bus, 'urlopen', return_value=response) as transport, redirect_stdout(io.StringIO()):
            stats = bus.fetch_requests(store, [self.request, next_request], args)
        self.assertEqual(stats, {'requests_used': 1, 'response_bytes': len(payload)})
        self.assertEqual(transport.call_count, 1)
        self.assertEqual([r['source_id'] for r in store.manifest['records']], [self.request['source_id']])
        self.assertTrue(store.needs_request(next_request['source_id'], False, False))

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

    def test_gzip_raw_and_output_preserve_full_json_and_both_byte_counts(self):
        store = bus.Store(self.root)
        response_bytes = json.dumps([model()], ensure_ascii=False).encode()
        response = io.BytesIO(response_bytes)
        response.status = 200
        response.headers = {'Content-Type': 'application/json', 'Content-Length': str(len(response_bytes))}
        args = SimpleNamespace(max_requests=1, max_response_mb=8, max_total_mb=20,
                               min_request_interval=13, refresh=False, compress=True)
        with patch.object(bus, 'token', return_value='synthetic-token'), patch.object(bus, 'urlopen', return_value=response), redirect_stdout(io.StringIO()):
            bus.fetch_requests(store, [self.request], args)
        receipt = bus.Store(self.root).successful(self.request['source_id'])
        path = self.root / receipt['raw_file']
        self.assertTrue(str(path).endswith('.json.gz'))
        self.assertEqual(receipt['storage_encoding'], 'gzip')
        self.assertEqual(receipt['bytes'], len(response_bytes))
        self.assertEqual(receipt['stored_bytes'], path.stat().st_size)
        self.assertEqual(gzip.decompress(path.read_bytes()), response_bytes)
        self.assertEqual(store.raw_body(receipt), [model()])
        dataset = bus.normalize_travel(store.raw_body(receipt), self.request, receipt)
        target = self.root / 'observation.json.gz'
        bus.write_compressed(target, dataset)
        self.assertEqual(json.loads(gzip.decompress(target.read_bytes())), dataset)
        wrong = {**receipt, 'bytes': receipt['bytes'] + 1}
        with self.assertRaises(ValueError):
            store.raw_body(wrong)

    def test_rate_limit_zero_and_retry_after_delay_next_request(self):
        self.assertEqual(bus.next_request_time({}, 1000, 15), 1015)
        self.assertEqual(bus.next_request_time({'X-RateLimit-Remaining-Minute': '0'}, 1000, 15), 1060)
        self.assertEqual(bus.next_request_time({'Retry-After': '90'}, 1000, 15), 1090)
        self.assertEqual(bus.next_request_time({'Retry-After': 'Thu, 01 Jan 1970 00:20:00 GMT'}, 1000, 15), 1200)
        with self.assertRaises(RuntimeError):
            bus.next_request_time({'Retry-After': 'unknown'}, 1000, 15)

    def test_resuming_all_routes_skips_known_failure_without_explicit_retry(self):
        store = bus.Store(self.root)
        store.upsert({**self.request, 'status': 'failed', 'http_status': 500, 'checked_at': '2026-10-21T10:00:00+08:00'})
        args = SimpleNamespace(max_requests=1, max_response_mb=8, max_total_mb=20,
                               min_request_interval=13, refresh=False, retry_failed=False)
        with patch.object(bus, 'token', return_value='synthetic-token'), patch.object(bus, 'urlopen', side_effect=AssertionError('Do not repeatedly query a failed source')):
            result = bus.fetch_requests(store, [self.request], args)
        self.assertEqual(result['requests_used'], 0)
        self.assertTrue(store.needs_request(self.request['source_id'], retry_failed=True))

    def test_complete_unusable_response_retained_separately_from_valid_cache(self):
        store = bus.Store(self.root)
        old_bytes = gzip.compress(json.dumps([model()]).encode(), mtime=0)
        path = store.raw / 'responses' / (self.request['source_id'] + '.json.gz')
        path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(old_bytes)
        receipt = {**self.request, 'status': 'downloaded', 'raw_file': str(path.relative_to(self.root)),
                   'storage_encoding': 'gzip', 'stored_bytes': len(old_bytes), 'bytes': len(gzip.decompress(old_bytes))}
        store.upsert(receipt)
        payload = b'{"Message":"No supported model"}'
        response = io.BytesIO(payload); response.status = 200
        response.headers = {'Content-Length': str(len(payload)), 'Content-Type': 'application/json'}
        args = SimpleNamespace(max_requests=1, max_response_mb=8, max_total_mb=20, min_request_interval=13, refresh=True, compress=True)
        with patch.object(bus, 'token', return_value='synthetic-token'), patch.object(bus, 'urlopen', return_value=response), redirect_stdout(io.StringIO()):
            bus.fetch_requests(store, [self.request], args)
        latest = bus.Store(self.root).successful(self.request['source_id'])
        failure = latest['last_failed_attempt']
        self.assertEqual(path.read_bytes(), old_bytes)
        self.assertNotEqual(failure['raw_file'], receipt['raw_file'])
        self.assertTrue(failure['complete_unparsed_response_retained'])
        self.assertEqual(gzip.decompress((self.root / failure['raw_file']).read_bytes()), payload)

    def test_published_no_data_message_is_source_unavailable_not_technical_failure(self):
        store = bus.Store(self.root)
        message = '目前沒有該縣市的[測試線]公車路線資料'
        payload = json.dumps({'message': message}, ensure_ascii=False).encode()
        response = io.BytesIO(payload); response.status = 200
        response.headers = {'Content-Length': str(len(payload)), 'Content-Type': 'application/json'}
        args = SimpleNamespace(max_requests=1, max_response_mb=8, max_total_mb=20, min_request_interval=13, refresh=False, compress=True)
        with patch.object(bus, 'token', return_value='synthetic-token'), patch.object(bus, 'urlopen', return_value=response), redirect_stdout(io.StringIO()):
            bus.fetch_requests(store, [self.request], args)
        source = bus.Store(self.root).manifest['records'][0]
        self.assertEqual(source['status'], 'failed')
        self.assertEqual(source['availability_status'], 'published_no_data_message')
        self.assertEqual(source['upstream_response_message'], message)
        self.assertTrue(source['complete_unparsed_response_retained'])
        self.assertEqual(gzip.decompress((self.root / source['raw_file']).read_bytes()), payload)
        self.assertFalse(store.needs_request(self.request['source_id']))
        self.assertIsNone(bus.published_no_data_message({'message': '服務暫時錯誤'}))


if __name__ == '__main__':
    unittest.main()
