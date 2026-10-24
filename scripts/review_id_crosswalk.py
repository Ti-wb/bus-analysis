"""Independent raw-record inspection; does not import the crosswalk producer."""
from collections import Counter
import csv
import gzip
import json
from pathlib import Path
import re
from urllib.parse import parse_qs, urlsplit

from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]


def load(path):
    return json.loads((ROOT / path).read_text(encoding='utf-8'))


def read_feed(name):
    with gzip.open(ROOT / 'raw_data/official_cross_city/crosswalk' / name, 'rt', encoding='utf-8') as f:
        return json.load(f)


def map_id(url):
    if not isinstance(url, str):
        return None
    value = urlsplit(url)
    if (value.scheme, value.netloc, value.path) != ('https', 'ebus.gov.taipei', '/MapOverview'):
        return None
    items = parse_qs(value.query)
    return items['nid'][0] if set(items) == {'nid'} and len(items['nid']) == 1 else None


def review():
    raw = ROOT / 'raw_data/official_cross_city/crosswalk'
    routes = read_feed('ntpc_route_feed.gz')['BusInfo']
    stops = read_feed('ntpc_stop_feed.gz')['BusInfo']
    paths = read_feed('ntpc_pathdetail.gz')['BusInfo']
    results = []
    # This comparison reads raw CSV and raw JSON independently of normalized inputs.
    def clean(v):
        return None if v is None or str(v).strip() == '' else str(v).strip()
    comparisons = {}
    for kind, feed in [('routes', routes), ('stops', stops)]:
        candidates = {}
        for row in feed:
            candidates.setdefault(str(row['Id']), []).append({k.lower(): clean(v) for k, v in row.items()})
        with (raw / ('ntpc_' + kind + '.csv')).open(encoding='utf-8-sig', newline='') as f:
            source = [{k.lower(): clean(v) for k, v in row.items()} for row in csv.DictReader(f, strict=True)]
        for row in source:
            assert any(all(other.get(k) == v for k, v in row.items()) for other in candidates[row['id']])
        comparisons[kind] = {'source_csv_rows': len(source), 'raw_feed_rows': len(feed), 'matched_all_source_columns': len(source)}
    file = 'data/official_cross_city/ntpc_feed_id_crosswalk.json'
    output = load(file)
    expected = {(str(r['Id']), str(r['pathAttributeId']), str(r['providerId'])) for r in routes}
    observed = {(r['route_id'], r['subroute_id'], r['operator_id']) for r in output['records']}
    assert expected == observed and len(observed) == len(output['records'])
    for r in output['records']:
        for number in r['source_record_numbers']:
            source = routes[number - 1]
            assert r['route_nid'] == source['NId'] == map_id(source['roadMapUrl'])
            assert r['subroute_nid'] == source['pathAttributeNId']
    results.append({'output_path': file, 'record_count': len(observed), 'review_status': 'pass_with_limitations',
                    'source_comparisons': comparisons, 'all_ids_and_source_row_numbers_verified': True})
    soup = BeautifulSoup((raw / 'ebus_ntpc_routes.html').read_text(encoding='utf-8'), 'html.parser')
    headings = [a for a in soup.select('a[href^="#collapse"]') if a.get_text(' ', strip=True) in ['Jump Frog Bus', '跳蛙公車', '跳蛙專車']]
    assert len(headings) == 1
    panel = soup.find(id=headings[0]['href'][1:])
    nids = {re.fullmatch(r"javascript:go\('([0-9A-Z]+)'\)", a['href'])[1] for a in panel.select('a[href^="javascript:go"]')}
    file = 'data/official_cross_city/ntpc_leapfrog_crosswalk.json'
    output = load(file)
    assert nids == {r['route_nid'] for r in output['records']}
    for r in output['records']:
        related = [s for s in routes if s['NId'] == r['route_nid']]
        assert {str(s['Id']) for s in related} == {r['route_id']}
        assert {str(s['pathAttributeId']) for s in related} == set(r['subroute_ids'])
        assert {str(s['providerId']) for s in related} == set(r['operator_ids'])
    results.append({'output_path': file, 'record_count': len(nids), 'review_status': 'pass_with_limitations',
                    'all_official_category_nids_and_feed_relations_verified': True,
                    'unique_subroute_count': len({b for r in output['records'] for b in r['subroute_ids']})})
    file = 'data/official_cross_city/ntpc_leapfrog_list_crosswalk.json'
    output = load(file)
    with (raw / 'ntpc_leapfrog.csv').open(encoding='utf-8-sig', newline='') as f:
        source = list(csv.DictReader(f, strict=True))
    assert len(source) == len(output['records'])
    for s, r in zip(source, output['records']):
        assert s['seqno'] == r['source_sequence'] and s['routename'] == r['route_name']
        assert r['route_id'] is r['route_nid'] is r['subroute_id'] is None
    results.append({'output_path': file, 'record_count': len(source), 'review_status': 'pass_with_limitations',
                    'verified_route_id_count': 0, 'unresolved_count': len(source),
                    'limitation': 'CSV has no explicit route ID; all unknown links remain null.'})
    file = 'data/official_cross_city/ntpc_unlinked_stops_crosswalk.json'
    output = load(file)
    source_stops = {str(r['Id']): r for r in stops}
    used = {str(r['stopId']) for r in paths}
    missing = set(source_stops) - used
    assert missing == {r['stop_id'] for r in output['records']}
    for r in output['records']:
        assert r['stop_name'] == source_stops[r['stop_id']]['nameZh']
        assert r['subroute_id'] is None and r['pathdetail_presence'] == 'absent_in_snapshot'
    results.append({'output_path': file, 'record_count': len(missing), 'review_status': 'pass_with_limitations',
                    'source_stop_count': len(source_stops), 'pathdetail_rows': len(paths), 'linked_stop_count': len(used),
                    'unlinked_stop_count': len(missing), 'source_label_counts': dict(Counter(r['source_label_service_note'] or 'none' for r in output['records'])),
                    'limitation': 'Source labels do not independently verify the cause or permanent service withdrawal.'})
    # Read each TDX source response and each direct feed rather than trusting the bridge output.
    tdx_files = ['data/tdx/observations/Taipei_2026-10-20_Route_top_100000.json',
                 'data/official_cross_city/regional_routes/NewTaipei.json']
    file = 'data/official_cross_city/tdx_route_crosswalk.json'
    output = load(file)
    index = {r['tdx_route_uid']: r for r in output['records']}
    checked = 0
    for name in tdx_files:
        meta = load(name)['metadata']
        source = load(meta['sources'][0]['raw_file'])
        tdx = source.get('Items') or source.get('BusRoutes')
        assert len(tdx) == source['Count']
        city = tdx[0]['City']
        if city == 'Taipei':
            with gzip.open(ROOT / 'raw_data/taipei_feeds/GetRoute.gz', 'rt', encoding='utf-8') as f:
                direct = json.load(f)['BusInfo']
        else:
            direct = routes
        map_counts = Counter(map_id(r.get('RouteMapImageUrl')) for r in tdx)
        for s in tdx:
            r = index[s['RouteUID']]
            assert r['tdx_route_id'] == str(s['RouteID']) and r['route_map_url'] == s.get('RouteMapImageUrl')
            nid = map_id(s.get('RouteMapImageUrl'))
            ids = {str(x['Id']) for x in direct if x.get('NId') == nid} if nid else set()
            verified = len(ids) == 1 and map_counts[nid] == 1 and str(s['RouteID']) in ids
            assert r['linkage_status'].startswith('verified_') == verified
            assert r['reference_route_id'] == (str(s['RouteID']) if verified else None)
            checked += 1
    assert checked == len(output['records'])
    results.append({'output_path': file, 'record_count': checked, 'review_status': 'pass_with_limitations',
                    'all_raw_tdx_and_direct_feed_ids_maps_uniqueness_verified': True,
                    'status_counts': dict(Counter(r['linkage_status'] for r in output['records']))})
    report = {'schema_version': 1, 'dataset_id': 'id_crosswalk_independent_review',
              'metadata': {'reviewer': 'root; different agent from crosswalk producer', 'reviewed_at': __import__('datetime').datetime.now().astimezone().isoformat(),
                           'method': 'Independent raw CSV/gzip/HTML parser and per-record comparison; producer not imported.',
                           'independent_review_status': 'pass_with_limitations',
                           'limitations': ['Version-specific explicit identifier relations; no assertion of full regional operations.',
                                           '65 CSV leapfrog links, 10 TDX links and 114 stop memberships remain unresolved.']},
              'records': results}
    target = ROOT / 'data/quality/id_crosswalk_review.json'
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'outputs_reviewed': len(results), 'rows_checked': sum(r['record_count'] for r in results)}, ensure_ascii=False))


if __name__ == '__main__':
    review()
