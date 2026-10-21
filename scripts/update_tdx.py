#!/usr/bin/env python3
"""Download official TDX catalog/specifications; collect explicitly bounded API scopes.

Catalogs are NOT bus observations. The default never calls a metered data API.
Credentials are read only from TDX_CLIENT_ID and TDX_CLIENT_SECRET, never saved.
"""
from __future__ import annotations
import argparse
from functools import lru_cache
import json
import math
import os
from pathlib import Path
import re
import subprocess
import time
from datetime import date, datetime, timedelta
from urllib.parse import urlencode, quote
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / 'raw_data/tdx'
OUT = ROOT / 'data/tdx'
HOST = 'https://tdx.transportdata.tw'
TOKEN_URL = HOST + '/auth/realms/TDXConnect/protocol/openid-connect/token'
SPECS = {
    'historical': ('39472fde-f790-42b9-9c8b-88282118e6b1', 'historical'),
    'travel_time': ('b22c71d8-5af0-4de2-ab0a-be28983fa051', 'premium'),
    'stop_distance': ('e2437b20-8f4a-4e71-875d-53466e94dae8', 'premium'),
    'bus_uncovered': ('366e56ed-55c8-4297-8ba6-4b7acb7e1d32', 'premium'),
    'transit_inaccessible': ('3b31f053-e920-4dc7-80f2-fefc4650a2d9', 'premium'),
    'bus_v3': ('939d7b26-14d6-40bc-bdc9-a076e3f8d4dc', 'basic'),
}
FAMILIES = {
    'historical': ('公車歷史 A1/A2 與歷史路網班表', ['historical']),
    'travel_time_distance': ('歷史推估站間時間、站站班表與站間距離', ['travel_time', 'stop_distance']),
    'accessibility': ('服務未涵蓋與不可及指標', ['bus_uncovered', 'transit_inaccessible']),
    'drts': ('DRTS 路線與預約規則', ['bus_v3']),
}

def now():
    return datetime.now(ZoneInfo('Asia/Taipei')).isoformat(timespec='seconds')

def write(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    part = path.with_suffix(path.suffix + '.part')
    with part.open('w', encoding='utf-8') as f:
        json.dump(obj, f, ensure_ascii=False, allow_nan=False, indent=2)
        f.write('\n')
    part.replace(path)

def snake(s):
    s = re.sub(r'([A-Z]+)([A-Z][a-z])', r'\1_\2', s)
    return re.sub(r'([a-z0-9])([A-Z])', r'\1_\2', s).replace('-', '_').lower()

def normalize(value, key=''):
    if isinstance(value, dict):
        keys = [snake(k) for k in value]
        if len(keys) != len(set(keys)): raise ValueError('Field normalization collision')
        return {snake(k): normalize(v, k) for k, v in value.items() if k not in {'DataHash', 'GeoHash'}}
    if isinstance(value, list): return [normalize(v, key) for v in value]
    if value is None: return None
    if isinstance(value, str) and not value.strip(): return None
    # Keep state-code sentinels, such as -1, unchanged. Never map missing to zero.
    if key.endswith(('ID', 'UID', 'Code')): return str(value)
    if key == 'Distance' and isinstance(value, str):
        text = value.strip()
        if text in {'-', '--', 'N/A'}: return None
        number = float(text)
        if not math.isfinite(number): raise ValueError('Nonfinite distance')
        return int(number) if number.is_integer() else number
    return value

def validate_public_data(key, data):
    if key in SPECS:
        if not isinstance(data, dict) or not data.get('paths') or not data.get('servers') or not data.get('components', {}).get('schemas'):
            raise ValueError('Unexpected OpenAPI document')
    elif key == 'historical_supply':
        if not isinstance(data, dict) or not isinstance(data.get('list'), list) or not data['list']:
            raise ValueError('Unexpected historical supply catalog')
        if not all(isinstance(row.get('name'), str) and row.get('pK_ServiceDetail') for row in data['list']):
            raise ValueError('Historical supply catalog missing service identifiers')


class Store:
    def __init__(self, offline=False):
        self.offline = offline
        self.path = RAW / 'manifest.json'
        self.manifest = json.loads(self.path.read_text()) if self.path.exists() else {'schema_version': 1, 'sources': {}}

    def save(self): write(self.path, self.manifest)

    def public(self, key, url, relative, source_page):
        path = RAW / relative
        old = self.manifest['sources'].get(key)
        if self.offline:
            if not old or not path.is_file() or path.stat().st_size == 0:
                raise ValueError(f'Missing or empty raw source: {relative}')
            data = json.loads(path.read_bytes())
            validate_public_data(key, data)
            return data
        path.parent.mkdir(parents=True, exist_ok=True)
        part = path.with_suffix(path.suffix + '.part')
        head = path.with_suffix(path.suffix + '.headers')
        cmd = ['curl', '--fail', '--location', '--silent', '--show-error', '--proto', '=https',
               '--proto-redir', '=https', '--connect-timeout', '20', '--max-time', '120',
               '--retry', '2', '--dump-header', str(head), '--output', str(part), url]
        try:
            subprocess.run(cmd, check=True, capture_output=True)
            data = json.loads(part.read_bytes())
            validate_public_data(key, data)
            headers = {}
            for line in head.read_text().splitlines():
                if line.startswith('HTTP/'): headers = {}
                elif ':' in line:
                    k, v = line.split(':', 1); headers[k.lower()] = v.strip()
            part.replace(path)
            self.manifest['sources'][key] = {
                'source_page': source_page, 'download_url': url, 'raw_file': str(path.relative_to(ROOT)),
                'downloaded_at': now(), 'source_updated_at': None,
                'bytes': path.stat().st_size, 'http_last_modified': headers.get('last-modified'),
                'content_type': headers.get('content-type'),
                'evidence_type': 'official_specification' if key in SPECS else 'official_supply_catalog',
            }
            self.save()
            return data
        finally:
            part.unlink(missing_ok=True); head.unlink(missing_ok=True)


def reference(store, key):
    return {'source_id': key, **store.manifest['sources'][key]}


def billing_policy(spec):
    text = spec['info'].get('description', '')
    calls = re.search(r'計次[：:]([0-9,]+)次/([0-9,]+)點', text)
    volume = re.search(r'計量[：:]([0-9,]+)MB/([0-9,]+)點', text)
    return {'request_base_calls': int(calls[1].replace(',', '')) if calls else None,
            'request_points': int(calls[2].replace(',', '')) if calls else None,
            'volume_base_mb': int(volume[1].replace(',', '')) if volume else None,
            'volume_points': int(volume[2].replace(',', '')) if volume else None,
            'verified_from': 'official_openapi_info_description',
            'account_balance_verified': False}

def relevant(name, path):
    if name == 'bus_v3': return '/DRTS/' in path
    if name == 'historical': return '/City/{City}' in path
    return '/City/{City}' in path


def catalog(store):
    schemas = {}
    endpoints = []
    fields = []
    for name, (uid, category) in SPECS.items():
        spec = store.public(name, HOST + '/webapi/File/Swagger/V3/' + uid,
                            'specifications/' + name + '.json', HOST + '/api-service/swagger/' + category + '/' + uid)
        schemas[name] = spec
        for path, ops in spec['paths'].items():
            if not relevant(name, path): continue
            for method, op in ops.items():
                if method != 'get': continue
                params = []
                for p in op.get('parameters', []):
                    params.append({'name': p['name'], 'location': p['in'], 'required': p.get('required', False),
                        'description': p.get('description'), 'value_type': p.get('schema', {}).get('type'),
                        'allowed_values': p.get('schema', {}).get('enum'), 'default': p.get('schema', {}).get('default')})
                endpoints.append({'specification_id': name, 'service_category': category,
                    'method': method.upper(), 'base_url': spec['servers'][0]['url'], 'path_template': path,
                    'summary': op.get('summary'), 'operation_id': op.get('operationId'),
                    'billing_policy': billing_policy(spec), 'parameters': params, 'response_formats': list(op.get('responses', {}).get('200', {}).get('content', {})),
                    'requires_member_credentials_for_script': True,
                    'scope_verified': 'specification_only_not_live_business_data',
                    'source_id': name})
        # Reach only response models used by the selected endpoints, avoiding unrelated schema baggage.
        roots = []
        for path, ops in spec['paths'].items():
            if relevant(name, path): roots.append(ops)
        seen = set()
        def walk(obj):
            if isinstance(obj, dict):
                ref = obj.get('$ref')
                if ref and ref.startswith('#/components/schemas/'):
                    model = ref.split('/')[-1]
                    if model not in seen:
                        seen.add(model)
                        target = spec['components']['schemas'].get(model)
                        if target is not None: walk(target)
                for v in obj.values(): walk(v)
            elif isinstance(obj, list):
                for v in obj: walk(v)
        for root in roots: walk(root)
        for model in sorted(seen):
            schema = spec['components']['schemas'].get(model, {})
            for field_name, f in schema.get('properties', {}).items():
                if field_name in {'DataHash', 'GeoHash'}: continue
                fields.append({'specification_id': name, 'model': model, 'source_field': field_name,
                    'normalized_field': snake(field_name), 'source_type': f.get('type'), 'format': f.get('format'),
                    'nullable': f.get('nullable', False), 'description': f.get('description'),
                    'reference': f.get('$ref'), 'items_reference': f.get('items', {}).get('$ref')})
    supply = store.public('historical_supply', HOST + '/webapi/service?category=historical',
                         'historical_supply.json', HOST + '/data-service/historical')
    rows = []
    for x in supply['list']:
        if x.get('domainL2Name') != '市區公車' or not any(c in x['name'] for c in ['臺北', '新北']): continue
        item = normalize(x)
        item['city'] = 'Taipei' if '臺北' in x['name'] else 'NewTaipei'
        item['data_start_time'] = x.get('dataStartTime')
        item['data_end_time'] = x.get('dataEndTime')
        item['time_zone'] = 'Asia/Taipei (official civil-date catalog; no source offset supplied)'
        rows.append(item)
    meta = {'evidence_type': 'official_specification_only', 'business_data_downloaded': False,
            'source_updated_at': None, 'statistical_period': None, 'geographic_scope': ['Taipei', 'NewTaipei'],
            'sources': [reference(store, key) for key in SPECS],
            'limitations': ['參數 enum 代表規格接受值，不代表該縣市確有資料。',
                           '官方歷史服務簡介的 2021 年是整體平台起期；雙北各資料起期以供應目錄逐項為準。',
                           '呼叫次數與傳輸量都可能扣點；本程式不訂購方案、不增加額度。']}
    write(OUT / 'api_catalog.json', {'schema_version': 1, 'dataset_id': 'tdx_api_catalog', 'metadata': {**meta, 'grain': 'one API operation'}, 'records': endpoints})
    write(OUT / 'field_dictionary.json', {'schema_version': 1, 'dataset_id': 'tdx_field_dictionary', 'metadata': {**meta, 'grain': 'one field per official response model'}, 'records': fields})
    write(OUT / 'historical_supply.json', {'schema_version': 1, 'dataset_id': 'tdx_historical_supply', 'metadata': {
        'evidence_type': 'official_supply_catalog_only', 'business_data_downloaded': False,
        'sources': [reference(store, 'historical_supply')], 'grain': 'one city/service/catalog classification',
        'source_updated_at': None, 'statistical_period': None,
        'limitations': ['供應起訖時間不是逐日完整性驗證，也不證明所需會員額度已具備。']}, 'records': rows})
    return schemas


def make_plan(args, specs):
    plan = []
    def add(spec_id, path, params, values, family, label):
        op = specs[spec_id]['paths'][path]['get']
        for p in op.get('parameters', []):
            if p['in'] == 'path':
                value = values[p['name']]
                choices = p.get('schema', {}).get('enum')
                if choices and value not in choices: raise ValueError(f'{value} not accepted for {p["name"]}')
                path = path.replace('{' + p['name'] + '}', quote(value, safe=''))
        url = specs[spec_id]['servers'][0]['url'] + path + '?' + urlencode(params)
        plan.append({'family': family, 'specification_id': spec_id, 'request_url': url,
            'path_values': values, 'query_parameters': params, 'label': label,
            'sample_only': bool(args.sample_top and family == 'historical' and not args.meta_only),
            'documented_top_default': next((p.get('schema', {}).get('default') for p in op.get('parameters', []) if p['name'] == '$top'), None),
            'request_limit': args.max_requests, 'max_response_bytes': int(args.max_response_mb * 1024**2)})
    for city in args.city:
        if 'historical' in args.family:
            if not args.date: raise ValueError('Historical fetch requires at least one explicit --date')
            for day in args.date:
                parsed = date.fromisoformat(day)
                if parsed >= datetime.now(ZoneInfo('Asia/Taipei')).date(): raise ValueError('Historical date must precede today')
                for kind in ['RealTimeByFrequency', 'RealTimeNearStop']:
                    if args.historical_kind and kind not in args.historical_kind: continue
                    path = '/v2/Historical/Bus/' + kind + '/City/{City}'
                    params = {'Dates': day, '$format': 'JSONL'}
                    if args.sample_top: params['$top'] = args.sample_top
                    elif args.full_top: params['$top'] = args.full_top
                    if args.meta_only: params['Meta'] = 'true'
                    add('historical', path, params, {'City': city}, 'historical', f'{city}_{day}_{kind}')
                for kind in ['Route', 'Stop', 'Station', 'StopOfRoute', 'Shape', 'Schedule']:
                    if args.meta_only or (args.historical_kind and kind not in args.historical_kind): continue
                    path = '/v2/Historical/Bus/' + kind + '/Date/{Date}/City/{City}'
                    params = {'$format': 'JSON'}
                    if args.sample_top: params['$top'] = args.sample_top
                    elif args.full_top: params['$top'] = args.full_top
                    add('historical', path, params, {'City': city, 'Date': day}, 'historical', f'{city}_{day}_{kind}')
        if 'drts' in args.family:
            for kind in ['Route', 'BookingRule']:
                add('bus_v3', '/v3/Bus/DRTS/' + kind + '/City/{City}', {'$format': 'JSON', '$top': args.page_size, '$skip': 0, '$count': 'true'},
                    {'City': city}, 'drts', f'{city}_DRTS_{kind}')
        if 'travel_time_distance' in args.family:
            if not args.route_name and not args.route_id: raise ValueError('Travel/distance require --route-name and/or --route-id')
            for route in args.route_name:
                for kind in ['Schedule', 'S2STravelTime']:
                    if args.travel_kind and kind not in args.travel_kind: continue
                    path = '/V3/Map/Estimation/Bus/' + kind + '/City/{City}/RouteName/{RouteName}'
                    add('travel_time', path, {'$format': 'JSON'}, {'City': city, 'RouteName': route},
                        'travel_time_distance', f'{city}_{kind}_{route}')
            for route in args.route_id:
                if args.travel_kind and 'Distance' not in args.travel_kind: continue
                add('stop_distance', '/S2SDistance/City/{City}/RouteID/{RouteID}', {'$format': 'JSON'},
                    {'City': city, 'RouteID': route}, 'travel_time_distance', f'{city}_distance_{route}')
        if 'accessibility' in args.family:
            if not args.town_code: raise ValueError('Accessibility requires explicit --town-code')
            for town in args.town_code:
                for spec_id in ['bus_uncovered', 'transit_inaccessible']:
                    path = next(iter(specs[spec_id]['paths']))
                    add(spec_id, path, {'$format': 'GEOJSON'}, {'City': city, 'TownCode': town},
                        'accessibility', f'{city}_{town}_{spec_id}')
    return plan


def response_records(body):
    if isinstance(body, list): return body, {}
    if isinstance(body, dict):
        if isinstance(body.get('Items'), list):
            return body['Items'], {k:v for k,v in body.items() if k != 'Items' and not (k.startswith('Bus') and isinstance(v, list))}
        if 'Items' in body and body['Items'] is None:
            if body.get('Count') == 0: return [], {k:v for k,v in body.items() if k != 'Items'}
            raise ValueError('Null Items without a confirmed zero count')
        actual_wrappers = [k for k in ['Routes', 'BookingRules'] if isinstance(body.get(k), list)]
        if len(actual_wrappers) == 1 and 'AuthorityCode' in body:
            field = actual_wrappers[0]
            return body[field], {k:v for k,v in body.items() if k != field}
        aliases = [k for k,v in body.items() if k.startswith('Bus') and isinstance(v, list)]
        if len(aliases) == 1 and any(k in body for k in ['VersionID', 'StartDate']):
            field = aliases[0]
            return body[field], {k:v for k,v in body.items() if k != field}
        if body.get('type') == 'FeatureCollection': return body['features'], {k:v for k,v in body.items() if k != 'features'}
        if any(k in body for k in ['Message', 'message', 'error', 'ErrorCode']): raise ValueError('API returned an error object')
        return [body], {}
    raise ValueError('Expected JSON object or array')


def request_filename(request):
    # Human-readable scope, with percent escapes so route names cannot create paths.
    suffix = ''
    query = request['query_parameters']
    if '$skip' in query: suffix += '_offset_' + str(query['$skip'])
    if request['family'] == 'historical' and '$top' in query: suffix += ('_sample_' if request.get('sample_only') else '_top_') + str(query['$top'])
    if query.get('Meta') == 'true': suffix += '_meta'
    return quote(request['label'], safe='-_') + suffix


def completeness_status(request, count, wrapper):
    if request['query_parameters'].get('Meta') == 'true': return 'availability_statistics_only'
    if request.get('sample_only'): return 'sample_only'
    if wrapper.get('Count') is not None and int(wrapper['Count']) > count: return 'partial_response'
    if request['family'] == 'historical':
        if '$top' not in request['query_parameters']: return 'omitted_top_with_documented_default_30_completeness_unverified'
        if wrapper.get('Count') is not None and int(wrapper['Count']) == count: return 'complete_requested_date_by_response_count'
        return 'explicit_top_completeness_unverified'
    if request['family'] == 'drts' and count < int(request['query_parameters'].get('$top', 0)):
        return 'complete_requested_endpoint_by_exhausted_page'
    return 'requested_scope_response'


@lru_cache(maxsize=1)
def district_bounds_reference():
    path = ROOT / 'data/population/taipei_statistical_area_boundaries.geojson'
    if not path.exists(): return None
    from shapely.geometry import shape
    obj = json.loads(path.read_text())
    bounds = {}
    for feature in obj['features']:
        key = feature['properties']['district_id']
        b = shape(feature['geometry']).bounds
        a = bounds.get(key, b)
        bounds[key] = (min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3]))
    return {'file': str(path.relative_to(ROOT)), 'bounds': bounds}


def record_semantics(request):
    label = request['label']
    if request['query_parameters'].get('Meta') == 'true':
        return {'grain': 'one service and requested date', 'units': {'count': 'records', 'size_bytes': 'bytes'}}
    if 'RealTimeByFrequency' in label or 'RealTimeNearStop' in label:
        return {'grain': 'one vehicle position/event observation', 'units': {'speed': 'km/h', 'azimuth': 'degrees', 'position_lat': 'degrees_north', 'position_lon': 'degrees_east'},
                'sample_interpretation': 'First N source records only; no random sampling and no representativeness for the day or three-year period.'}
    if 'DRTS_Route' in label:
        return {'grain': 'one DRTS route', 'units': {'route_distance': None},
                'unit_notes': {'route_distance': 'Official supplied schema describes route length without a unit; do not assume km until independently confirmed.'}}
    if 'DRTS_BookingRule' in label:
        return {'grain': 'one DRTS booking rule', 'units': {'min_minutes_in_advanced': 'minutes', 'latest_day_for_booking': 'days', 'earliest_day_for_booking': 'days', 'min_passenger': 'persons'}}
    if request['family'] == 'historical':
        return {'grain': 'one source entity within the requested historical version; nested stops/trips retained', 'units': {'stop_position': 'longitude/latitude degrees', 'geometry': 'WGS84 longitude/latitude degrees'}}
    if request['family'] == 'travel_time_distance':
        return {'grain': 'one route/subroute/direction with nested stop-pair or timetable records', 'units': {'distance': 'meters', 'run_time': 'seconds', 'historical_est_arrival_time_sd': 'minutes'}}
    return {'grain': 'one source spatial feature returned for the requested district; actual containment is not guaranteed', 'units': {'number_of_households': 'households', 'reasonable_household_numbers': 'households', 'road_legth': None, 'un_coverage_rate': None, 'inaccessible_rate': None},
            'unit_notes': 'Supplied specification does not state the road-length unit or whether rate is a fraction/percentage; preserve raw values without scaling.',
            'underlying_household_statistical_period': None,
            'period_note': 'UpdateDate is a system update date; it does not establish the statistical period of the underlying household counts.',
            'household_interpretation': 'ReasonableHouseholdNumbers is only defined as a reasonable household count; it is not verified as an accessible-household count. Do not subtract it from NumberOfHouseholds to infer inaccessible households.'}


def parse_business(path, request, provenance):
    # Historical JSONL is kept streamed during raw download. Parse each non-empty line, preserving typed values.
    if request['query_parameters'].get('$format') == 'JSONL' and request['query_parameters'].get('Meta') != 'true':
        with path.open(encoding='utf-8-sig') as f: rows = [json.loads(line) for line in f if line.strip()]
        wrapper = {}
    else:
        rows, wrapper = response_records(json.loads(path.read_bytes()))
    if not all(isinstance(r, dict) for r in rows): raise ValueError('Non-object business record')
    metadata = {**record_semantics(request), 'sources': [provenance], 'request': request, 'source_updated_at': wrapper.get('UpdateTime') or wrapper.get('SrcUpdateTime'),
        'statistical_period': {'requested_date': request['path_values'].get('Date') or request['query_parameters'].get('Dates'),
            'version_start': wrapper.get('StartDate'), 'version_end': wrapper.get('EndDate')},
        'response_metadata': normalize(wrapper), 'record_count': len(rows), 'time_zone': 'Asia/Taipei',
        'time_zone_note': 'Asia/Taipei identifies the requested civil day. Each source timestamp retains its own original UTC offset.',
        'scope_status': completeness_status(request, len(rows), wrapper),
        'limitations': ['來源識別碼不直接與臺北市直接下載 feed 的識別碼關聯。', '未知/缺漏不補零；狀態碼原值保留。'],
        'field_dictionary': 'data/tdx/field_dictionary.json'}
    for source_field, period_field in [('EffectiveDate', 'effective_dates'), ('ExpireDate', 'expire_dates')]:
        values = sorted({r[source_field] for r in rows if r.get(source_field)})
        if values: metadata['statistical_period'][period_field] = values
    update_times = sorted({r['UpdateTime'] for r in rows if r.get('UpdateTime')})
    if len(update_times) == 1: metadata['source_updated_at'] = update_times[0]
    elif update_times: metadata['source_record_update_times'] = update_times
    if metadata['source_updated_at'] and not re.search(r'(?:Z|[+-]\d{2}:?\d{2})$', metadata['source_updated_at']):
        metadata['source_time_offset_note'] = 'Source update timestamp has no UTC offset; retained verbatim without timezone inference.'
    filename = request_filename(request)
    target = OUT / ('availability' if request['query_parameters'].get('Meta') == 'true' else 'observations') / (filename + '.json')
    if wrapper.get('type') == 'FeatureCollection' or (rows and all(r.get('type') == 'Feature' for r in rows)):
        from shapely.geometry import shape
        for r in rows:
            if r.get('geometry'):
                geom = shape(r['geometry']); b = geom.bounds
                if not geom.is_empty and (not geom.is_valid or not (119 <= b[0] <= b[2] <= 123 and 21 <= b[1] <= b[3] <= 27)):
                    raise ValueError('Geometry invalid or outside expected WGS84 Taiwan bounds')
            props = r.get('properties') or {}
            if set(props) == {'model'} and isinstance(props['model'], dict): props = props['model']
            r['properties'] = normalize(props)
        dates = sorted({r['properties']['update_date'] for r in rows if r['properties'].get('update_date')})
        if len(dates) == 1: metadata['source_updated_at'] = dates[0]
        if dates: metadata['statistical_period']['source_update_dates'] = dates
        info_dates = sorted({r['properties']['info_date'] for r in rows if r['properties'].get('info_date')})
        if info_dates: metadata['statistical_period']['source_info_dates'] = info_dates
        metadata['empty_geometry_count'] = sum(bool(r.get('geometry')) and shape(r['geometry']).is_empty for r in rows)
        if request['specification_id'] == 'transit_inaccessible':
            boundary = district_bounds_reference()
            code = request['path_values']['TownCode']
            if boundary and code in boundary['bounds']:
                bounds = boundary['bounds'][code]
                outside = []
                for index, feature in enumerate(rows):
                    geom = shape(feature['geometry']) if feature.get('geometry') else None
                    if geom is None or geom.is_empty: continue
                    b = geom.bounds
                    if not (bounds[0] <= b[0] <= b[2] <= bounds[2] and bounds[1] <= b[1] <= b[3] <= bounds[3]): outside.append(index)
                metadata['geometry_screening'] = {'reference_file': boundary['file'], 'reference_bounds': bounds, 'outside_bounds_feature_indexes': outside, 'method': 'bounding-box screening against saved statistical-area boundaries; this is not full district-polygon containment validation'}
                if outside:
                    metadata['geometry_use_status'] = 'requires_source_review_before_district_mapping'
                    metadata['limitations'].append('來源幾何超出所請求行政區的參考邊界範圍；保留原座標，不可直接視為該區不可及戶數分布。')
                else: metadata['geometry_use_status'] = 'passes_bbox_screening_only_not_full_containment_validation'
        metadata['coordinate_reference_system'] = 'EPSG:4326; GeoJSON order [longitude, latitude]; range validated'
        target = target.with_suffix('.geojson')
        output = {'type': 'FeatureCollection', 'schema_version': 1, 'dataset_id': 'tdx_' + request['label'], 'metadata': metadata, 'features': rows}
    else:
        records = [normalize(r) for r in rows]
        if request['family'] == 'travel_time_distance':
            missing = {}
            for record in records:
                for field, keys in [('travel_times', ['from_stop_id', 'to_stop_id', 'run_time']), ('stops', ['from_stop_id', 'to_stop_id', 'distance'])]:
                    for pair in record.get(field, []):
                        for key in keys:
                            if key not in pair:
                                pair[key] = None
                                missing[key] = missing.get(key, 0) + 1
            metadata['missing_fields_filled_with_null'] = missing
        if 'Shape' in request['label'] and records:
            from shapely import from_wkt
            from shapely.geometry import mapping
            features = []
            for r in records:
                wkt = r.pop('geometry', None)
                if not isinstance(wkt, str): raise ValueError('Shape geometry is not WKT')
                geom = from_wkt(wkt); b = geom.bounds
                if not geom.is_valid or not (119 <= b[0] <= b[2] <= 123 and 21 <= b[1] <= b[3] <= 27): raise ValueError('Invalid WGS84 shape')
                features.append({'type': 'Feature', 'properties': r, 'geometry': mapping(geom)})
            metadata['coordinate_reference_system'] = 'EPSG:4326'
            metadata['crs_evidence'] = 'inferred_from_taiwan_longitude_latitude_bounds; original WKT retained in raw source'
            metadata['limitations'].append('來源 WKT 無獨立 CRS 定義，依臺灣經緯度範圍判讀為 WGS84，未重投影。')
            target = target.with_suffix('.geojson')
            output = {'type': 'FeatureCollection', 'schema_version': 1, 'dataset_id': 'tdx_' + request['label'], 'metadata': metadata, 'features': features}
        else: output = {'schema_version': 1, 'dataset_id': 'tdx_' + request['label'], 'metadata': metadata, 'records': records}
    write(target, output)
    return {'output_file': str(target.relative_to(ROOT)), 'record_count': len(rows), 'response_count': wrapper.get('Count'), 'scope_status': metadata['scope_status'], 'source_period': metadata['statistical_period'], 'request': request}


def load_local_credentials():
    path = ROOT / '.env'
    if not path.is_file(): return
    allowed = {'TDX_CLIENT_ID', 'TDX_CLIENT_SECRET'}
    for line in path.read_text(encoding='utf-8-sig').splitlines():
        line = line.strip()
        if not line or line.startswith('#') or '=' not in line: continue
        key, value = line.split('=', 1)
        key = key.strip().removeprefix('export ').strip()
        if key not in allowed: continue
        value = value.strip()
        if len(value) >= 2 and value[0] in {'\"', "'"} and value[-1] == value[0]: value = value[1:-1]
        else: value = re.split(r'\s+#', value, maxsplit=1)[0].strip()
        if value: os.environ.setdefault(key, value)


def token():
    load_local_credentials()
    values = [os.environ.get(k) for k in ['TDX_CLIENT_ID', 'TDX_CLIENT_SECRET']]
    if not all(values): raise ValueError('Missing TDX_CLIENT_ID / TDX_CLIENT_SECRET environment variables')
    data = urlencode({'grant_type': 'client_credentials', 'client_id': values[0], 'client_secret': values[1]}).encode()
    req = Request(TOKEN_URL, data=data, headers={'Content-Type': 'application/x-www-form-urlencoded'})
    try:
        with urlopen(req, timeout=45) as response: result = json.load(response)
    except Exception as exc:
        raise RuntimeError(f'TDX authentication failed ({type(exc).__name__}); credentials were not logged') from None
    return result['access_token']


def fetch_business(args, store, requests):
    auth = token()
    count = 0
    total_bytes = 0
    results = []
    recent = [datetime.fromisoformat(x['downloaded_at']).timestamp() for x in store.manifest['sources'].values() if x.get('request') and x.get('downloaded_at')]
    last_request_at = max(recent, default=0)
    for initial in requests:
        request = initial
        for page in range(args.max_pages):
            if count >= args.max_requests: raise RuntimeError('Reached --max-requests; completed files preserved')
            remaining = int(args.max_total_mb * 1024**2) - total_bytes
            limit = min(remaining, int(args.max_response_mb * 1024**2))
            if limit <= 0: raise RuntimeError('Reached --max-total-mb')
            delay = max(0, args.min_request_interval - (time.time() - last_request_at))
            if delay: time.sleep(delay)
            last_request_at = time.time()
            count += 1
            url = request['request_url']
            filename = request_filename(request)
            query = request['query_parameters']
            suffix = '.jsonl' if query.get('$format') == 'JSONL' and query.get('Meta') != 'true' else '.json'
            path = RAW / 'responses' / (filename + suffix)
            path.parent.mkdir(parents=True, exist_ok=True)
            part = path.with_suffix('.part')
            try:
                req = Request(url, headers={'Authorization': 'Bearer ' + auth, 'Accept-Encoding': 'identity'})
                with urlopen(req, timeout=180) as response:
                    if response.status != 200 and not (response.status == 249 and request['query_parameters'].get('Meta') == 'true'): raise RuntimeError(f'HTTP {response.status}')
                    if int(response.headers.get('Content-Length', 0)) > limit: raise RuntimeError('Response exceeds byte budget')
                    size = 0
                    with part.open('wb') as f:
                        while chunk := response.read(min(65536, max(1, limit - size + 1))):
                            size += len(chunk); total_bytes += len(chunk)
                            if size > limit: raise RuntimeError('Response exceeded byte budget; partial file discarded')
                            f.write(chunk)
                    content_type = response.headers.get('Content-Type')
                    usage_headers = {k.lower(): v for k,v in response.headers.items() if k.lower().startswith(('x-ratelimit', 'x-rate-limit', 'x-quota', 'x-point'))}
                    http_status = response.status
                if content_type and 'text/html' in content_type.lower(): path = path.with_suffix('.html')
                provenance = {'source_page': reference(store, request['specification_id'])['source_page'],
                    'download_url': url, 'raw_file': str(path.relative_to(ROOT)), 'downloaded_at': now(),
                    'source_updated_at': None, 'bytes': size, 'content_type': content_type,
                    'evidence_type': 'api_availability_statistics' if request['query_parameters'].get('Meta') == 'true' else 'business_api_response',
                    'http_status': http_status, 'usage_headers': usage_headers}
                # Preserve the original response even if its format differs from the specification.
                part.replace(path)
                source_id = 'business_' + filename
                store.manifest['sources'][source_id] = {**provenance, 'request': request, 'parsed': False}
                store.save()
                try:
                    item = parse_business(path, request, provenance)
                except (ValueError, UnicodeError) as exc:
                    store.manifest['sources'][source_id]['parse_status'] = type(exc).__name__
                    store.save()
                    print(f'{request["label"]}: response retained for format review', flush=True)
                    break
                store.manifest['sources'][source_id]['parsed'] = True
                store.save(); results.append(item)
                print(f'{request["label"]}: {item["record_count"]} records', flush=True)
            except RuntimeError as exc:
                if 'byte budget' not in str(exc): raise
                store.manifest.setdefault('failures', {})[filename] = {'request': request, 'checked_at': now(), 'reason': 'response_exceeds_local_size_limit_reference_only', 'max_response_bytes': limit, 'partial_file_retained': False}
                store.save()
                print(f'{request["label"]}: size limit; reference retained', flush=True)
                break
            except HTTPError as exc:
                store.manifest.setdefault('failures', {})[filename] = {'request': request, 'checked_at': now(), 'http_status': exc.code, 'reason': 'provider_http_error', 'response_body_saved': False}
                store.save()
                if exc.code in {401, 403, 429}:
                    raise RuntimeError(f'TDX returned HTTP {exc.code}; no retries or account changes performed') from None
                print(f'{request["label"]}: HTTP {exc.code}; reference retained', flush=True)
                break
            finally: part.unlink(missing_ok=True)
            if initial['family'] != 'drts' or item['record_count'] < args.page_size: break
            if page + 1 == args.max_pages: raise RuntimeError('Reached --max-pages; DRTS data may be incomplete')
            query = {**request['query_parameters'], '$skip': (page + 1) * args.page_size}
            request = {**request, 'query_parameters': query, 'request_url': url.split('?')[0] + '?' + urlencode(query)}
    return results


def request_gap(store, url):
    base = url.split('?')[0]
    failures = [x for x in store.manifest.get('failures', {}).values() if x['request']['request_url'].split('?')[0] == base]
    unusable = [x for x in store.manifest['sources'].values() if x.get('request') and x.get('parsed') is False and x['request']['request_url'].split('?')[0] == base]
    if unusable:
        x = unusable[-1]
        return {'status': 'source_returned_unusable_format', 'http_status': x.get('http_status'), 'content_type': x.get('content_type'), 'raw_file': x['raw_file']}
    if failures:
        x = failures[-1]
        return {'status': x['reason'], 'http_status': x.get('http_status'), 'max_response_bytes': x.get('max_response_bytes')}
    return {'status': 'reference_only_not_queried'}


def build_reference_catalog(store, found):
    supply = json.loads((OUT / 'historical_supply.json').read_text())['records']
    as_of = datetime.fromisoformat(store.manifest['sources']['historical_supply']['downloaded_at']).date()
    requested_start = as_of.replace(year=as_of.year - 3)
    requested_end = as_of - timedelta(days=1)
    names = {'RealTimeByFrequency': '定時歷史資料(A1)', 'RealTimeNearStop': '定點歷史資料(A2)',
             'Route': '路線歷史資料', 'Stop': '站牌歷史資料', 'Station': '站位歷史資料',
             'StopOfRoute': '路線站序歷史資料', 'Shape': '線形歷史資料', 'Schedule': '路線班表歷史資料'}
    records = []
    for kind, suffix in names.items():
        item = next(x for x in supply if x['name'] == '臺北市市區公車' + suffix)
        start = max(requested_start, date.fromisoformat(item['data_start_time'][:10]))
        end = min(requested_end, date.fromisoformat(item['data_end_time'][:10]))
        dynamic = kind.startswith('RealTime')
        url = HOST + '/api/historical/v2/Historical/Bus/' + kind
        url += '/City/Taipei?Dates={date}&$format=JSONL' if dynamic else '/Date/{date}/City/Taipei?$format=JSON'
        files = [x for x in found if x['request']['family'] == 'historical' and x['request']['label'].endswith('_' + kind)]
        months = []
        cursor = start
        while cursor <= end:
            next_month = date(cursor.year + (cursor.month == 12), cursor.month % 12 + 1, 1)
            months.append({'month': cursor.strftime('%Y-%m'), 'start_date': cursor.isoformat(), 'end_date': min(end, next_month - timedelta(days=1)).isoformat()})
            cursor = next_month
        records.append({'category': 'historical', 'service': kind, 'city': 'Taipei',
            'official_service_id': item['p_k_service_detail'],
            'available_period_in_requested_window': {'start': start.isoformat(), 'end': end.isoformat()},
            'unavailable_prefix': {'start': requested_start.isoformat(), 'end': (start - timedelta(days=1)).isoformat()} if start > requested_start else None,
            'monthly_partitions': months, 'download_unit': 'one civil date per request',
            'download_url_template': url, 'availability_url_template': url + '&Meta=true' if dynamic else None,
            'retrieval_note': 'Default $top=30; obtain Count/Size first and specify an explicit top for complete data. Historical top samples are not full-day observations.',
            'retained_files': [x['output_file'] for x in files if x['request']['query_parameters'].get('Meta') != 'true'],
            'availability_metadata_files': [x['output_file'] for x in files if x['request']['query_parameters'].get('Meta') == 'true'],
            'coverage_status': 'reference_for_entire_available_window',
            'retrieval_failures': [x for x in store.manifest.get('failures', {}).values() if x['request']['family'] == 'historical' and x['request']['label'].endswith('_' + kind)]})
    # Reference every official Taipei route without issuing hundreds of metered queries.
    routes = []
    for item in found:
        if item['request']['family'] == 'historical' and item['request']['label'].endswith('_Route') and item['scope_status'] == 'complete_requested_date_by_response_count':
            routes = json.loads((ROOT / item['output_file']).read_text())['records']
            route_reference = item['output_file']
            break
    for route in routes:
        route_name = route.get('route_name', {}).get('zh_tw')
        route_id = route.get('route_id')
        for service in ['Schedule', 'S2STravelTime', 'S2SDistance']:
            if service == 'S2SDistance':
                url = HOST + '/api/premium/S2SDistance/City/Taipei/RouteID/' + quote(route_id, safe='') + '?$format=JSON'
            else:
                url = HOST + '/api/premium/V3/Map/Estimation/Bus/' + service + '/City/Taipei/RouteName/' + quote(route_name, safe='') + '?$format=JSON'
            matches = [x for x in found if x['request']['family'] == 'travel_time_distance' and x['request']['request_url'].split('?')[0] == url.split('?')[0]]
            records.append({'category': 'travel_time_distance', 'service': service, 'city': 'Taipei', 'route_id': route_id,
                'route_name': route_name, 'route_identifier_source': route_reference, 'download_url': url,
                'retained_files': [x['output_file'] for x in matches],
                'coverage_status': 'downloaded_selected_route' if matches else request_gap(store, url)['status'],
                'source_gap': None if matches else request_gap(store, url)})
    district_path = ROOT / 'data/population/taipei_statistical_area_boundaries.geojson'
    if district_path.exists():
        features = json.loads(district_path.read_text())['features']
        districts = sorted({(x['properties']['district_id'], x['properties']['district_name']) for x in features})
        for code, name in districts:
            for service, prefix in [('bus_uncovered', 'RoadUnCoverage/Bus'), ('transit_inaccessible', 'InAccessibility/Transit/Household')]:
                url = HOST + '/api/premium/V3/Map/Governance/' + prefix + '/City/Taipei/TownCode/' + code + '?$format=GEOJSON'
                matches = [x for x in found if x['request']['family'] == 'accessibility' and x['request']['request_url'].split('?')[0] == url.split('?')[0]]
                records.append({'category': 'accessibility', 'service': service, 'city': 'Taipei', 'district_id': code,
                    'district_name': name, 'district_identifier_source': str(district_path.relative_to(ROOT)), 'download_url': url,
                    'retained_files': [x['output_file'] for x in matches], 'coverage_status': 'downloaded' if matches else request_gap(store, url)['status'],
                    'source_gap': None if matches else request_gap(store, url)})
    write(OUT / 'reference_catalog.json', {'schema_version': 1, 'dataset_id': 'tdx_reference_catalog', 'metadata': {
        'grain': 'one historical service, route/service, or district/service reference',
        'evidence_type': 'download_reference_not_business_observation',
        'sources': [reference(store, 'historical_supply')], 'source_updated_at': None,
        'statistical_period': {'requested_start': requested_start.isoformat(), 'requested_end': requested_end.isoformat()},
        'limitations': ['每月分區是可供後續取用的目錄，未逐日驗證完整性。', '加值路線目錄以所保存最近日TDX路線ID產生；不等於每條路線均有加值資料。']}, 'records': records})
    return str((OUT / 'reference_catalog.json').relative_to(ROOT))


def delivery(store):
    found = []
    for key, source in store.manifest['sources'].items():
        if not key.startswith('business_'): continue
        req = source['request']; path = ROOT / source['raw_file']
        if not path.is_file(): raise ValueError('Missing business source')
        try:
            item = parse_business(path, req, {k:v for k,v in source.items() if k not in {'request', 'parsed', 'parse_status'}})
        except (ValueError, UnicodeError) as exc:
            source['parsed'] = False
            source['parse_status'] = 'provider_request_rejected_html' if b'Request Rejected' in path.read_bytes()[:500] else type(exc).__name__
            continue
        source['parsed'] = True
        source.pop('parse_status', None)
        found.append(item)
    store.save()
    reference_file = build_reference_catalog(store, found)
    rows = []
    for family, (title, names) in FAMILIES.items():
        files = [r for r in found if r['request']['family'] == family and r['request']['query_parameters'].get('Meta') != 'true']
        availability = [r['output_file'] for r in found if r['request']['family'] == family and r['request']['query_parameters'].get('Meta') == 'true']
        gaps = [x for x in store.manifest.get('failures', {}).values() if x['request']['family'] == family]
        gaps += [{'request': x['request'], 'reason': 'source_returned_unusable_format', 'http_status': x.get('http_status'), 'content_type': x.get('content_type'), 'raw_file': x['raw_file']} for x in store.manifest['sources'].values() if x.get('request') and x['request']['family'] == family and x.get('parsed') is False]
        rows.append({'dataset_id': 'tdx_' + family, 'title': title,
            'status': ('sample_and_reference' if family == 'historical' else 'selected_route_and_reference' if family == 'travel_time_distance' else 'downloaded_requested_scope') if files else 'blocked_missing_credentials_or_scope',
            'gaps': gaps,
            'output_files': [r['output_file'] for r in files], 'availability_files': availability, 'reference_files': [reference_file], 'scope_statuses': sorted({r['scope_status'] for r in files}), 'record_count': sum(r['record_count'] for r in files) if files else None,
            'period': sorted(set(r['request']['path_values'].get('Date') or r['request']['query_parameters'].get('Dates') for r in files if r['request']['path_values'].get('Date') or r['request']['query_parameters'].get('Dates'))) or [r['source_period'] for r in files if any(r['source_period'].values())] or None,
            'specification_output_files': ['data/tdx/api_catalog.json', 'data/tdx/field_dictionary.json'] + (['data/tdx/historical_supply.json'] if family == 'historical' else []),
            'limitations': ['尚無實際明細；規格與供應目錄不可當成實績。'] if not files else (['近三年保留完整供應reference，實際歷史僅明列日期；$top樣本為前N筆，只供格式與串接驗證，非隨機或整日代表。'] if family == 'historical' else ['僅下載307路線已成功取得的加值資料；416條路線保留完整取用reference，沒有全市加值覆蓋。'] if family == 'travel_time_distance' else ['已取得臺北12區的請求回應，但不代表幾何已完整落於各區。松山不可及Point超出松山參考邊界，須待來源釐清才可作區內分布圖；其他區只通過bbox篩檢。合理家戶數量不可直接當作可及戶數，也不可用兩戶數欄位差值推估不可及戶數。'] if family == 'accessibility' else ['僅代表明列請求範圍與來源期間。'])})
    write(RAW / 'delivery.json', {'schema_version': 1, 'generated_at': now(),
        'credential_config_present': bool(os.getenv('TDX_CLIENT_ID') and os.getenv('TDX_CLIENT_SECRET')),
        'records': rows})
    return rows


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--offline', action='store_true', help='Validate cached source format and rebuild only')
    p.add_argument('--use-cached-specs', action='store_true', help='Use existing public specifications while executing an authorized fetch')
    p.add_argument('--historical-kind', action='append', choices=['RealTimeByFrequency','RealTimeNearStop','Route','Stop','Station','StopOfRoute','Shape','Schedule'])
    p.add_argument('--meta-only', action='store_true', help='Query A1/A2 availability count and size; not observations')
    p.add_argument('--full-top', type=int, help='Explicit historical row bound; Count must confirm completeness')
    p.add_argument('--plan', action='store_true', help='Write request plan without authentication/data API calls')
    p.add_argument('--fetch', action='store_true', help='Execute the explicitly selected API scope')
    p.add_argument('--city', action='append', choices=['Taipei','NewTaipei'])
    p.add_argument('--family', action='append', choices=list(FAMILIES))
    p.add_argument('--date', action='append', default=[])
    p.add_argument('--travel-kind', action='append', choices=['Schedule','S2STravelTime','Distance'])
    p.add_argument('--route-name', action='append', default=[])
    p.add_argument('--route-id', action='append', default=[])
    p.add_argument('--town-code', action='append', default=[])
    p.add_argument('--sample-top', type=int, help='Deliberate sample only; never represents full historical coverage')
    p.add_argument('--page-size', type=int, default=1000)
    p.add_argument('--max-pages', type=int, default=10)
    p.add_argument('--min-request-interval', type=float, default=13, help='Seconds between API calls; default respects observed five calls/minute')
    p.add_argument('--max-requests', type=int, default=20)
    p.add_argument('--max-response-mb', type=float, default=8)
    p.add_argument('--max-total-mb', type=float, default=20)
    args = p.parse_args()
    if args.offline and args.fetch: p.error('--offline cannot be combined with --fetch')
    if any(x <= 0 for x in [args.page_size,args.max_pages,args.max_requests,args.max_response_mb,args.max_total_mb]): p.error('Limits must be positive')
    if args.sample_top is not None and args.sample_top <= 0: p.error('--sample-top must be positive')
    args.city = args.city or ['Taipei']
    if (args.plan or args.fetch) and not args.family: p.error('Specify one or more --family scopes')
    if args.sample_top and args.full_top: p.error('--sample-top and --full-top are mutually exclusive')
    load_local_credentials()
    store = Store(args.offline or args.use_cached_specs)
    specs = catalog(store)
    try:
        if args.plan or args.fetch:
            plan = make_plan(args, specs)
            write(RAW / 'request_plan.json', {'schema_version': 1, 'generated_at': now(),
                'status': 'planned_not_executed' if not args.fetch else 'fetch_requested',
                'minimum_api_requests': len(plan), 'max_api_requests': args.max_requests,
                'max_total_bytes': int(args.max_total_mb*1024**2), 'max_pages_per_endpoint': args.max_pages,
                'billing_limit_note': '本地下載上限不是供應商扣點保證；請先確認方案與可用點數。',
                'credentials_saved': False, 'requests': plan})
            print(f'Plan: {len(plan)} initial requests; maximum {args.max_requests} API requests')
            if args.fetch:
                if len(plan) > args.max_requests: raise ValueError('Plan exceeds --max-requests; change scope or explicitly raise limit')
                fetch_business(args, store, plan)
    finally:
        rows = delivery(store)
    print(json.dumps({'catalogs': 4, 'families': len(rows), 'business_families_with_data': sum(bool(r['output_files']) for r in rows)}, ensure_ascii=False))

if __name__ == '__main__': main()
