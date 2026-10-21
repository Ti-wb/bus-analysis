#!/usr/bin/env python3
"""Acquire and normalize Taipei registered population and statistical areas."""

import argparse
from collections import Counter, defaultdict
import csv
from datetime import datetime
import io
import json
from pathlib import Path
import re
import subprocess
import xml.etree.ElementTree as ET
import zipfile
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup
from pyproj import CRS, Transformer
import shapefile
from shapely.geometry import shape, mapping
from shapely import transform

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / 'raw_data/population'
OUT = ROOT / 'data/population'
PAGES = {
    'taipei_population': 'https://data.taipei/dataset/detail?id=a6394e3f-3514-4542-87bd-de4310a40db3',
    'statistical_population': 'https://data.gov.tw/dataset/18681',
    'statistical_boundary': 'https://data.gov.tw/dataset/25128',
}


def now():
    return datetime.now(ZoneInfo('Asia/Taipei')).isoformat(timespec='seconds')


def write(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    part = path.with_suffix(path.suffix + '.part')
    with part.open('w', encoding='utf-8') as f:
        json.dump(obj, f, ensure_ascii=False, allow_nan=False, separators=(',', ':'))
        f.write('\n')
    part.replace(path)


class Downloads:
    def __init__(self, offline):
        self.offline = offline
        RAW.mkdir(parents=True, exist_ok=True)
        self.path = RAW / 'manifest.json'
        self.entries = json.loads(self.path.read_text()) if self.path.exists() else {}

    def fetch(self, url, name, source_page, source_updated_at=None):
        path = RAW / name
        old = self.entries.get(name)
        if self.offline:
            if not old or old.get('url') != url or not path.is_file() or not path.stat().st_size:
                raise ValueError(f'Offline source missing or source URL differs: {name}')
            # Parsing below verifies schemas, row counts and numerical consistency.
            return path
        part = path.with_suffix(path.suffix + '.download')
        headers = path.with_suffix(path.suffix + '.headers')
        proc = subprocess.run(['curl', '--fail', '--location', '--silent', '--show-error',
            '--proto', '=https', '--proto-redir', '=https', '--connect-timeout', '25',
            '--max-time', '240', '--retry', '2', '--retry-delay', '2', '--dump-header', str(headers),
            '--output', str(part), '--write-out', '%{json}', url], text=True, capture_output=True)
        if proc.returncode:
            part.unlink(missing_ok=True)
            raise RuntimeError(f'{name}: {proc.stderr.strip()}')
        info = json.loads(proc.stdout)
        hs = {}
        for line in headers.read_text(errors='replace').splitlines():
            if line.startswith('HTTP/'):
                hs = {}
            elif ':' in line:
                k, v = line.split(':', 1)
                hs[k.lower()] = v.strip()
        part.replace(path)
        self.entries[name] = {'page_url': source_page, 'url': url,
            'raw_file': path.relative_to(ROOT).as_posix(), 'downloaded_at': now(),
            'source_updated_at': source_updated_at, 'last_modified': hs.get('last-modified')}
        if info['url_effective'] != url:
            self.entries[name]['resolved_url'] = info['url_effective']
        headers.unlink(missing_ok=True)
        write(self.path, self.entries)
        print(name, path.stat().st_size, flush=True)
        return path


def acquire(d):
    resources = {'taipei': [], 'statistical': [], 'boundary': []}
    pages = {name: d.fetch(url, name + '_page.html', url) for name, url in PAGES.items()}
    soup = BeautifulSoup(pages['taipei_population'].read_text(), 'html.parser')
    for a in soup.select('a[href]'):
        if a.get_text(strip=True) != '下載':
            continue
        cells = a.find_parent('tr').find_all('td')
        title = cells[0].get_text(strip=True)
        # The active task is the latest three years. The current cumulative
        # resource supplies that window; archived 2021/2022 files are not fetched.
        if '迄今' not in title:
            continue
        source_updated_at = cells[3].get_text(strip=True)
        year = re.search(r'(\d+)年', title).group(1)
        name = f'taipei_population_{year}.csv'
        path = d.fetch(a['href'], name, PAGES['taipei_population'], source_updated_at)
        resources['taipei'].append((path, title))
    for key, field, suffix in [('statistical_population', 'statistical', 'xml'), ('statistical_boundary', 'boundary', 'zip')]:
        soup = BeautifulSoup(pages[key].read_text(), 'html.parser')
        for li in soup.select('li.resource-item'):
            title = li.get_text(' ', strip=True)
            if '臺北市' not in title:
                continue
            level = 'base' if '最小統計區' in title else 'level1' if '一級' in title else 'level2'
            name = f'{key}_{level}.{suffix}'
            path = d.fetch(li.find('a', href=True)['href'], name, PAGES[key])
            resources[field].append((path, title, level))
    return resources


MISSING = {'', '-', '--', '—', '…', '...', 'NA', 'N/A', '*'}
AGES = [str(age) for age in range(100)] + ['100_plus']


def integer(value):
    value = (value or '').strip()
    if value in MISSING:
        return None
    if not re.fullmatch(r'\d+', value):
        raise ValueError(f'Unexpected population count: {value!r}')
    return int(value)


def metadata(d, paths, grain, period, **extra):
    return {'title': extra.pop('title', None), 'sources': [d.entries[p.name] for p in paths],
        'source_period': period, 'geographic_scope': '臺北市', 'grain': grain,
        'missing_value_policy': 'Unknown, suppressed or unavailable values are null; original symbols are retained in missing_values.',
        **extra}


def check_unique(records, fields):
    values = [tuple(r.get(k) for k in fields) for r in records]
    if len(values) != len(set(values)):
        raise ValueError(f'Duplicate key at grain {fields}')


def normalize_taipei(d, inputs, months=36):
    """Preserve all demographic detail within the latest complete-month window."""
    available_periods = set()
    current = datetime.now(ZoneInfo('Asia/Taipei'))
    current_month = f'{current.year:04d}-{current.month:02d}'
    for path, _ in inputs:
        for raw in csv.DictReader(io.StringIO(path.read_text(encoding='cp950'))):
            period = f"{int(raw['年份']) + 1911:04d}-{int(raw['月份']):02d}"
            if period < current_month:
                available_periods.add(period)
    if not available_periods:
        raise ValueError('No completed source months available')
    latest = max(available_periods)
    latest_index = int(latest[:4]) * 12 + int(latest[5:]) - 1
    selected_periods = {f'{i // 12:04d}-{i % 12 + 1:02d}' for i in range(latest_index - months + 1, latest_index + 1)}
    missing_months = selected_periods - available_periods
    if missing_months:
        raise ValueError(f'Incomplete requested monthly window: {sorted(missing_months)}')
    records = []
    source_counts = []
    for path, title in inputs:
        encoding = 'cp950'
        text = path.read_text(encoding=encoding)
        reader = csv.DictReader(io.StringIO(text))
        age_fields = [f'{age}歲數量' for age in range(100)] + ['100歲以上']
        if reader.fieldnames != ['年份', '月份', '區域代碼', '區域別', '性別', '總計', *age_fields]:
            raise ValueError(f'Unexpected age CSV schema: {path}')
        source_periods = set()
        count = 0
        for line, raw in enumerate(reader, 2):
            year_roc, month = int(raw['年份']), int(raw['月份'])
            period = f'{year_roc + 1911:04d}-{month:02d}'
            if not 1 <= month <= 12:
                raise ValueError('Invalid month')
            source_periods.add(period)
            count += 1
            if period not in selected_periods:
                continue
            area_id = raw['區域代碼'].strip()
            level = 'city' if area_id == '63000000' else 'district' if len(area_id) == 8 else 'village' if len(area_id) == 11 else None
            if level is None or not area_id.startswith('63000') or not area_id.isdigit():
                raise ValueError(f'Unexpected area ID: {area_id}')
            ages = [integer(raw[field]) for field in age_fields]
            total = integer(raw['總計'])
            if all(v is not None for v in ages) and total is not None and sum(ages) != total:
                raise ValueError(f'Age sum differs from total: {path.name}:{line}')
            record = {'period': period, 'year': year_roc + 1911, 'year_roc': year_roc, 'month': month,
                'area_id': area_id, 'area_name': raw['區域別'].strip(), 'area_level': level,
                'district_id': area_id[:8] if level != 'city' else None,
                'sex': {'計': 'total', '男': 'male', '女': 'female'}[raw['性別']],
                'population': total, 'age_counts': ages, 'source_file': path.name, 'source_row': line}
            missing = {field: raw[field] for field in ['總計', *age_fields] if raw[field].strip() in MISSING}
            if missing:
                record['missing_values'] = missing
            records.append(record)
        periods = sorted(source_periods)
        d.entries[path.name]['source_period'] = {'start': periods[0], 'end': periods[-1]}
        d.entries[path.name]['source_encoding'] = encoding
        source_counts.append({'file': path.name, 'source_record_count': count, 'source_periods': periods,
            'selected_record_count': sum(r['source_file'] == path.name for r in records)})
    records.sort(key=lambda r: (r['period'], r['area_id'], r['sex']))
    check_unique(records, ['period', 'area_id', 'sex'])
    groups = defaultdict(dict)
    for r in records:
        groups[(r['period'], r['area_id'])][r['sex']] = r
    for key, sexes in groups.items():
        if set(sexes) != {'total', 'male', 'female'}:
            raise ValueError(f'Missing sex category: {key}')
        for field in ['population', 'age_counts']:
            total = sexes['total'][field]
            male, female = sexes['male'][field], sexes['female'][field]
            pairs = zip(total, male, female) if isinstance(total, list) else [(total, male, female)]
            for t, m, f in pairs:
                if None not in (t, m, f) and t != m + f:
                    raise ValueError(f'Sex reconciliation failed: {key}, {field}')
    periods = sorted({r['period'] for r in records})
    reconciliation = []
    for period in periods:
        rows = [r for r in records if r['period'] == period and r['sex'] == 'total']
        city = next(r['population'] for r in rows if r['area_level'] == 'city')
        district_sum = sum(r['population'] for r in rows if r['area_level'] == 'district')
        village_sum = sum(r['population'] for r in rows if r['area_level'] == 'village')
        if not city == district_sum == village_sum:
            raise ValueError(f'Geographic population reconciliation failed: {period}')
        reconciliation.append({'period': period, 'city_population': city, 'district_sum': district_sum,
            'village_sum': village_sum, 'village_count': sum(r['area_level'] == 'village' for r in rows)})
    period = {'start': periods[0], 'end': periods[-1], 'months': len(periods)}
    obj = {'schema_version': 1, 'dataset_id': 'taipei_village_population_age_monthly',
        'metadata': metadata(d, [p for p, _ in inputs], 'month × area_level × area_id × sex; 101 age bins per row', period,
            title='臺北市各里人口數按年齡分', units={'population': 'persons', 'age_counts': 'persons'},
            calendar='Source year is Republic of China; year = year_roc + 1911.',
            selection_scope={'rolling_complete_months': months, 'start': periods[0], 'end': periods[-1],
                'rule': 'Latest complete source month, then preceding consecutive months. Current calendar month is excluded.',
                'source_may_include_earlier_periods': True},
            age_counts_order=AGES, age_counts_definition='Index 0–99 is exact age in years; index 100 is age 100 and older.',
            sex_codes={'total': '計', 'male': '男', 'female': '女'},
            area_level_codes={'city': '全市總計', 'district': '區總計', 'village': '里'},
            limitations=['戶籍人口不等於白天活動人口或公車搭乘需求。', '全市、區、里是重疊的彙總層級；sex=total 與 male/female 亦不可相加。',
                '區域代碼保留原值；未以名稱與其他資料的 ID 自動配對。'],
            validation={'record_count': len(records), 'unique_key': ['period', 'area_id', 'sex'],
                'age_sum_equals_population': True, 'male_plus_female_equals_total_all_ages': True,
                'city_equals_district_sum_equals_village_sum_all_months': True}), 'records': records}
    return obj, {'sources': source_counts, 'monthly_reconciliation': reconciliation}


def normalize_statistical(d, inputs):
    datasets = {}
    field_map = {'INFO_TIME': 'source_period_code', 'CODE2': 'level2_id', 'CODE1': 'level1_id',
        'CODEBASE': 'base_area_id', 'H_CNT': 'households', 'P_CNT': 'population',
        'M_CNT': 'male_population', 'F_CNT': 'female_population'}
    for path, title, level in inputs:
        root = ET.parse(path).getroot()
        if root.tag != 'Result':
            raise ValueError('Expected statistical population Result XML')
        info = {e.tag: e.text for e in root.find('Info')}
        columns = [{field_map[c.findtext('COLUMN_NAME')]: {
            'source_field': c.findtext('COLUMN_NAME'), 'description': c.findtext('COLUMN_DESC'),
            'unit': c.findtext('DISPLAY_UNIT') or None, 'source_type': c.findtext('DATA_TYPE')}}
            for c in root.findall('ColumnList/Column')]
        records = []
        for row_index, row in enumerate(root.findall('RowDataList/RowData'), 1):
            source = {c.tag: c.text for c in row}
            record = {field_map[k]: integer(v) if k.endswith('_CNT') else v for k, v in source.items()}
            match = re.fullmatch(r'(\d+)Y(\d+)M', record['source_period_code'])
            if not match:
                raise ValueError(f'Unexpected statistical period: {record["source_period_code"]}')
            record['period'] = f'{int(match[1]) + 1911:04d}-{int(match[2]):02d}'
            record['source_row'] = row_index
            missing = {field_map[k]: v for k, v in source.items() if k.endswith('_CNT') and (v or '').strip() in MISSING}
            if missing:
                record['missing_values'] = missing
            if all(record[k] is not None for k in ['population', 'male_population', 'female_population']):
                if record['population'] != record['male_population'] + record['female_population']:
                    raise ValueError('Statistical sex totals disagree')
            records.append(record)
        if len(records) != int(info['OutTotal']) or info['InCountyId'] != '63000':
            raise ValueError('Statistical row count or county mismatch')
        key = {'base': 'base_area_id', 'level1': 'level1_id', 'level2': 'level2_id'}[level]
        check_unique(records, ['period', key])
        periods = sorted({r['period'] for r in records})
        d.entries[path.name]['source_period'] = {'start': periods[0], 'end': periods[-1]}
        datasets[level] = {'schema_version': 1, 'dataset_id': f'taipei_statistical_population_{level}',
            'metadata': metadata(d, [path], f'period × {key}', {'start': periods[0], 'end': periods[-1]},
                title={'base': '臺北市最小統計區人口', 'level1': '臺北市一級發布區人口', 'level2': '臺北市二級發布區人口'}[level],
                units={'households': 'households', 'population': 'persons', 'male_population': 'persons', 'female_population': 'persons'},
                source_dataset_code=info['InMetaDatCode'], source_unit_code=info['InSTUnitCode'], county_id=info['InCountyId'],
                source_columns={k: v for c in columns for k, v in c.items()},
                limitations=['發布區人口為戶籍人口，不代表乘車需求。', '各發布層級是重疊彙總，三份資料不可加總。',
                    '官方此服務只回傳目前供應的資料期間；不以下載年份假冒統計年份。'],
                validation={'declared_row_count': int(info['OutTotal']), 'record_count': len(records),
                    'unique_key': ['period', key], 'male_plus_female_equals_total': True}), 'records': records}
    return datasets


def normalize_boundary(d, inputs):
    if len(inputs) != 1:
        raise ValueError('Expected one Taipei statistical boundary archive')
    path, title, level = inputs[0]
    directory = RAW / 'statistical_boundary_base_extracted'
    directory.mkdir(exist_ok=True)
    with zipfile.ZipFile(path) as z:
        for member in z.namelist():
            target = (directory / member).resolve()
            if not target.is_relative_to(directory.resolve()):
                raise ValueError('Unsafe archive member')
            z.extract(member, directory)
    shp_files = list(directory.glob('*.shp'))
    if len(shp_files) != 1:
        raise ValueError('Expected one shapefile')
    shp_path = shp_files[0]
    prj = shp_path.with_suffix('.prj')
    cpg = shp_path.with_suffix('.cpg')
    crs = CRS.from_wkt(prj.read_text())
    if crs.to_epsg() != 3826:
        raise ValueError(f'Unexpected CRS: {crs}')
    source_encoding = cpg.read_text().strip()
    if source_encoding.lower() not in {'big5', 'cp950'}:
        raise ValueError(f'Unexpected shapefile encoding: {source_encoding}')
    transformer = Transformer.from_crs(crs, 'EPSG:4326', always_xy=True)
    reader = shapefile.Reader(str(shp_path), encoding='big5')
    features = []
    area_error = []
    for row_number, sr in enumerate(reader.iterShapeRecords(), 1):
        raw = sr.record.as_dict()
        source_geometry = shape(sr.shape.__geo_interface__)
        geometry = transform(source_geometry, transformer.transform, interleaved=False)
        if geometry.is_empty or not geometry.is_valid or geometry.geom_type not in {'Polygon', 'MultiPolygon'}:
            raise ValueError(f'Invalid statistical area geometry: {raw["CODEBASE"]}')
        xmin, ymin, xmax, ymax = geometry.bounds
        if not (121 < xmin <= xmax < 122 and 24 < ymin <= ymax < 26):
            raise ValueError('Transformed boundary not in Taipei bounds')
        if raw['COUNTY_ID'] != '63000' or raw['COUNTY'] != '臺北市':
            raise ValueError('Boundary includes another county')
        props = {'source_uid': str(raw['U_ID']), 'base_area_id': raw['CODEBASE'], 'level1_id': raw['CODE1'],
            'level2_id': raw['CODE2'], 'district_id': raw['TOWN_ID'], 'district_name': raw['TOWN'],
            'county_id': raw['COUNTY_ID'], 'county_name': raw['COUNTY'],
            'source_x_m': raw['X'], 'source_y_m': raw['Y'], 'source_area_m2': raw['AREA'], 'source_row': row_number}
        area_error.append(abs(raw['AREA'] - source_geometry.area))
        features.append({'type': 'Feature', 'id': raw['CODEBASE'], 'properties': props, 'geometry': mapping(geometry)})
    check_unique([f['properties'] for f in features], ['base_area_id'])
    d.entries[path.name]['source_period'] = None
    d.entries[path.name]['boundary_vintage_in_filename'] = '2015'
    files = [{'raw_file': p.relative_to(ROOT).as_posix()}
        for p in sorted(directory.iterdir()) if p.is_file()]
    meta = metadata(d, [path], 'one polygon per base_area_id', None,
        title='臺北市最小統計區圖', crs='EPSG:4326', coordinate_order=['longitude', 'latitude'],
        source_crs='EPSG:3826', crs_evidence=prj.relative_to(ROOT).as_posix(), source_encoding=source_encoding,
        boundary_vintage_year_in_filename=2015,
        boundary_vintage_note='SHP basename G97_63000_U0200_2015 identifies 2015. Source gives no separate validity date; source_period stays null.',
        units={'source_x_m': 'metres in EPSG:3826', 'source_y_m': 'metres in EPSG:3826', 'source_area_m2': 'square metres'},
        source_area_unit_evidence='AREA agrees with area calculated in projected metres; maximum absolute difference is recorded in validation.',
        extracted_files=files,
        limitations=['邊界檔名標示 2015，不代表 2026 年行政里界。', '統計區代碼與行政里代碼不同，不以名稱或碼前綴直接關聯。',
            '統計區人口為 2024-12，邊界版本不同；即使 ID 全數相符，仍應在分析結果標示兩者的版本。'],
        validation={'feature_count': len(features), 'unique_base_area_ids': True, 'valid_geometry_count': len(features),
            'source_area_max_absolute_difference_m2': max(area_error)})
    return {'type': 'FeatureCollection', 'schema_version': 1, 'dataset_id': 'taipei_statistical_area_boundaries',
        'metadata': meta, 'features': features}


def cross_checks(taipei, statistical, boundaries):
    features = {f['properties']['base_area_id']: f['properties'] for f in boundaries['features']}
    base = statistical['base']['records']
    if set(r['base_area_id'] for r in base) != set(features):
        raise ValueError('Statistical population and boundary keys differ')
    for r in base:
        f = features[r['base_area_id']]
        if any(r[k] != f[k] for k in ['level1_id', 'level2_id']):
            raise ValueError('Population/boundary parent keys differ')
    rollup_checks = []
    measures = ['population', 'male_population', 'female_population', 'households']
    for child_level, parent_level, id_field in [('base', 'level1', 'level1_id'), ('level1', 'level2', 'level2_id')]:
        rollups = defaultdict(lambda: {m: 0 for m in measures})
        for r in statistical[child_level]['records']:
            for m in measures:
                if r[m] is None:
                    raise ValueError('Cannot verify statistical rollup with missing values')
                rollups[(r['period'], r[id_field])][m] += r[m]
        parent = {(r['period'], r[id_field]): r for r in statistical[parent_level]['records']}
        if set(rollups) != set(parent):
            raise ValueError('Statistical parent membership differs')
        for key, totals in rollups.items():
            if any(totals[m] != parent[key][m] for m in measures):
                raise ValueError(f'Statistical rollup mismatch: {key}')
        rollup_checks.append({'child': child_level, 'parent': parent_level, 'parent_count': len(parent), 'all_measures_match': True})
    city = {r['period']: r['population'] for r in taipei['records'] if r['area_level'] == 'city' and r['sex'] == 'total'}
    totals = defaultdict(int)
    for r in base:
        totals[r['period']] += r['population']
    comparisons = [{'period': period, 'statistical_population': count, 'monthly_city_population': city.get(period),
        'difference': count - city[period] if period in city else None} for period, count in totals.items()]
    return {'boundary_population_exact_key_matches': len(base), 'boundary_parent_keys_match': True,
        'statistical_rollups': rollup_checks, 'independent_monthly_city_comparison': comparisons,
        'note': 'A numeric difference across official releases is preserved, not overwritten or backfilled.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--offline', action='store_true')
    parser.add_argument('--download-only', action='store_true')
    parser.add_argument('--months', type=int, default=36,
        help='Number of latest consecutive complete months to normalize (default: 36).')
    args = parser.parse_args()
    if args.months < 1:
        parser.error('--months must be positive')
    d = Downloads(args.offline)
    resources = acquire(d)
    if args.download_only:
        return
    taipei, monthly_quality = normalize_taipei(d, resources['taipei'], args.months)
    statistical = normalize_statistical(d, resources['statistical'])
    boundaries = normalize_boundary(d, resources['boundary'])
    checks = cross_checks(taipei, statistical, boundaries)
    for comparison in checks['independent_monthly_city_comparison']:
        if comparison['difference']:
            warning = (f"{comparison['period']} 統計區人口合計 {comparison['statistical_population']} 人，"
                f"民政局全市月人口 {comparison['monthly_city_population']} 人，相差 {comparison['difference']} 人。"
                '兩個官方來源皆保留原數值；差異原因尚未確認，不混用為同一基準。')
            taipei['metadata']['limitations'].append(warning)
            for obj in statistical.values():
                obj['metadata']['limitations'].append(warning)
    for obj in [taipei, *statistical.values(), boundaries]:
        obj['metadata']['quality_report'] = 'raw_data/population/quality_report.json'
    write(d.path, d.entries)
    outputs = {'taipei_village_population_age_monthly.json': taipei,
        **{f'taipei_statistical_population_{level}.json': obj for level, obj in statistical.items()},
        'taipei_statistical_area_boundaries.geojson': boundaries}
    catalog = []
    for name, obj in outputs.items():
        path = OUT / name
        write(path, obj)
        count = len(obj.get('records', obj.get('features', [])))
        catalog.append({'dataset_id': obj['dataset_id'], 'status': 'normalized', 'output_files': [path.relative_to(ROOT).as_posix()],
            'record_count': count, 'period': obj['metadata']['source_period'],
            'limitations': obj['metadata']['limitations']})
        print(name, count, flush=True)
    quality = {'schema_version': 1, 'dataset_id': 'population_quality_report', 'checked_at': now(),
        'monthly_population': monthly_quality, 'cross_source_checks': checks,
        'independent_review_status': 'pending_review_by_separate_agent'}
    write(RAW / 'quality_report.json', quality)
    write(RAW / 'delivery.json', {'group': 'population', 'datasets': catalog,
        'active_scope': taipei['metadata']['selection_scope'],
        'excluded_historical_source_references': 'raw_data/population/historical_references.json',
        'independent_review_status': 'pending', 'quality_report': 'raw_data/population/quality_report.json',
        'update_command': 'uv run scripts/update_population.py',
        'offline_command': 'uv run scripts/update_population.py --offline'})
    write(OUT / 'catalog.json', {'schema_version': 1, 'dataset_id': 'population_catalog',
        'metadata': {'sources': list(d.entries.values()), 'cross_source_checks': checks,
            'active_scope': taipei['metadata']['selection_scope'],
            'excluded_historical_source_references': 'raw_data/population/historical_references.json'}, 'records': catalog})


if __name__ == '__main__':
    main()
