"""Independent raw road response review; imports no producer code."""
from collections import Counter
from datetime import datetime
import json
from pathlib import Path
import re
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
COLLECTIONS = {'section': 'Sections', 'vd': 'VDs', 'section_live': 'LiveTraffics', 'vd_live': 'VDLives'}


def load(path):
    return json.loads((ROOT / path).read_text(encoding='utf-8'))


def field_name(value):
    # A separately implemented field-name check, supplemented by per-leaf comparisons.
    first = re.sub(r'([A-Z]+)([A-Z][a-z])', r'\1_\2', value)
    result = re.sub(r'([a-z0-9])([A-Z])', r'\1_\2', first).lower()
    return {'vdid': 'vd_id', 'link_i_ds': 'link_ids'}.get(result, result)


def compare(source, output, key=''):
    if isinstance(source, dict):
        assert isinstance(output, dict)
        for name, value in source.items():
            if name not in ['DataHash', 'GeoHash']:
                assert field_name(name) in output
                compare(value, output[field_name(name)], name)
    elif isinstance(source, list):
        assert isinstance(output, list) and len(source) == len(output)
        for s, o in zip(source, output):
            compare(s, o, key)
    elif source is None or isinstance(source, str) and not source.strip():
        assert output is None
    elif key.endswith(('ID', 'UID', 'Code')):
        assert isinstance(output, str) and output == str(source)
    else:
        assert type(source) is type(output) and source == output


def review():
    manifest = load('raw_data/tdx_road_traffic/manifest.json')
    delivery = load('data/tdx_road_traffic/delivery.json')
    outputs = {r['source_id']: r for r in delivery['records']}
    checks = []
    for receipt in manifest['records']:
        if not receipt.get('request') or receipt.get('status') != 'downloaded':
            continue
        req = receipt['request']
        path = ROOT / receipt['raw_file']
        assert path.stat().st_size == receipt['bytes']
        assert receipt['download_url'] == req['request_url']
        assert receipt['http_status'] in ([200, 249] if req['mode'] == 'meta' else [200])
        raw = path.read_text(encoding='utf-8-sig')
        if req['category'] == 'historical' and req['mode'] != 'meta':
            rows = [json.loads(line) for line in raw.splitlines() if line.strip()]
        else:
            body = json.loads(raw)
            if req['mode'] == 'meta':
                assert body['Dates'] == req['date'] and isinstance(body['Count'], int)
                rows = [body]
            else:
                named = COLLECTIONS[req['key']]
                names = [key for key in [named, 'Items'] if isinstance(body.get(key), list)]
                assert len(names) == 1
                rows = body[names[0]]
                assert len(rows) == body['Count']
        entry = outputs[receipt['source_id']]
        output = load(entry['output_file'])
        assert output['metadata']['license']['url'] == 'https://tdx.transportdata.tw/term'
        assert len(rows) == len(output['records']) == output['metadata']['record_count'] == entry['record_count']
        for s, o in zip(rows, output['records']):
            compare(s, o)
        period = output['metadata']['statistical_period']
        if req['category'] == 'historical' and req['mode'] != 'meta':
            info_mismatch = sum(s['InfoDate'] != req['date'] for s in rows)
            collect_mismatch = sum(datetime.fromisoformat(s['DataCollectTime']).astimezone(ZoneInfo('Asia/Taipei')).date().isoformat() != req['date']
                                   for s in rows if s.get('DataCollectTime'))
            assert period['source_info_date_mismatch_count'] == info_mismatch
            assert period['data_collect_requested_taipei_day_mismatch_count'] == collect_mismatch
            assert output['metadata']['out_of_requested_date_count'] == info_mismatch
            assert output['metadata']['requires_source_review'] is bool(info_mismatch or collect_mismatch)
        kind = req['data_kind']
        if kind == 'road_definition':
            assert 'static' in output['metadata']['grain']
        if req['mode'] == 'sample':
            assert output['metadata']['scope_status'] == 'sample_only'
        if kind == 'detector_flow_observation':
            assert output['metadata']['units']['volume'] is None
        item = {'output_path': entry['output_file'], 'source_id': receipt['source_id'],
                'record_count': len(rows), 'review_status': 'pass_with_limitations',
                'all_source_fields_nested_values_and_ids_compared': True,
                'raw_file': receipt['raw_file'], 'http_status': receipt['http_status'],
                'requested_date': req['date'], 'data_kind': kind,
                'scope_status': output['metadata']['scope_status'],
                'source_info_dates': period['source_info_dates'],
                'data_collect_taipei_dates': period['data_collect_taipei_dates'],
                'source_info_date_mismatch_count': period['source_info_date_mismatch_count'],
                'data_collect_date_mismatch_count': period['data_collect_requested_taipei_day_mismatch_count'],
                'requires_source_review': output['metadata']['requires_source_review']}
        if kind == 'detector_flow_observation':
            leaves = [v for r in rows for link in (r.get('LinkFlows') or []) for lane in (link.get('Lanes') or [])
                      for v in (lane.get('Vehicles') or [])]
            item.update({'vehicle_type_rows': len(leaves), 'known_volume_raw_sum': sum(v['Volume'] for v in leaves if isinstance(v.get('Volume'), (int, float))),
                         'volume_missing_count': sum(v.get('Volume') is None for v in leaves),
                         'sum_interpretation': 'QA arithmetic only across source rows; no physical hourly/daily traffic interpretation.'})
        checks.append(item)
    assert set(outputs) == {r['source_id'] for r in checks}
    diagnostics = load('data/tdx_road_traffic/reference_diagnostics.json')
    for row in diagnostics['records']:
        observation = load(row['observation_file'])
        definition = load(row['definition_file'])
        observed = {r[row['id_field']] for r in observation['records']}
        defined = {r[row['id_field']] for r in definition['records']}
        assert row['matched_exact_id_count'] == len(observed & defined)
        assert set(row['observation_only_ids']) == observed - defined
        assert set(row['definition_only_ids']) == defined - observed
        assert row['version_equivalence_verified'] is False
        assert row['join_status'] == 'coverage_diagnostic_only_not_a_versioned_join'
    historical = load('data/tdx_road_traffic/historical_catalog.json')
    assert len(historical['records']) == 4
    for service in historical['records']:
        for request in service['month_requests']:
            start = datetime.fromisoformat(request['start_date'])
            end = datetime.fromisoformat(request['end_date'])
            assert 0 <= (end - start).days <= 6
    report = {'schema_version': 1, 'dataset_id': 'tdx_road_traffic_independent_review',
              'metadata': {'reviewer': 'root; different agent from road-data producer',
                           'reviewed_at': datetime.now(ZoneInfo('Asia/Taipei')).isoformat(timespec='seconds'),
                           'method': 'Independent JSON/JSONL per-field and nested source-row inspection; producer not imported.',
                           'independent_review_status': 'pass_with_limitations',
                           'id_diagnostics_checked': len(diagnostics['records']),
                           'limitations': ['Static Section history is not a speed or flow observation.',
                                           'Historical speed/flow rows are first-N samples; complete historical volume not downloaded.',
                                           'VD unit/aggregation period unspecified by source schema; negative sentinels retained.',
                                           'Both historical dynamic samples contain 30 observations outside the requested Taipei civil day; not validated as observations within that day.',
                                           'Named collection fields differ from current supplied OpenAPI Items; actual raw responses retained.']},
              'records': checks}
    target = ROOT / 'data/quality/tdx_road_traffic_review.json'
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'outputs_reviewed': len(checks), 'records_reviewed': sum(r['record_count'] for r in checks),
                      'kinds': dict(Counter(r['data_kind'] for r in checks))}))


if __name__ == '__main__':
    review()
