#!/usr/bin/env python3
"""Bounded TDX station-to-station travel time acquisition; default is a plan.

Only the documented current estimation endpoints are used. Their three-month
training input is not a three-year historical travel-time archive.
"""
from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta
import json
import math
from pathlib import Path
import time
from urllib.error import HTTPError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

try:
    from .update_tdx import normalize, response_records, token, write
except ImportError:
    from update_tdx import normalize, response_records, token, write

ROOT = Path(__file__).resolve().parents[1]
HOST = 'https://tdx.transportdata.tw'
SPEC_ID = 'b22c71d8-5af0-4de2-ab0a-be28983fa051'
SPEC_URL = HOST + '/webapi/File/Swagger/V3/' + SPEC_ID
SPEC_PAGE = HOST + '/api-service/swagger/premium/' + SPEC_ID
HIST_PAGE = HOST + '/api-service/swagger/historical/39472fde-f790-42b9-9c8b-88282118e6b1'
TERMS = {'name': 'TDX資料使用條款', 'url': HOST + '/term'}
CITY_PATH = '/V3/Map/Estimation/Bus/S2STravelTime/City/{City}/RouteName/{RouteName}'
INTERCITY_PATH = '/V3/Map/Estimation/Bus/S2STravelTime/InterCity/RouteName/{RouteName}'
DEFAULT_SCOPES = [
    ('NewTaipei', '918'), ('Keelung', '101'), ('InterCity', '1815'),
    ('Taipei', '306'), ('Taipei', '307西藏三民'), ('Taipei', '南京幹線'),
    ('Taipei', '信義幹線'), ('Taipei', '基隆路幹線'), ('Taipei', '670'),
    ('Taipei', '63'), ('Taipei', '276'), ('Taipei', '303'), ('Taipei', '915'),
    ('Taipei', '內湖幹線'), ('Taipei', '民生幹線'), ('Taipei', '南港幹線'),
    ('Taipei', '通勤29'), ('Taipei', '南軟通勤專車中和線'), ('Taipei', '232'),
]


def now():
    return datetime.now(ZoneInfo('Asia/Taipei')).isoformat(timespec='seconds')


def envelope(dataset_id, metadata, records):
    return {'schema_version': 1, 'dataset_id': dataset_id, 'metadata': metadata, 'records': records}


def scope_id(city, route_name):
    if city not in {'Taipei', 'NewTaipei', 'Keelung', 'InterCity'}:
        raise ValueError('Scope must be Taipei, NewTaipei, Keelung, or InterCity')
    if not route_name or route_name.strip() != route_name:
        raise ValueError('Nonempty exact route name required')
    return city + '_' + quote(route_name, safe='')


def travel_request(spec, city, name):
    path = INTERCITY_PATH if city == 'InterCity' else CITY_PATH
    operation = spec['paths'][path]['get']
    if city != 'InterCity':
        choices = next(x['schema']['enum'] for x in operation['parameters'] if x['name'] == 'City')
        if city not in choices:
            raise ValueError('City not accepted by official specification')
    path = path.replace('{City}', city).replace('{RouteName}', quote(name, safe=''))
    return {'source_id': scope_id(city, name), 'kind': 'travel_time', 'city': city,
            'route_name': name, 'source_page': SPEC_PAGE,
            'download_url': spec['servers'][0]['url'] + path + '?' + urlencode({'$format': 'JSON'})}


def route_request(day):
    return {'source_id': 'Taipei_routes_' + day, 'kind': 'route_reference', 'city': 'Taipei',
            'reference_date': day, 'source_page': HIST_PAGE,
            'download_url': HOST + '/api/historical/v2/Historical/Bus/Route/Date/' + day
                            + '/City/Taipei?' + urlencode({'$format': 'JSON', '$top': 100000})}


def validate_spec(spec):
    if spec.get('servers') != [{'url': HOST + '/api/premium'}]:
        raise ValueError('Unexpected travel-time API server')
    for path in [CITY_PATH, INTERCITY_PATH]:
        params = spec['paths'][path]['get']['parameters']
        if not any(x['name'] == 'RouteName' and x['in'] == 'path' and x.get('required') for x in params):
            raise ValueError('RouteName contract changed')
        if any(x['name'].lower() in {'date', 'dates', 'version'} for x in params):
            raise ValueError('Historical support changed; review the catalog before rebuilding')


def normalize_travel(body, request, provenance):
    if not isinstance(body, list) or not all(isinstance(x, dict) for x in body):
        raise ValueError('Expected the documented array of travel-time models')
    groups = []
    seen = set()
    duplicate_count = 0
    for source in body:
        if not isinstance(source.get('TravelTimes'), list):
            raise ValueError('TravelTimes missing or not an array')
        record = normalize(source)
        for field in ['route_uid', 'route_id', 'sub_route_uid', 'sub_route_id', 'direction',
                      'effective_date', 'expire_date', 'update_time', 'validity_description']:
            record.setdefault(field, None)
        if not record['route_uid'] or not record['sub_route_uid']:
            raise ValueError('Route/subroute identifier missing')
        for pair in record['travel_times']:
            for field in ['sequence', 'from_stop_id', 'to_stop_id', 'run_time', 'start_time', 'end_time', 'service_tag']:
                pair.setdefault(field, None)
            for field in ['from_stop_id', 'to_stop_id', 'service_tag']:
                if pair[field] is not None:
                    pair[field] = str(pair[field])
            runtime = pair['run_time']
            if runtime is not None and (isinstance(runtime, bool) or not isinstance(runtime, (float, int)) or not math.isfinite(runtime) or runtime < 0):
                raise ValueError('RunTime is not a finite nonnegative number or null')
            # IDs alone do not identify a segment: sequence, direction, service day,
            # and interval are all part of the source row's grain.
            key = tuple(record.get(k) for k in ['route_uid', 'sub_route_uid', 'direction']) + tuple(
                pair[k] for k in ['sequence', 'from_stop_id', 'to_stop_id', 'service_tag', 'start_time', 'end_time'])
            if key in seen:
                duplicate_count += 1
            seen.add(key)
        groups.append(record)
    pairs = [p for r in groups for p in r['travel_times']]
    updates = sorted({r['update_time'] for r in groups if r['update_time']})
    metadata = {
        'source_page': SPEC_PAGE, 'sources': [provenance], 'request': request,
        'license': TERMS,
        'source_updated_at': updates[0] if len(updates) == 1 else None,
        'source_record_update_times': updates, 'source_time_offset_note': 'Unqualified source timestamps are preserved verbatim; no offset inferred.',
        'statistical_period': {'effective_dates': sorted({r['effective_date'] for r in groups if r['effective_date']}),
                               'expire_dates': sorted({r['expire_date'] for r in groups if r['expire_date']}),
                               'historical_input_period': '近3個月；精確起訖未由來源給定'},
        'grain': 'one route/subroute/direction model with nested stop-sequence/service-tag/time-interval rows',
        'units': {'run_time': 'seconds', 'update_interval': 'months', 'start_time': 'source local civil clock HH:mm', 'end_time': 'source local civil clock HH:mm; 24:00 retained'},
        'service_tag_dictionary': {'0': 'weekend', '1': 'weekdays'},
        'record_count': len(groups), 'travel_time_row_count': len(pairs),
        'missing_run_time_count': sum(p['run_time'] is None for p in pairs),
        'missing_from_stop_id_count': sum(p['from_stop_id'] is None for p in pairs),
        'missing_to_stop_id_count': sum(p['to_stop_id'] is None for p in pairs),
        'duplicate_grain_count': duplicate_count,
        'scope_status': 'complete_unpaginated_requested_endpoint_response' if groups else 'empty_requested_endpoint_response',
        'citywide_coverage': 'bounded_selected_route_requests_only',
        'cross_source_id_join_status': 'not_joined; TDX source identifiers retained without mapping to Taipei/NTPC feeds',
        'independent_review_status': 'pending',
        'independent_review_report': 'data/quality/tdx_bus_travel_time_review.json',
        'limitations': ['歷史推估，不是當日實際旅行時間；有效期與各子路線說明原樣保留。',
                       '官方端點無歷史日期/版本參數；不能回填近三年每月版本。',
                       '完整只指該次未分頁端點回應，不代表全市、所有時段或每一站間均有值。',
                       'RouteName查詢如回傳其他名稱/子路線，全部保留，不用名稱推斷跨市範圍。',
                       '缺少起站ID或RunTime的首站/其他來源列保留null，不推算成0秒。'],
    }
    return envelope('tdx_bus_s2s_' + request['source_id'], metadata, groups)


class Store:
    def __init__(self, root):
        self.root = root
        self.raw = root / 'raw_data/tdx_bus_travel_time'
        self.out = root / 'data/tdx_bus_travel_time'
        self.path = self.raw / 'manifest.json'
        self.manifest = json.loads(self.path.read_text()) if self.path.exists() else envelope(
            'tdx_bus_travel_time_manifest', {'grain': 'one acquisition request/source receipt', 'source_updated_at': None, 'license': TERMS}, [])

    def save(self):
        write(self.path, self.manifest)

    def upsert(self, record):
        rows = self.manifest['records']
        for i, old in enumerate(rows):
            if old['source_id'] == record['source_id']:
                # A failed explicit refresh must not erase the last usable
                # response or cause offline rebuild to drop its provenance.
                if old.get('status') == 'downloaded' and record.get('status') == 'failed':
                    old['last_failed_attempt'] = record
                else:
                    rows[i] = record
                break
        else:
            rows.append(record)
        self.save()

    def successful(self, source_id):
        return next((x for x in self.manifest['records'] if x['source_id'] == source_id and x.get('status') == 'downloaded'), None)

    def raw_body(self, source):
        path = self.root / source['raw_file']
        if not path.is_file() or path.stat().st_size != source['bytes']:
            raise ValueError('Raw file missing or size differs from saved receipt: ' + source['source_id'])
        return json.loads(path.read_bytes())

    def import_existing(self):
        # Reuse only a known complete unpaginated 307 response, retaining its own
        # original retrieval time. Never relabel it as a fresh download.
        if self.successful('Taipei_307'):
            return
        path = self.root / 'data/tdx/observations/Taipei_S2STravelTime_307.json'
        if not path.exists():
            return
        old = json.loads(path.read_text())
        source = old['metadata']['sources'][0]
        request = travel_request(json.loads((self.raw / 'specifications/travel_time.json').read_text()), 'Taipei', '307')
        candidate = {**source, **request, 'status': 'downloaded', 'reused_existing_response': True}
        normalize_travel(self.raw_body(candidate), request, source)
        self.upsert(candidate)

    def rebuild(self, as_of):
        successful = [x for x in self.manifest['records'] if x.get('status') == 'downloaded']
        references = [x for x in successful if x.get('kind') == 'route_reference']
        if references:
            route_source = max(references, key=lambda x: x['reference_date'])
            body = self.raw_body(route_source)
            routes, wrapper = response_records(body)
            if not routes or wrapper.get('Count') != len(routes):
                raise ValueError('Full route reference Count does not match rows')
            route_rows = [normalize(x) for x in routes]
            route_period = {'requested_date': route_source['reference_date'], 'version_start': wrapper.get('StartDate'), 'version_end': wrapper.get('EndDate')}
        else:
            path = self.root / 'data/tdx/observations/Taipei_2026-10-20_Route_top_100000.json'
            if not path.exists():
                raise ValueError('No route reference; fetch a reference first')
            old = json.loads(path.read_text())
            if old['metadata'].get('scope_status') != 'complete_requested_date_by_response_count':
                raise ValueError('Fallback route reference has not passed full Count validation')
            route_rows = old['records']; route_source = old['metadata']['sources'][0]
            route_period = old['metadata']['statistical_period']
        retained = {}
        coverage = []
        for source in self.manifest['records']:
            if source.get('kind') != 'travel_time':
                continue
            record = {k: source.get(k) for k in ['source_id', 'city', 'route_name', 'download_url', 'status', 'http_status', 'failure_reason', 'downloaded_at', 'last_failed_attempt']}
            if source.get('status') == 'downloaded':
                dataset = normalize_travel(self.raw_body(source), {k: source[k] for k in ['source_id', 'kind', 'city', 'route_name', 'source_page', 'download_url']}, source)
                filename = 'data/tdx_bus_travel_time/observations/' + source['source_id'] + '.json'
                write(self.root / filename, dataset)
                m = dataset['metadata']
                record.update({k: m[k] for k in ['record_count', 'travel_time_row_count', 'missing_run_time_count', 'statistical_period', 'scope_status']})
                record['output_file'] = filename
                for group in dataset['records']:
                    if group.get('route_uid'):
                        retained.setdefault(group['route_uid'], []).append(filename)
            coverage.append(record)
        catalog = []
        for route in route_rows:
            name = route.get('route_name', {}).get('zh_tw')
            if not isinstance(name, str) or not route.get('route_uid'):
                raise ValueError('Route reference lacks route name or UID')
            request = travel_request(json.loads((self.raw / 'specifications/travel_time.json').read_text()), 'Taipei', name)
            catalog.append({'city': 'Taipei', 'route_uid': route['route_uid'], 'route_id': route['route_id'], 'route_name': name,
                            'sub_routes': [{k: s.get(k) for k in ['sub_route_uid', 'sub_route_id', 'sub_route_name', 'direction']} for s in route.get('sub_routes', [])],
                            'download_url': request['download_url'], 'download_unit': 'one RouteName, no pagination/date/version parameters',
                            'retained_files': sorted(set(retained.get(route['route_uid'], []))),
                            'coverage_status': 'downloaded_route_uid_present' if route['route_uid'] in retained else 'reference_only_not_downloaded',
                            'historical_retrieval_status': 'no_historical_date_or_version_parameter_in_official_spec'})
        start = as_of.replace(year=as_of.year - 3) if not (as_of.month == 2 and as_of.day == 29) else as_of.replace(year=as_of.year - 3, day=28)
        write(self.out / 'route_catalog.json', envelope('tdx_bus_travel_time_route_catalog', {
            'source_page': SPEC_PAGE, 'sources': [route_source], 'source_updated_at': None,
            'license': TERMS,
            'statistical_period': route_period, 'grain': 'one Taipei TDX route in the dated full route reference',
            'route_reference_count': len(catalog), 'requested_historical_window': {'start': start.isoformat(), 'end': (as_of - timedelta(days=1)).isoformat()},
            'historical_window_status': 'not_available_from_this_current_estimation_endpoint',
            'limitations': ['此為完整的日期版本路線取用目錄；不代表全部路線都有站間旅行時間。',
                           '近三年歷史推估版本未由現行官方API提供日期參數；不將當期估計複製為歷史。',
                           '跨市線保留全段；不以名稱、ID前綴或站牌位置裁切。'],
        }, catalog))
        write(self.out / 'coverage.json', envelope('tdx_bus_travel_time_coverage', {
            'source_updated_at': None, 'statistical_period': None, 'grain': 'one attempted/reused selected route endpoint',
            'license': TERMS,
            'scope': 'bounded selected Taipei, NewTaipei, Keelung and InterCity requests; not a regional census',
            'route_reference_count': len(catalog), 'taipei_route_uids_retained': sum(bool(r['retained_files']) for r in catalog),
            'request_count': len(coverage), 'successful_response_count': sum(r['status'] == 'downloaded' for r in coverage),
            'nonempty_response_count': sum(r.get('record_count', 0) > 0 for r in coverage),
            'failed_scope_count': sum(r['status'] == 'failed' for r in coverage),
            'travel_time_row_count': sum(r.get('travel_time_row_count', 0) for r in coverage),
            'missing_run_time_count': sum(r.get('missing_run_time_count', 0) for r in coverage),
            'independent_review_status': 'pending',
            'independent_review_report': 'data/quality/tdx_bus_travel_time_review.json',
        }, coverage))
        batch = self.manifest['metadata'].get('last_batch', {})
        planned = batch.get('planned_requests') or [route_request((as_of - timedelta(days=1)).isoformat())] + [
            travel_request(json.loads((self.raw / 'specifications/travel_time.json').read_text()), *scope) for scope in DEFAULT_SCOPES]
        attempted = {r['source_id']: r for r in self.manifest['records'] if r.get('kind') in {'travel_time', 'route_reference'}}
        delivery_rows = []
        for request in list({r['source_id']: r for r in planned + [
                {k: r[k] for k in ['source_id', 'kind', 'city', 'route_name', 'download_url', 'source_page']}
                for r in attempted.values() if r.get('kind') == 'travel_time']}.values()):
            source = attempted.get(request['source_id'])
            delivery_rows.append({**request,
                'acquisition_status': source['status'] if source else 'not_requested_in_this_bounded_stage',
                'downloaded_at': source.get('downloaded_at') if source else None,
                'raw_file': source.get('raw_file') if source else None,
                'reused_existing_response': source.get('reused_existing_response', False) if source else False,
                'http_status': source.get('http_status') if source else None})
        pending = [r['source_id'] for r in delivery_rows if r['acquisition_status'] == 'not_requested_in_this_bounded_stage']
        write(self.out / 'delivery.json', envelope('tdx_bus_travel_time_delivery', {
            'license': TERMS, 'source_updated_at': None, 'statistical_period': None,
            'grain': 'one planned or reused route/reference endpoint scope',
            'stage_status': 'partial_bounded_acquisition' if pending else 'completed_bounded_acquisition',
            'citywide_status': 'partial_route_selection_not_citywide_travel_time_coverage',
            'acquisition_stop_reason': batch.get('stop_reason'),
            'new_business_request_count': batch.get('new_business_request_count'),
            'reused_response_count': sum(r['reused_existing_response'] for r in delivery_rows),
            'not_requested_scope_ids': pending,
            'not_requested_is_failure': False,
            'route_catalog': 'data/tdx_bus_travel_time/route_catalog.json',
            'coverage': 'data/tdx_bus_travel_time/coverage.json',
            'historical_archive_status': 'not_exposed_by_documented_current_estimation_endpoint',
        }, delivery_rows))
        return coverage


def fetch_requests(store, requests, args):
    auth = token()
    requests_used = 0
    total = 0
    last_at = time.monotonic() - args.min_request_interval
    for request in requests:
        if store.successful(request['source_id']) and not args.refresh:
            continue
        if requests_used >= args.max_requests:
            break
        limit = min(int(args.max_response_mb * 1024**2), int(args.max_total_mb * 1024**2) - total)
        if limit <= 0:
            break
        delay = args.min_request_interval - (time.monotonic() - last_at)
        if delay > 0:
            time.sleep(delay)
        last_at = time.monotonic()
        requests_used += 1
        batch = store.manifest['metadata'].get('last_batch')
        if batch is not None:
            batch['new_business_request_count'] = requests_used
            batch['last_request_started_at'] = now()
            store.save()
        path = store.raw / 'responses' / (request['source_id'] + '.json')
        path.parent.mkdir(parents=True, exist_ok=True)
        part = path.with_suffix('.part')
        try:
            req = Request(request['download_url'], headers={'Authorization': 'Bearer ' + auth, 'Accept-Encoding': 'identity'})
            with urlopen(req, timeout=120) as response:
                if response.status != 200:
                    raise ValueError('Unexpected HTTP status ' + str(response.status))
                declared_header = response.headers.get('Content-Length')
                declared_length = int(declared_header) if declared_header is not None else None
                if declared_length is not None and declared_length > limit:
                    raise ValueError('response_exceeds_local_size_limit')
                size = 0
                with part.open('wb') as output:
                    while chunk := response.read(min(65536, max(1, limit - size + 1))):
                        size += len(chunk); total += len(chunk)
                        if size > limit:
                            raise ValueError('response_exceeds_local_size_limit')
                        output.write(chunk)
                # A truncated payload can still be valid JSON (e.g. a shorter
                # array). Reject it before parsing or replacing a valid cache.
                if declared_length is not None and size != declared_length:
                    raise ValueError('response_length_mismatch')
                content_type = response.headers.get('Content-Type')
                usage = {k.lower(): v for k, v in response.headers.items() if k.lower().startswith(('x-ratelimit', 'x-rate-limit', 'x-quota', 'x-point'))}
            body = json.loads(part.read_bytes())
            source = {**request, 'status': 'downloaded', 'raw_file': str(path.relative_to(store.root)),
                      'downloaded_at': now(), 'source_updated_at': None, 'bytes': size, 'http_status': 200,
                      'content_type': content_type, 'usage_headers': usage}
            if request['kind'] == 'travel_time':
                normalize_travel(body, request, source)
            else:
                rows, wrapper = response_records(body)
                if not rows or wrapper.get('Count') != len(rows):
                    raise ValueError('route_reference_count_mismatch')
            part.replace(path)
            store.upsert(source)
            print(request['source_id'] + ': saved ' + str(size) + ' bytes', flush=True)
        except HTTPError as exc:
            usage = {k.lower(): v for k, v in (exc.headers or {}).items()
                     if k.lower() == 'retry-after' or k.lower().startswith(('x-ratelimit', 'x-rate-limit', 'x-quota', 'x-point'))}
            store.upsert({**request, 'status': 'failed', 'checked_at': now(), 'http_status': exc.code,
                          'failure_reason': 'provider_http_error', 'usage_headers': usage})
            print(request['source_id'] + ': HTTP ' + str(exc.code), flush=True)
            if exc.code in {401, 403, 429}:
                raise RuntimeError('TDX denied/limited the request; no retries or account changes performed') from None
        except (ValueError, UnicodeError) as exc:
            reason = str(exc) if str(exc) in {'response_exceeds_local_size_limit', 'response_length_mismatch'} else 'unusable_response_format_or_contract'
            store.upsert({**request, 'status': 'failed', 'checked_at': now(), 'failure_reason': reason, 'partial_file_retained': False})
            print(request['source_id'] + ': ' + reason, flush=True)
        finally:
            part.unlink(missing_ok=True)
    return {'requests_used': requests_used, 'response_bytes': total}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=ROOT)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--fetch', action='store_true')
    mode.add_argument('--offline', action='store_true')
    parser.add_argument('--route', action='append', help='exact City:RouteName; accepted City Taipei/NewTaipei/Keelung/InterCity')
    parser.add_argument('--all-taipei', action='store_true', help='resume all Taipei route names from the dated route reference; bounded per invocation')
    parser.add_argument('--as-of', type=date.fromisoformat, default=datetime.now(ZoneInfo('Asia/Taipei')).date())
    parser.add_argument('--max-requests', type=int, default=20)
    parser.add_argument('--max-response-mb', type=float, default=8)
    parser.add_argument('--max-total-mb', type=float, default=20)
    parser.add_argument('--min-request-interval', type=float, default=26)
    parser.add_argument('--refresh', action='store_true', help='explicitly re-fetch cached successful scopes')
    args = parser.parse_args(argv)
    if args.max_requests < 1 or min(args.max_response_mb, args.max_total_mb) <= 0 or args.min_request_interval < 13:
        parser.error('Positive request/byte limits and at least 13 seconds between requests required')
    store = Store(args.root)
    if args.fetch:
        # Public specification is unmetered and separate from the bounded
        # business API batch. Validate before replacing the cached contract.
        with urlopen(Request(SPEC_URL), timeout=120) as response:
            raw_spec = response.read(2 * 1024**2 + 1)
            if len(raw_spec) > 2 * 1024**2:
                raise ValueError('Official specification exceeds 2 MB safety limit')
            spec = json.loads(raw_spec)
            validate_spec(spec)
            downloaded_at = now()
            store.raw.joinpath('specifications').mkdir(parents=True, exist_ok=True)
            (store.raw / 'specifications/travel_time.json').write_bytes(raw_spec)
            store.upsert({'source_id': 'travel_time_specification', 'kind': 'official_specification',
                          'status': 'downloaded', 'source_page': SPEC_PAGE, 'download_url': SPEC_URL,
                          'raw_file': 'raw_data/tdx_bus_travel_time/specifications/travel_time.json',
                          'downloaded_at': downloaded_at, 'source_updated_at': None,
                          'bytes': len(raw_spec), 'http_status': response.status,
                          'content_type': response.headers.get('Content-Type')})
    spec_path = store.raw / 'specifications/travel_time.json'
    if not spec_path.exists():
        spec_path = args.root / 'raw_data/tdx/specifications/travel_time.json'
    spec = json.loads(spec_path.read_text())
    validate_spec(spec)
    scopes = [tuple(value.split(':', 1)) for value in args.route] if args.route else list(DEFAULT_SCOPES)
    if any(len(scope) != 2 for scope in scopes):
        parser.error('--route must contain City:RouteName')
    if args.all_taipei:
        catalog_path = store.out / 'route_catalog.json'
        if not catalog_path.exists():
            parser.error('--all-taipei requires a prior offline/fetch catalog build')
        scopes = [('Taipei', x['route_name']) for x in json.loads(catalog_path.read_text())['records']]
    requests = [route_request((args.as_of - timedelta(days=1)).isoformat())] + [travel_request(spec, *scope) for scope in scopes]
    # Exact endpoint scopes are deduplicated without joining IDs from other feeds.
    requests = list({x['source_id']: x for x in requests}.values())
    if not args.fetch and not args.offline:
        pending = [x for x in requests if args.refresh or not store.successful(x['source_id'])]
        print(json.dumps(envelope('tdx_bus_travel_time_request_plan', {
            'default_mode': 'plan_only_no_network_or_writes', 'max_requests': args.max_requests,
            'max_response_bytes': int(args.max_response_mb * 1024**2), 'max_total_bytes': int(args.max_total_mb * 1024**2),
            'min_request_interval_seconds': args.min_request_interval,
            'pending_scope_count': len(pending), 'planned_this_batch_count': min(len(pending), args.max_requests),
            'metering': 'official spec: 10 requests/point; 1 MB/10 points; account balance not verified',
        }, pending[:args.max_requests]), ensure_ascii=False, indent=2))
        return
    if not (store.raw / 'specifications/travel_time.json').exists():
        write(store.raw / 'specifications/travel_time.json', spec)
    store.import_existing()
    if args.fetch:
        store.manifest['metadata']['last_batch'] = {'started_at': now(), 'planned_requests': requests,
            'status': 'started', 'stop_reason': None}
        store.save()
        try:
            stats = fetch_requests(store, requests, args)
        except (KeyboardInterrupt, RuntimeError) as exc:
            batch = store.manifest['metadata']['last_batch']
            batch.update({'status': 'partial_stopped', 'finished_at': now(),
                          'stop_reason': 'local_interrupt' if isinstance(exc, KeyboardInterrupt) else 'provider_denied_or_limited_request'})
            store.save()
            store.rebuild(args.as_of)
            raise
        store.manifest['metadata']['last_batch'].update({'status': 'bounded_batch_finished', 'finished_at': now(),
                                                        'new_business_request_count': stats['requests_used']})
        store.save()
        print(json.dumps(stats))
    coverage = store.rebuild(args.as_of)
    print('Rebuilt ' + str(len(coverage)) + ' selected scopes and the full dated Taipei route catalog.')


if __name__ == '__main__':
    main()
