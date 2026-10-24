#!/usr/bin/env python3
"""Independent raw-row review of population, village boundaries and route masters.

This reviewer imports no production collector, parser, normalizer, or matcher.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import csv
from datetime import datetime
from io import BytesIO
import json
from pathlib import Path
import re
from zipfile import ZipFile
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
CITIES = {'63000': '臺北市', '65000': '新北市', '10017': '基隆市'}
ROUTE_KEYS = {
    'AuthorityID': 'authority_id', 'BusRouteType': 'bus_route_type', 'City': 'city', 'CityCode': 'city_code',
    'DepartureStopNameEn': 'departure_stop_name_en', 'DepartureStopNameZh': 'departure_stop_name_zh',
    'DestinationStopNameEn': 'destination_stop_name_en', 'DestinationStopNameZh': 'destination_stop_name_zh',
    'Direction': 'direction', 'En': 'en', 'FareBufferZoneDescriptionEn': 'fare_buffer_zone_description_en',
    'FareBufferZoneDescriptionZh': 'fare_buffer_zone_description_zh', 'FirstBusTime': 'first_bus_time',
    'HasSubRoutes': 'has_sub_routes', 'Headsign': 'headsign', 'HeadsignEn': 'headsign_en',
    'HolidayFirstBusTime': 'holiday_first_bus_time', 'HolidayLastBusTime': 'holiday_last_bus_time',
    'LastBusTime': 'last_bus_time', 'OperatorCode': 'operator_code', 'OperatorID': 'operator_id',
    'OperatorIDs': 'operator_i_ds', 'OperatorName': 'operator_name', 'OperatorNo': 'operator_no',
    'Operators': 'operators', 'ProviderID': 'provider_id', 'RouteID': 'route_id',
    'RouteMapImageUrl': 'route_map_image_url', 'RouteName': 'route_name', 'RouteUID': 'route_uid',
    'SubRouteID': 'sub_route_id', 'SubRouteName': 'sub_route_name', 'SubRouteUID': 'sub_route_uid',
    'SubRoutes': 'sub_routes', 'TicketPriceDescriptionEn': 'ticket_price_description_en',
    'TicketPriceDescriptionZh': 'ticket_price_description_zh', 'Zh_tw': 'zh_tw',
}


def read(root, path):
    return json.loads((root / path).read_text(encoding='utf-8'))


def write(root, path, obj):
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(obj, ensure_ascii=False, allow_nan=False, indent=2) + '\n', encoding='utf-8')


def check(checks, check_id, passed, **details):
    checks.append({'check_id': check_id, 'status': 'passed' if passed else 'failed', **details})


def route_values_equal(raw, output, source_key=''):
    """Explicit field map derived from the observed official Route schema."""
    if isinstance(raw, dict):
        if not isinstance(output, dict) or set(output) != {ROUTE_KEYS[k] for k in raw}:
            return False
        return all(route_values_equal(value, output[ROUTE_KEYS[key]], key) for key, value in raw.items())
    if isinstance(raw, list):
        return isinstance(output, list) and len(raw) == len(output) and all(route_values_equal(a, b, source_key) for a, b in zip(raw, output))
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return output is None
    if source_key.endswith(('ID', 'UID', 'Code')):
        return output == str(raw) and isinstance(output, str)
    return type(raw) is type(output) and raw == output


def review(root):
    from bs4 import BeautifulSoup
    import shapefile
    from pyproj import CRS, Transformer
    from shapely.geometry import shape
    from shapely.ops import transform as transform_geometry

    checks = []
    map_checks = []
    population = read(root, 'data/official_cross_city/population_2026-09.json')
    pop_source = population['metadata']['source']
    csv_path = root / pop_source['raw_file']
    receipt = read(root, str(Path(pop_source['raw_file']).with_suffix('.csv.source.json')))
    check(checks, 'population_source_receipt', csv_path.stat().st_size == receipt['bytes'] == pop_source['bytes']
          and receipt['http_status'] == 200 and receipt['requested_url'] == pop_source['requested_url'],
          raw_file=pop_source['raw_file'], source_url=pop_source['requested_url'], bytes=csv_path.stat().st_size)
    pop_catalog_path = root / 'raw_data/official_cross_city/moi_catalog_2026-09.html'
    pop_catalog_receipt = read(root, 'raw_data/official_cross_city/moi_catalog_2026-09.html.source.json')
    pop_catalog = BeautifulSoup(pop_catalog_path.read_text(encoding='utf-8'), 'html.parser')
    month_links = [a for a in pop_catalog.find_all('a', href=pop_source['requested_url'])
                   if a.find_parent('li') and '11509-村里戶數、單一年齡人口' in a.find_parent('li').get_text()]
    check(checks, 'population_official_catalog_exact_month_resource', len(month_links) == 1
          and pop_catalog_receipt['page_url'] == 'https://data.gov.tw/dataset/77132'
          and pop_catalog_path.stat().st_size == pop_catalog_receipt['bytes'],
          advertised_month='11509', advertised_resource_url=pop_source['requested_url'],
          exact_month_resource_match_count=len(month_links))
    with csv_path.open(encoding='utf-8-sig', newline='') as handle:
        raw_rows = list(csv.DictReader(handle))
    selected = [(number, row) for number, row in enumerate(raw_rows, 2)
                if any(row['區域別'].startswith(name) for name in CITIES.values())]
    expected_pop = {}
    totals = defaultdict(Counter)
    for number, row in selected:
        code = row['區域別代碼']
        if code in expected_pop:
            raise ValueError('Duplicate population source code')
        values = {
            'period': f"{int(row['統計年月'][:3]) + 1911}-{row['統計年月'][3:]}",
            'county_code': code[:5], 'county_name': row['區域別'][:3],
            'source_area_code': code, 'area_name': row['區域別'], 'village_name': row['村里'],
            'households': int(row['戶數']), 'population': int(row['人口數']),
            'population_male': int(row['人口數-男']), 'population_female': int(row['人口數-女']),
            'population_age_0_14': sum(int(row[f'{age}歲-{sex}']) for age in range(15) for sex in ['男', '女']),
            'population_age_15_64': sum(int(row[f'{age}歲-{sex}']) for age in range(15, 65) for sex in ['男', '女']),
            'population_age_65_plus': sum(int(row[f'{age}歲-{sex}']) for age in range(65, 100) for sex in ['男', '女'])
                                      + int(row['100歲以上-男']) + int(row['100歲以上-女']),
            'source_row_number': number, 'boundary_join_status': 'not_verified',
        }
        expected_pop[code] = values
        for field in ['households', 'population', 'population_male', 'population_female',
                      'population_age_0_14', 'population_age_15_64', 'population_age_65_plus']:
            totals[values['county_name']][field] += values[field]
        if values['population'] != values['population_male'] + values['population_female']:
            raise ValueError('Source population sex total mismatch')
        if values['population'] != sum(values[f'population_age_{x}'] for x in ['0_14', '15_64', '65_plus']):
            raise ValueError('Source population age total mismatch')
    actual_pop = {r['source_area_code']: r for r in population['records']}
    differences = [code for code in expected_pop if expected_pop[code] != actual_pop.get(code)]
    check(checks, 'population_all_rows_and_2026_09_age_sex_totals', len(actual_pop) == len(population['records'])
          and set(actual_pop) == set(expected_pop) and not differences and {r['period'] for r in expected_pop.values()} == {'2026-09'},
          national_raw_row_count=len(raw_rows), selected_row_count=len(selected), output_row_count=len(population['records']),
          mismatch_codes=differences[:10], county_row_counts=dict(Counter(r['county_name'] for r in expected_pop.values())),
          county_totals={key: dict(value) for key, value in totals.items()})

    manifest = read(root, 'map-data/raw/validation/manifest.json')
    check(map_checks, 'official_boundary_source_receipts', all((root / s['raw_file']).stat().st_size == s['bytes']
          and s['http_status'] == 200 for s in manifest['records']), source_files=[s['raw_file'] for s in manifest['records']])
    boundary_page = BeautifulSoup((root / 'map-data/raw/validation/villages_page.html').read_text(encoding='utf-8'), 'html.parser')
    archive_receipt = next(s for s in manifest['records'] if s['raw_file'].endswith('.zip'))
    check(map_checks, 'national_boundary_official_catalog_resource', bool(boundary_page.find_all('a', href=archive_receipt['download_url']))
          and archive_receipt['source_page'] == 'https://data.gov.tw/dataset/7438',
          advertised_resource_url=archive_receipt['download_url'])
    with ZipFile(root / 'map-data/raw/validation/villages.zip') as archive:
        stems = [name[:-4] for name in archive.namelist() if re.fullmatch(r'VILLAGE_NLSC_\d{7}\.shp', name)]
        if len(stems) != 1:
            raise ValueError('Ambiguous official national shapefile')
        stem = stems[0]
        version_digits = stem[-7:]
        version = f'{int(version_digits[:3]) + 1911}-{version_digits[3:5]}-{version_digits[5:]}'
        crs = CRS.from_wkt(archive.read(stem + '.prj').decode('utf-8'))
        converter = Transformer.from_crs(crs, CRS.from_epsg(4326), always_xy=True)
        reader = shapefile.Reader(shp=BytesIO(archive.read(stem + '.shp')), shx=BytesIO(archive.read(stem + '.shx')),
                                  dbf=BytesIO(archive.read(stem + '.dbf')), encoding='utf-8')
        national_count = len(reader)
        boundaries = {}
        keelung_source = {}
        for sr in reader.iterShapeRecords():
            row = sr.record.as_dict()
            if row['COUNTYCODE'] not in CITIES:
                continue
            code = row['VILLCODE']
            if code in boundaries:
                raise ValueError('Duplicate official village code')
            boundaries[code] = {'village_code': code, 'county_code': row['COUNTYCODE'], 'county_name': row['COUNTYNAME'],
                                'district_code': row['TOWNCODE'], 'district_name': row['TOWNNAME'],
                                'village_name': row['VILLNAME'] or None, 'source_note': row['NOTE'] or None}
            if row['COUNTYCODE'] == '10017':
                keelung_source[code] = transform_geometry(converter.transform, shape(sr.shape.__geo_interface__))
    crosswalk = read(root, 'data/official_cross_city/population_boundary_crosswalk.json')
    expected_crosswalk = {}
    variants = []
    for code, p in expected_pop.items():
        b = boundaries.get(code)
        if b is None:
            raise ValueError('Population source code missing from national boundary source: ' + code)
        if b['county_name'] + b['district_name'] != p['area_name'] or b['county_code'] != p['county_code']:
            raise ValueError('Same-code population and boundary hierarchy mismatch')
        status = 'matched_code_and_names' if b['village_name'] == p['village_name'] else 'matched_code_name_variant_requires_review'
        expected_crosswalk[code] = {'population_area_code': code, 'boundary_village_code': code,
            'county_code': p['county_code'], 'population_area_name': p['area_name'],
            'population_village_name': p['village_name'], 'boundary_village_name': b['village_name'],
            'population_period': p['period'], 'boundary_source_period': version,
            'population': p['population'], 'population_source_row_number': p['source_row_number'],
            'status': status, 'same_month_geometry_verified': False}
        if status != 'matched_code_and_names':
            variants.append({'area_code': code, 'population_name': p['village_name'], 'boundary_name': b['village_name']})
    actual_crosswalk = {r['population_area_code']: r for r in crosswalk['records']}
    extras = {code: row for code, row in boundaries.items() if code not in expected_pop}
    actual_extras = {r['village_code']: r for r in crosswalk['boundary_only_records']}
    check(checks, 'population_to_official_boundary_all_exact_codes_and_hierarchy', actual_crosswalk == expected_crosswalk
          and len(actual_crosswalk) == len(crosswalk['records']) and extras == actual_extras,
          population_row_count=len(expected_pop), regional_boundary_row_count=len(boundaries),
          boundary_only_count=len(extras), boundary_version=version,
          variant_count=len(variants), name_variants=variants,
          boundary_only_classification=dict(Counter(r['source_note'] for r in extras.values())),
          same_month_geometry_verified=False)

    geo = read(root, 'map-data/admin/keelung_villages.geojson')
    features = {f['id']: f for f in geo['features']}
    errors = []
    max_hausdorff = 0
    bounds = [180.0, 90.0, -180.0, -90.0]
    for code, source_geom in keelung_source.items():
        f = features.get(code)
        if f is None or f['properties'] != boundaries[code]:
            errors.append(code + ':properties_or_missing')
            continue
        output_geom = shape(f['geometry'])
        distance = source_geom.hausdorff_distance(output_geom)
        max_hausdorff = max(max_hausdorff, distance)
        b = output_geom.bounds
        if not source_geom.is_valid or not output_geom.is_valid or output_geom.is_empty or output_geom.geom_type not in {'Polygon', 'MultiPolygon'}:
            errors.append(code + ':invalid_geometry')
        if not (121 < b[0] <= b[2] < 123 and 24 < b[1] <= b[3] < 26) or distance > 1e-9:
            errors.append(code + ':coordinate_or_source_geometry_difference')
        bounds = [min(bounds[0], b[0]), min(bounds[1], b[1]), max(bounds[2], b[2]), max(bounds[3], b[3])]
    check(map_checks, 'keelung_source_crs_and_all_geometry_coordinates', not errors and set(features) == set(keelung_source)
          and len(features) == len(geo['features']) and crs.to_epsg() == 3824 and geo['metadata']['crs'] == 'EPSG:4326'
          and geo['metadata']['coordinate_order'] == ['longitude', 'latitude'] and geo['metadata']['source_period'] == version,
          source_crs_epsg=crs.to_epsg(), output_crs_epsg=4326, national_raw_feature_count=national_count,
          source_shapefile=stem + '.shp', boundary_version=version, keelung_feature_count=len(features),
          max_source_output_hausdorff_distance_degrees=max_hausdorff, bounding_box=bounds,
          unassigned_feature_count=sum(f['properties']['source_note'] == '未編定村里' for f in features.values()),
          mismatches=errors[:10])

    for city in ['NewTaipei', 'Keelung', 'InterCity']:
        output = read(root, f'data/official_cross_city/regional_routes/{city}.json')
        metadata = output['metadata']; source = metadata['sources'][0]
        raw = read(root, source['raw_file'])
        original = raw['BusRoutes']
        actual = {r['route_uid']: r for r in output['records']}
        source_ids = [r['RouteUID'] for r in original]
        mismatches = [r['RouteUID'] for r in original if not route_values_equal(r, actual.get(r['RouteUID']))]
        expected_url_path = '/InterCity?' if city == 'InterCity' else '/City/' + city + '?'
        check(checks, 'tdx_full_route_master_' + city, raw['Count'] == len(original) == len(actual) == len(output['records'])
              and set(actual) == set(source_ids) and len(set(source_ids)) == len(source_ids) and not mismatches
              and metadata['requested_date'] == '2026-10-23'
              and metadata['version_start'] == raw['StartDate'] and metadata['version_end'] == raw['EndDate']
              and metadata['source_version_id'] == str(raw['VersionID'])
              and expected_url_path in source['download_url'] and '$top=100000' in source['download_url'].replace('%24', '$')
              and (root / source['raw_file']).stat().st_size == source['bytes'],
              source_url=source['download_url'], raw_file=source['raw_file'],
              raw_count=raw['Count'], output_count=len(actual),
              nested_subroute_count=sum(len(r.get('SubRoutes', [])) for r in original),
              nested_operator_count=sum(len(r.get('Operators', [])) for r in original),
              source_version_start=raw['StartDate'], source_version_end=raw['EndDate'],
              field_level_mismatch_route_uids=mismatches[:10], geographic_crop_applied=False)
    moment = datetime.now(ZoneInfo('Asia/Taipei')).isoformat(timespec='seconds')
    shared = {'reviewed_at': moment, 'reviewer': 'independent_agent_travel_time',
              'method': 'independent CSV/DBF/ZIP and raw API row comparison; no producer parser/normalizer/matcher imported',
              'source_updated_at': None,
              'limitations': ['人口月份與里界版本不同；未宣稱相同月份幾何相等。',
                             '六筆里名異體字/造字差異仍為requires_review，不自動改名。',
                             '公路客運保留全國主檔；完整主檔不代表完整站序/班表或北北基營運普查。']}
    for path, dataset, rows in [('data/quality/regional_validation_review.json', 'regional_validation_independent_review', checks),
                                ('map-data/quality/keelung_review.json', 'keelung_boundary_independent_review', map_checks)]:
        write(root, path, {'schema_version': 1, 'dataset_id': dataset,
                          'metadata': {**shared, 'overall_status': 'passed_with_explicit_limitations' if all(r['status'] == 'passed' for r in rows) else 'failed'},
                          'records': rows})
    failed = [r['check_id'] for r in checks + map_checks if r['status'] != 'passed']
    print(json.dumps({'checks': len(checks) + len(map_checks), 'failed': failed, 'county_totals': {k: dict(v) for k, v in totals.items()}}, ensure_ascii=False))
    return bool(failed)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=ROOT)
    raise SystemExit(review(parser.parse_args().root))
