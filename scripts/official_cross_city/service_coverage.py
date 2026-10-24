"""Bounded, resumable official basic-v2 stop sequences and service calendars."""
from __future__ import annotations

import argparse
from datetime import datetime
import gzip
from http.client import IncompleteRead
import json
from pathlib import Path
import time
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from scripts.tdx_bus_travel_time import TERMS, envelope, next_request_time, now, write_compressed
from scripts.update_tdx import normalize, token, write

ROOT = Path(__file__).resolve().parents[2]
HOST = 'https://tdx.transportdata.tw'
SPEC_ID = '2998e851-81d0-40f5-b26d-77e2f5ac4118'
SPEC_URL = HOST + '/webapi/File/Swagger/V3/' + SPEC_ID
PAGE = HOST + '/api-service/swagger/basic/' + SPEC_ID
KINDS = ('StopOfRoute', 'Schedule')
SCOPES = ('Keelung', 'InterCity', 'NewTaipei', 'Taipei')


def request(scope, kind, skip, top):
    if scope not in SCOPES or kind not in KINDS or skip < 0 or top < 1:
        raise ValueError('Invalid service acquisition scope')
    suffix = '/InterCity' if scope == 'InterCity' else '/City/' + scope
    path = '/v2/Bus/' + kind + suffix
    params = {'$format': 'JSON', '$top': top, '$skip': skip, '$orderby': 'RouteUID,SubRouteUID,Direction'}
    return {'source_id': f'{scope}_{kind}_skip_{skip}_top_{top}', 'scope': scope, 'service': kind,
            'path': path, 'skip': skip, 'top': top, 'source_page': PAGE,
            'download_url': HOST + '/api/basic' + path + '?' + urlencode(params)}


def validate_spec(spec):
    if spec['servers'][0]['url'] != HOST + '/api/basic':
        raise ValueError('Unexpected official API server')
    for scope in SCOPES:
        for kind in KINDS:
            path = request(scope, kind, 0, 1)['path']
            template = path if scope == 'InterCity' else path.replace(scope, '{City}')
            parameters = spec['paths'][template]['get']['parameters']
            if not {'$top', '$skip', '$orderby', '$format'}.issubset({p['name'] for p in parameters}):
                raise ValueError('Official paging contract changed')
            if scope != 'InterCity' and scope not in next(p['schema']['enum'] for p in parameters if p['name'] == 'City'):
                raise ValueError('Requested city not accepted by official specification')


def validate_rows(rows, kind):
    if not isinstance(rows, list) or not all(isinstance(r, dict) for r in rows):
        raise ValueError('Expected official basic-v2 response array')
    for row in rows:
        if not isinstance(row.get('RouteUID'), str) or not isinstance(row.get('SubRouteUID'), str):
            raise ValueError('Missing route/subroute identity')
        if kind == 'StopOfRoute' and row.get('Stops') is not None and not isinstance(row['Stops'], list):
            raise ValueError('Stops missing from stop-sequence response')
        if kind == 'Schedule' and any(row.get(key) is not None and not isinstance(row[key], list) for key in ['Timetables', 'Frequencys']):
            raise ValueError('Unexpected timetable/frequency type')


def validate_installable(rows, kind):
    """Reject raw nonfinite values and normalization failures before caching."""
    validate_rows(rows, kind)
    json.dumps(rows, ensure_ascii=False, allow_nan=False).encode('utf-8')
    json.dumps([normalize(row) for row in rows], ensure_ascii=False, allow_nan=False).encode('utf-8')


class Store:
    def __init__(self, root):
        self.root = root
        self.raw = root / 'raw_data/official_cross_city/service_coverage'
        self.out = root / 'data/official_cross_city/service_coverage'
        self.manifest_path = self.raw / 'manifest.json'
        self.manifest = json.loads(self.manifest_path.read_text()) if self.manifest_path.exists() else envelope(
            'regional_bus_service_sources', {'source_updated_at': None, 'license': TERMS}, [])

    def save(self):
        write(self.manifest_path, self.manifest)

    def receipt(self, source_id):
        return next((r for r in self.manifest['records'] if r['source_id'] == source_id), None)

    def upsert(self, receipt):
        old = self.receipt(receipt['source_id'])
        if old:
            self.manifest['records'].remove(old)
        self.manifest['records'].append(receipt)
        self.save()

    def rows(self, receipt):
        path = self.root / receipt['raw_file']
        if path.stat().st_size != receipt['stored_bytes']:
            raise ValueError('Stored raw byte count mismatch')
        content = gzip.decompress(path.read_bytes())
        if len(content) != receipt['bytes']:
            raise ValueError('HTTP byte count mismatch after decompression')
        rows = json.loads(content)
        validate_rows(rows, receipt['service'])
        return rows

    def rebuild(self):
        coverage = []
        latest = {(r['scope'], r['service']): r['top'] for r in self.manifest['records'] if r.get('service')}
        selected = self.manifest['metadata'].get('active_page_sizes', {})
        groups = sorted((scope, kind, selected.get(scope + '_' + kind, top)) for (scope, kind), top in latest.items())
        for scope, kind, top in groups:
            pages = []; rows = []; skip = 0; complete = False; gaps = []
            while True:
                req = request(scope, kind, skip, top)
                receipt = self.receipt(req['source_id'])
                if not receipt:
                    gaps.append({'skip': skip, 'status': 'not_requested'}); break
                if receipt['status'] != 'downloaded':
                    gaps.append({'skip': skip, 'status': receipt['status'], 'http_status': receipt.get('http_status')}); break
                page = self.rows(receipt); pages.append(receipt); rows.extend(page)
                if len(page) < top:
                    complete = True; break
                skip += top
            reference_path = self.root / f'data/official_cross_city/regional_routes/{scope}.json'
            if scope == 'Taipei' and not reference_path.exists():
                reference_path = self.root / 'data/tdx_bus_travel_time/route_catalog.json'
            reference = json.loads(reference_path.read_text()) if reference_path.exists() else None
            route_uids = {r['RouteUID'] for r in rows}
            reference_uids = {r['route_uid'] for r in reference['records']} if reference else set()
            updates = sorted({r.get('UpdateTime') for r in rows if r.get('UpdateTime')})
            calendars = [t.get('ServiceDay') for r in rows for t in (r.get('Timetables') or []) + (r.get('Frequencys') or []) if t.get('ServiceDay') is not None]
            specials = [d for r in rows for t in (r.get('Timetables') or []) + (r.get('Frequencys') or []) for d in (t.get('SpecialDays') or [])]
            meta = {'sources': pages, 'source_updated_at': updates[0] if len(updates) == 1 else None,
                    'source_record_update_times': updates, 'statistical_period': None,
                    'grain': 'one published route/subroute/direction/operator model or operator-relation set; every nested stop/timetable/frequency/calendar retained',
                    'license': TERMS, 'scope': scope, 'service': kind,
                    'record_count': len(rows), 'page_count': len(pages),
                    'scope_status': 'complete_published_endpoint_by_exhausted_page' if complete else 'partial_contiguous_pages',
                    'empty_endpoint_response': complete and not rows,
                    'route_uid_count': len(route_uids), 'nested_stop_count': sum(len(r.get('Stops') or []) for r in rows),
                    'models_missing_stops': sum(r.get('Stops') is None for r in rows) if kind == 'StopOfRoute' else None,
                    'timetable_count': sum(len(r.get('Timetables') or []) for r in rows),
                    'frequency_count': sum(len(r.get('Frequencys') or []) for r in rows),
                    'models_without_timetables_or_frequencies': sum(not r.get('Timetables') and not r.get('Frequencys') for r in rows) if kind == 'Schedule' else None,
                    'service_day_object_count': len(calendars), 'special_day_object_count': len(specials),
                    'next_pages_or_source_gaps': gaps,
                    'route_reference': str(reference_path.relative_to(self.root)) if reference else None,
                    'route_reference_requested_date': (reference['metadata'].get('requested_date') or reference['metadata'].get('statistical_period', {}).get('requested_date')) if reference else None,
                    'reference_route_uids_without_published_service': sorted(reference_uids - route_uids),
                    'published_route_uids_absent_from_dated_reference': sorted(route_uids - reference_uids) if reference else None,
                    'units': {'stop_position': 'source longitude/latitude degrees', 'min_headway_mins': 'minutes', 'max_headway_mins': 'minutes', 'departure_time': 'source HH:mm clock'},
                    'independent_review_status': 'pending',
                    'limitations': ['完整只表示此端點已分頁取至尾頁；不表示所有路線都有班表或已實際行駛。',
                                    '服務日曆來自班表ServiceDay/SpecialDays，非獨立實際行駛觀測。',
                                    '現行端點與日期版本路線主檔可能不同版；缺UID不解讀為沒有服務。',
                                    '公路客運保留全國資料，不以名稱推斷北北基範圍。',
                                    '相同路線/附線/方向可按業者分模型；不按三碼去重，也不直接加總各業者班表。',
                                    '只核對同一TDX namespace的原始UID；不連接地方直接feed ID。']}
            output = f'data/official_cross_city/service_coverage/{scope}_{kind}.json.gz'
            write_compressed(self.root / output, envelope('regional_bus_' + scope + '_' + kind, meta, [normalize(r) for r in rows]))
            coverage.append({'scope': scope, 'service': kind, 'output_file': output,
                             **{key: meta[key] for key in ['record_count', 'page_count', 'scope_status', 'empty_endpoint_response', 'route_uid_count', 'nested_stop_count', 'timetable_count', 'frequency_count', 'service_day_object_count', 'special_day_object_count', 'next_pages_or_source_gaps', 'reference_route_uids_without_published_service']}})
        write(self.out / 'coverage.json', envelope('regional_bus_service_coverage', {
            'source_updated_at': None, 'statistical_period': None, 'license': TERMS,
            'grain': 'one scope/service endpoint acquisition', 'independent_review_status': 'pending'}, coverage))
        return coverage


def fetch(store, args):
    with urlopen(Request(SPEC_URL), timeout=120) as response:
        content = response.read(2 * 1024**2 + 1)
        if len(content) > 2 * 1024**2:
            raise ValueError('Specification exceeds local size limit')
        spec = json.loads(content); validate_spec(spec)
        path = store.raw / 'specifications/bus_v2.json'; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(content)
        store.upsert({'source_id': 'bus_v2_specification', 'status': 'downloaded', 'source_page': PAGE,
                      'download_url': SPEC_URL, 'raw_file': str(path.relative_to(store.root)),
                      'downloaded_at': now(), 'source_updated_at': None, 'bytes': len(content)})
    auth = token(); used = 0; total = 0; allowed_at = time.time()
    # Share pacing with travel-time batches, without changing their manifest.
    other = store.root / 'raw_data/tdx_bus_travel_time/manifest.json'
    past = list(store.manifest['records'])
    if other.exists():
        past += json.loads(other.read_text())['records']
    for row in past:
        for attempt in (row, row.get('last_failed_attempt', {})):
            stamp = attempt.get('downloaded_at') or attempt.get('checked_at')
            if stamp and (attempt.get('service') or attempt.get('kind') in {'travel_time', 'route_reference'}):
                allowed_at = max(allowed_at, next_request_time(attempt.get('usage_headers'), datetime.fromisoformat(stamp).timestamp(), args.min_request_interval))
    batch = {'started_at': now(), 'attempted_source_ids': [], 'successful_source_ids': [], 'new_business_request_count': 0}
    store.manifest['metadata']['last_batch'] = batch
    settings = store.manifest['metadata'].setdefault('active_page_sizes', {})
    for scope in args.scope:
        for kind in args.kind:
            settings[scope + '_' + kind] = args.stops_page_size if kind == 'StopOfRoute' else args.schedule_page_size
    store.save()
    try:
        for scope in args.scope:
            for kind in args.kind:
                skip = 0; top = args.stops_page_size if kind == 'StopOfRoute' else args.schedule_page_size
                while True:
                    req = request(scope, kind, skip, top)
                    old = store.receipt(req['source_id'])
                    if old and old['status'] == 'downloaded':
                        if len(store.rows(old)) < top: break
                        skip += top; continue
                    if used >= args.max_requests or total >= args.max_total_mb * 1024**2:
                        batch['status'] = 'bounded_limit_reached'; return
                    # Resume before starting a response which would exceed
                    # the remaining whole-response byte budget.
                    if used and int(args.max_total_mb * 1024**2) - total < int(args.max_response_mb * 1024**2):
                        batch['status'] = 'bounded_limit_reached'; return
                    delay = allowed_at - time.time()
                    while delay > 0:
                        step = min(delay, 60); time.sleep(step); delay -= step
                    limit = min(int(args.max_response_mb * 1024**2), int(args.max_total_mb * 1024**2) - total)
                    raw_path = store.raw / 'responses' / (req['source_id'] + '.json.gz'); raw_path.parent.mkdir(parents=True, exist_ok=True)
                    usage = {}; http_status = None; in_transport = True
                    try:
                        batch['attempted_source_ids'].append(req['source_id']); batch['new_business_request_count'] += 1; used += 1; store.save()
                        with urlopen(Request(req['download_url'], headers={'Authorization': 'Bearer ' + auth, 'Accept-Encoding': 'identity'}), timeout=120) as response:
                            http_status = response.status
                            usage = {k.lower(): v for k, v in response.headers.items() if k.lower().startswith(('x-ratelimit', 'x-rate-limit', 'x-quota', 'x-point')) or k.lower() == 'retry-after'}
                            allowed_at = max(allowed_at, next_request_time(usage, time.time(), args.min_request_interval))
                            if http_status != 200:
                                raise ValueError('unexpected_http_status')
                            declared = response.headers.get('Content-Length')
                            if declared and int(declared) > limit: raise ValueError('response_exceeds_local_size_limit')
                            body = response.read(limit + 1); total += len(body)
                            if not body or len(body) > limit: raise ValueError('empty_or_oversized_response')
                            if declared and len(body) != int(declared): raise ValueError('response_length_mismatch')
                            content_type = response.headers.get('Content-Type')
                        in_transport = False
                        rows = json.loads(body); validate_installable(rows, kind)
                        compressed = gzip.compress(body, mtime=0)
                        source = {**req, 'status': 'downloaded', 'downloaded_at': now(), 'source_updated_at': None,
                                  'http_status': http_status, 'usage_headers': usage, 'content_type': content_type,
                                  'raw_file': str(raw_path.relative_to(store.root)), 'storage_encoding': 'gzip', 'bytes': len(body), 'stored_bytes': len(compressed), 'record_count': len(rows)}
                        part = raw_path.with_suffix('.part')
                        try:
                            part.write_bytes(compressed); part.replace(raw_path)
                        finally:
                            part.unlink(missing_ok=True)
                        store.upsert(source); batch['successful_source_ids'].append(req['source_id'])
                        print(req['source_id'] + ': ' + str(len(rows)) + ' models', flush=True)
                        if len(rows) < top: break
                        skip += top
                    except HTTPError as exc:
                        usage = {k.lower(): v for k, v in (exc.headers or {}).items() if k.lower().startswith(('x-ratelimit', 'x-rate-limit', 'x-quota', 'x-point')) or k.lower() == 'retry-after'}
                        store.upsert({**req, 'status': 'failed', 'checked_at': now(), 'http_status': exc.code, 'usage_headers': usage, 'failure_reason': 'provider_http_error'})
                        allowed_at = max(allowed_at, next_request_time(usage, time.time(), args.min_request_interval))
                        print(req['source_id'] + ': HTTP ' + str(exc.code), flush=True)
                        if exc.code in {401, 403, 429}:
                            batch['status'] = 'stopped_provider_denial'
                            raise RuntimeError('Provider denied or rate limited request; stopped without retry') from None
                        break
                    except (ValueError, UnicodeError) as exc:
                        reason = str(exc) if str(exc) in {'response_exceeds_local_size_limit', 'empty_or_oversized_response', 'response_length_mismatch'} else 'response_contract_mismatch'
                        store.upsert({**req, 'status': 'failed', 'checked_at': now(), 'http_status': http_status, 'usage_headers': usage, 'failure_reason': reason})
                        print(req['source_id'] + ': ' + reason, flush=True)
                        allowed_at = max(allowed_at, time.time() + args.min_request_interval); break
                    except (OSError, IncompleteRead) as exc:
                        reason = 'transport_failure' if in_transport else 'local_storage_failure'
                        store.upsert({**req, 'status': 'failed', 'checked_at': now(), 'http_status': http_status, 'usage_headers': usage,
                                      'failure_reason': reason, 'error_type': type(exc).__name__})
                        batch['status'] = 'stopped_' + reason
                        raise RuntimeError('Service acquisition stopped after ' + reason + '; failed receipt retained') from None
    finally:
        if 'status' not in batch:
            has_failed_page = any(not store.receipt(source_id) or store.receipt(source_id)['status'] != 'downloaded' for source_id in batch['attempted_source_ids'])
            batch['status'] = 'partial_source_or_local_gaps' if has_failed_page else 'requested_endpoint_pages_exhausted'
        batch['finished_at'] = now(); store.save()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=ROOT)
    parser.add_argument('--scope', choices=SCOPES, action='append')
    parser.add_argument('--kind', choices=KINDS, action='append')
    parser.add_argument('--stops-page-size', type=int, default=500)
    parser.add_argument('--schedule-page-size', type=int, default=100)
    parser.add_argument('--max-requests', type=int, default=20)
    parser.add_argument('--max-response-mb', type=float, default=8)
    parser.add_argument('--max-total-mb', type=float, default=20)
    parser.add_argument('--min-request-interval', type=float, default=15)
    mode = parser.add_mutually_exclusive_group(); mode.add_argument('--fetch', action='store_true'); mode.add_argument('--offline', action='store_true')
    args = parser.parse_args(argv); args.scope = list(dict.fromkeys(args.scope or ['Keelung', 'InterCity'])); args.kind = list(dict.fromkeys(args.kind or KINDS))
    if min(args.stops_page_size, args.schedule_page_size, args.max_requests) < 1 or min(args.max_response_mb, args.max_total_mb) <= 0 or args.min_request_interval < 13:
        parser.error('Positive paging/budgets and at least 13 seconds pacing required')
    store = Store(args.root)
    if not (args.fetch or args.offline):
        print(json.dumps(envelope('regional_bus_service_request_plan', {'business_data_requested': False, 'specification_url': SPEC_URL}, [request(s, k, 0, args.stops_page_size if k == 'StopOfRoute' else args.schedule_page_size) for s in args.scope for k in args.kind]), ensure_ascii=False, indent=2)); return
    if args.fetch:
        try: fetch(store, args)
        finally: store.rebuild()
    else: store.rebuild()


if __name__ == '__main__':
    main()
