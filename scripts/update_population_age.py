#!/usr/bin/env python3
"""Acquire and normalize Taipei smallest-statistical-area population by five-year age group."""

import argparse
from datetime import datetime
import json
from pathlib import Path
import re
import subprocess
import xml.etree.ElementTree as ET
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / 'raw_data/population_age'
OUT = ROOT / 'data/population'
PAGE_URL = 'https://data.gov.tw/dataset/18255'
# 臺北市統計區五歲年齡組人口統計_最小統計區 (XML resource listed on PAGE_URL).
SOURCE_URL = ('https://segisws.moi.gov.tw/STATWSSTData/OpenService.asmx/GetStatSTDataForOpenCode'
    '?oCode=6E03CA29B955A854397166638603FC3B51A1FBEE829C41DB383C1E6118239FA900541D4FC678FFFA0956DEC9A8D69E14E03D116C7D8EACF3')
RAW_NAME = 'statistical_population_age5_base.xml'
DATASET_ID = 'taipei_statistical_population_age5_base'
OUTPUT = OUT / f'{DATASET_ID}.json'
BASE_POPULATION = OUT / 'taipei_statistical_population_base.json'
AGE_FIELDS = [f'A{a}A{a + 4}_CNT' for a in range(0, 100, 5)] + ['A100UP_5_CNT']
AGE_GROUPS = [f'{a}-{a + 4}' for a in range(0, 100, 5)] + ['100_plus']
LIMITATIONS = [
    '戶籍人口，不代表白天活動人口或公車搭乘需求。',
    '來源只提供年齡組總數，沒有性別拆分。',
    '統計區代碼與行政里代碼不同，不以名稱或碼前綴關聯里別資料。',
    '邊界檔名標示 2015，人口為 2024-12；兩者版本不同。',
]


def now():
    return datetime.now(ZoneInfo('Asia/Taipei')).isoformat(timespec='seconds')


def write(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    part = path.with_suffix(path.suffix + '.part')
    with part.open('w', encoding='utf-8', newline='\n') as f:
        json.dump(obj, f, ensure_ascii=False, allow_nan=False, separators=(',', ':'))
        f.write('\n')
    part.replace(path)


def download(manifest):
    path = RAW / RAW_NAME
    part = path.with_suffix('.xml.download')
    proc = subprocess.run(['curl', '--fail', '--location', '--silent', '--show-error',
        '--proto', '=https', '--proto-redir', '=https', '--connect-timeout', '25', '--max-time', '240',
        '--retry', '2', '--retry-delay', '2', '--dump-header', '-', '--output', str(part), SOURCE_URL],
        text=True, capture_output=True)
    if proc.returncode:
        part.unlink(missing_ok=True)
        raise RuntimeError(f'{RAW_NAME}: {proc.stderr.strip()}')
    headers = {}
    for line in proc.stdout.splitlines():
        if line.startswith('HTTP/'):
            headers = {}
        elif ':' in line:
            k, v = line.split(':', 1)
            headers[k.lower()] = v.strip()
    part.replace(path)
    manifest[RAW_NAME] = {'page_url': PAGE_URL, 'url': SOURCE_URL, 'raw_file': path.relative_to(ROOT).as_posix(),
        'downloaded_at': now(), 'source_updated_at': None, 'last_modified': headers.get('last-modified')}
    print(RAW_NAME, path.stat().st_size, flush=True)
    return path


def integer(value):
    text = (value or '').strip()
    return int(text) if re.fullmatch(r'\d+', text) else None


def normalize(path, manifest):
    root = ET.parse(path).getroot()
    if root.tag != 'Result':
        raise ValueError('Expected statistical Result XML')
    info = {e.tag: e.text for e in root.find('Info')}
    columns = {c.findtext('COLUMN_NAME'): c for c in root.findall('ColumnList/Column')}
    if list(columns) != ['INFO_TIME', 'CODE2', 'CODE1', 'CODEBASE', *AGE_FIELDS]:
        raise ValueError(f'Unexpected age XML columns: {list(columns)}')
    if info['InCountyId'] != '63000' or info['InSTUnitCode'] != 'U0200':
        raise ValueError('Expected Taipei smallest statistical areas')
    records, missing = [], 0
    for row in root.findall('RowDataList/RowData'):
        source = {c.tag: c.text for c in row}
        match = re.fullmatch(r'(\d+)Y(\d+)M', source['INFO_TIME'] or '')
        if not match:
            raise ValueError(f'Unexpected statistical period: {source["INFO_TIME"]}')
        counts = [integer(source[f]) for f in AGE_FIELDS]
        record = {'base_area_id': source['CODEBASE'], 'level1_id': source['CODE1'], 'level2_id': source['CODE2'],
            'period': f'{int(match[1]) + 1911:04d}-{int(match[2]):02d}', 'age_counts': counts}
        if None in counts:
            missing += 1
            record['missing_values'] = {g: source[f] for g, f in zip(AGE_GROUPS, AGE_FIELDS) if integer(source[f]) is None}
        records.append(record)
    if len(records) != int(info['OutTotal']):
        raise ValueError('Row count differs from the declared OutTotal')
    if len({r['base_area_id'] for r in records}) != len(records):
        raise ValueError('Duplicate base_area_id')
    periods = sorted({r['period'] for r in records})
    period = {'start': periods[0], 'end': periods[-1]}
    manifest[RAW_NAME]['source_period'] = period

    # Cross-check against the separately published total population of the same areas.
    base = {r['base_area_id']: r for r in json.loads(BASE_POPULATION.read_text(encoding='utf-8'))['records']}
    unmatched = sorted({r['base_area_id'] for r in records} ^ set(base))
    parent_mismatch = sum(1 for r in records if r['base_area_id'] in base
        and (base[r['base_area_id']]['level1_id'], base[r['base_area_id']]['level2_id']) != (r['level1_id'], r['level2_id']))
    total_mismatch = [r['base_area_id'] for r in records if r['base_area_id'] in base and None not in r['age_counts']
        and sum(r['age_counts']) != base[r['base_area_id']]['population']]
    period_mismatch = sum(1 for r in records if r['base_area_id'] in base and base[r['base_area_id']]['period'] != r['period'])
    if unmatched or parent_mismatch or total_mismatch or period_mismatch:
        raise ValueError(f'Age data disagrees with base population: unmatched={unmatched[:5]} '
            f'parents={parent_mismatch} totals={total_mismatch[:5]} periods={period_mismatch}')
    group_totals = [sum(r['age_counts'][k] or 0 for r in records) for k in range(len(AGE_GROUPS))]
    return {'schema_version': 1, 'dataset_id': DATASET_ID, 'metadata': {
        'title': '臺北市最小統計區五歲年齡組人口',
        'sources': [manifest[RAW_NAME]],
        'source_period': period,
        'geographic_scope': '臺北市',
        'grain': 'period × base_area_id; 21 five-year age groups per row',
        'units': {'age_counts': 'persons'},
        'source_dataset_code': info['InMetaDatCode'], 'source_unit_code': info['InSTUnitCode'], 'county_id': info['InCountyId'],
        'age_counts_order': AGE_GROUPS,
        'age_counts_definition': 'Each entry counts registered residents in the inclusive age range; the last entry is 100 and older.',
        'source_fields': dict(zip(AGE_GROUPS, AGE_FIELDS)),
        'missing_value_policy': 'Unknown or suppressed values are null; original symbols are retained in missing_values.',
        'calendar': 'Source year is Republic of China; year = year_roc + 1911.',
        'limitations': LIMITATIONS,
        'validation': {'declared_row_count': int(info['OutTotal']), 'record_count': len(records),
            'rows_with_missing_values': missing, 'unique_key': ['period', 'base_area_id'],
            'matches_base_population_ids': True, 'matches_base_population_parents': True,
            'age_sum_equals_base_population': True, 'base_population_file': BASE_POPULATION.relative_to(ROOT).as_posix(),
            'city_total': sum(group_totals), 'age_group_totals': dict(zip(AGE_GROUPS, group_totals))},
    }, 'records': records}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--offline', action='store_true', help='Reuse the downloaded XML instead of fetching it again.')
    args = parser.parse_args()
    RAW.mkdir(parents=True, exist_ok=True)
    manifest_path = RAW / 'manifest.json'
    manifest = json.loads(manifest_path.read_text(encoding='utf-8')) if manifest_path.exists() else {}
    if args.offline:
        path = RAW / RAW_NAME
        if manifest.get(RAW_NAME, {}).get('url') != SOURCE_URL or not path.is_file():
            raise SystemExit(f'Offline source missing or URL differs: {RAW_NAME}')
    else:
        path = download(manifest)
    obj = normalize(path, manifest)
    write(manifest_path, manifest)
    write(OUTPUT, obj)
    write(RAW / 'delivery.json', {'group': 'population_age', 'datasets': [{
        'dataset_id': DATASET_ID, 'status': 'normalized', 'output_files': [OUTPUT.relative_to(ROOT).as_posix()],
        'record_count': len(obj['records']), 'period': obj['metadata']['source_period'],
        'limitations': LIMITATIONS}],
        'update_command': 'uv run scripts/update_population_age.py',
        'offline_command': 'uv run scripts/update_population_age.py --offline'})
    print(OUTPUT.name, len(obj['records']), obj['metadata']['validation']['city_total'], flush=True)


if __name__ == '__main__':
    main()
