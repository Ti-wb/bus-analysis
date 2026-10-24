"""Bounded TDX historical route masters; coverage is explicit, never inferred by name."""
from __future__ import annotations

import argparse
from datetime import date
import json
from pathlib import Path
import time
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .common import ROOT, now, write_json
from scripts.update_tdx import normalize, response_records, token

SPEC_URL = 'https://tdx.transportdata.tw/webapi/File/Swagger/V3/39472fde-f790-42b9-9c8b-88282118e6b1'
HOST = 'https://tdx.transportdata.tw'
SCOPES = ('NewTaipei', 'Keelung', 'InterCity')


def request_url(scope, requested_date):
    if scope not in SCOPES:
        raise ValueError('Unsupported route-master scope')
    date.fromisoformat(requested_date)
    path = 'InterCity' if scope == 'InterCity' else 'City/' + scope
    return (f'{HOST}/api/historical/v2/Historical/Bus/Route/Date/{requested_date}/{path}?'
            + urlencode({'$format': 'JSON', '$top': 100000}))


def parse(body, source, scope, requested_date):
    rows, wrapper = response_records(body)
    if not isinstance(body, dict) or not rows or wrapper.get('Count') != len(rows):
        raise ValueError('Historical route master must have a verified full Count')
    keys = set()
    for row in rows:
        uid = row.get('RouteUID')
        if not isinstance(uid, str) or not uid or uid in keys:
            raise ValueError('Missing/duplicate RouteUID')
        keys.add(uid)
        if scope != 'InterCity' and row.get('City') != scope:
            raise ValueError('Source City differs from requested scope')
    return {'schema_version': 1, 'dataset_id': 'tdx_regional_routes_' + scope.lower(),
            'metadata': {'sources': [source], 'source_updated_at': wrapper.get('UpdateTime'),
                         'requested_date': requested_date,
                         'version_start': wrapper.get('StartDate'), 'version_end': wrapper.get('EndDate'),
                         'source_version_id': str(wrapper['VersionID']) if wrapper.get('VersionID') is not None else None,
                         'grain': 'one published TDX RouteUID; subroutes/operators remain nested',
                         'record_count': len(rows), 'source_count': wrapper['Count'],
                         'scope_status': 'complete_requested_route_master_by_response_count',
                         'scope': scope, 'independent_review_status': 'pending',
                         'license': {'name': 'TDX資料使用條款', 'url': HOST + '/term'},
                         'limitations': ['完整指定日期路線主檔不等於完整班表、站序或實際營運覆蓋。',
                                         '保留全國公路客運主檔，不以名稱猜測北北基地理範圍。',
                                         '來源版本可能跨日；要求日期不是來源逐筆更新日期。',
                                         '未提供行人通行權或合法步行轉乘證明。']},
            'records': [normalize(r) for r in rows]}


def acquire(root, scope, requested_date, *, offline, auth=None, max_mb=8):
    raw = root / 'raw_data/official_cross_city/regional_coverage'
    raw.mkdir(parents=True, exist_ok=True)
    path = raw / f'{scope}_{requested_date}.json'
    receipt = path.with_suffix('.source.json')
    url = request_url(scope, requested_date)
    if offline:
        source = json.loads(receipt.read_text(encoding='utf-8'))
        if (source['download_url'] != url or source['bytes'] != path.stat().st_size
                or source.get('http_status') != 200):
            raise ValueError('Cached route master does not match source receipt')
        body = json.loads(path.read_bytes())
    else:
        part = path.with_suffix('.part')
        try:
            req = Request(url, headers={'Authorization': 'Bearer ' + auth, 'Accept-Encoding': 'identity'})
            with urlopen(req, timeout=120) as response:
                if response.status != 200 or 'html' in response.headers.get('Content-Type', '').lower():
                    raise ValueError('Expected a complete historical JSON response')
                limit = max_mb * 1024**2
                if int(response.headers.get('Content-Length', 0)) > limit:
                    raise ValueError('Route master exceeds local byte budget')
                content = response.read(limit + 1)
                if not content or len(content) > limit:
                    raise ValueError('Empty or oversized route master')
                declared = response.headers.get('Content-Length')
                if declared and len(content) != int(declared):
                    raise ValueError('Truncated historical response')
                body = json.loads(content)
                source = {'source_page': HOST + '/data-service/historical', 'specification_url': SPEC_URL,
                          'download_url': url, 'raw_file': path.relative_to(root).as_posix(),
                          'downloaded_at': now(), 'source_updated_at': None,
                          'http_status': response.status, 'bytes': len(content)}
            parse(body, source, scope, requested_date)  # Validate before replacing the cached snapshot.
            part.write_bytes(content)
            part.replace(path)
            write_json(receipt, source)
        finally:
            part.unlink(missing_ok=True)
    return parse(body, source, scope, requested_date)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=ROOT)
    parser.add_argument('--date', required=True, type=lambda s: date.fromisoformat(s).isoformat())
    parser.add_argument('--scope', choices=SCOPES, action='append')
    parser.add_argument('--min-request-interval', type=float, default=13)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--fetch', action='store_true')
    mode.add_argument('--offline', action='store_true')
    args = parser.parse_args(argv)
    if args.min_request_interval < 13:
        parser.error('Keep at least 13 seconds between TDX requests')
    scopes = list(dict.fromkeys(args.scope or SCOPES))
    if not (args.fetch or args.offline):
        print(json.dumps({'status': 'plan_only', 'requests': [request_url(s, args.date) for s in scopes]}))
        return
    auth = token() if args.fetch else None
    entries = []
    for i, scope in enumerate(scopes):
        if i and args.fetch:
            time.sleep(args.min_request_interval)
        result = acquire(args.root, scope, args.date, offline=args.offline, auth=auth)
        target = args.root / f'data/official_cross_city/regional_routes/{scope}.json'
        write_json(target, result)
        entries.append({'scope': scope, 'output_path': target.relative_to(args.root).as_posix(),
                        'record_count': len(result['records']), 'requested_date': args.date,
                        'source': result['metadata']['sources'][0], 'scope_status': result['metadata']['scope_status']})
        print(f'{scope}: {len(result["records"])} complete route-master records', flush=True)
    write_json(args.root / 'raw_data/official_cross_city/regional_coverage/manifest.json',
               {'schema_version': 1, 'dataset_id': 'tdx_regional_route_manifest',
                'metadata': {'record_count': len(entries)}, 'records': entries})


if __name__ == '__main__':
    main()
