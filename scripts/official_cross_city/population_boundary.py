"""Compare one population month against the actual official NLSC village vintage."""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime
from io import BytesIO
import json
from pathlib import Path
import re
from urllib.parse import quote
from urllib.request import Request, build_opener
from zipfile import ZipFile

from .common import ROOT, SafeRedirect, now, validate_month, write_json

PAGE = 'https://data.gov.tw/dataset/7438'
URL = 'https://www.tgos.tw/tgos/VirtualDir/Product/a04697c8-64db-450a-a105-3eb471c45abd/村(里)界(TWD97經緯度).zip'
CITIES = {'63000': '臺北市', '65000': '新北市', '10017': '基隆市'}


def match(population, boundaries, version):
    by_code = {}
    for b in boundaries:
        code = b['village_code']
        if code in by_code:
            raise ValueError('Duplicate official boundary code')
        by_code[code] = b
    rows, used = [], set()
    for p in population['records']:
        code = p['source_area_code']
        if code in used:
            raise ValueError('Duplicate population code')
        used.add(code)
        b = by_code.get(code)
        if b and (b['county_code'] != p['county_code'] or b['county_name'] != p['county_name']
                  or b['county_name'] + b['district_name'] != p['area_name']):
            raise ValueError('Official code matches but administrative hierarchy conflicts')
        status = ('missing_boundary' if b is None else 'matched_code_and_names' if b['village_name'] == p['village_name']
                  else 'matched_code_name_variant_requires_review')
        rows.append({'population_area_code': code, 'boundary_village_code': code if b else None,
                     'county_code': p['county_code'], 'population_area_name': p['area_name'],
                     'population_village_name': p['village_name'],
                     'boundary_village_name': b['village_name'] if b else None,
                     'population_period': p['period'], 'boundary_source_period': version,
                     'population': p['population'], 'population_source_row_number': p['source_row_number'],
                     'status': status, 'same_month_geometry_verified': False})
    extras = [b for code, b in by_code.items() if code not in used]
    return rows, extras


def download(root):
    raw = root / 'map-data/raw/validation'
    raw.mkdir(parents=True, exist_ok=True)
    opener = build_opener(SafeRedirect({'data.gov.tw', 'www.tgos.tw'}))
    entries = []
    for url, name, limit in [(PAGE, 'villages_page.html', 5 * 1024**2), (URL, 'villages.zip', 300 * 1024**2)]:
        target = raw / name
        part = target.with_suffix('.part')
        try:
            req = Request(url if name.endswith('html') else quote(url, safe=':/()'),
                          headers={'Accept-Encoding': 'identity', 'User-Agent': 'bus-analysis-official-data/1.0'})
            with opener.open(req, timeout=180) as response:
                if response.status != 200:
                    raise ValueError('Expected complete official source')
                size = 0
                with part.open('wb') as f:
                    while block := response.read(1024**2):
                        size += len(block)
                        if size > limit:
                            raise ValueError('Official boundary source exceeds budget')
                        f.write(block)
                declared = response.headers.get('Content-Length')
                if not size or (declared and int(declared) != size):
                    raise ValueError('Truncated official source')
                entries.append({'source_page': PAGE, 'download_url': url,
                                'raw_file': target.relative_to(root).as_posix(), 'bytes': size,
                                'downloaded_at': now(), 'source_updated_at': None,
                                'http_status': response.status})
            part.replace(target)
        finally:
            part.unlink(missing_ok=True)
    # Confirm that the current official catalog still advertises this archive.
    from bs4 import BeautifulSoup
    links = [a['href'] for a in BeautifulSoup((raw / 'villages_page.html').read_text(encoding='utf-8'), 'html.parser').select('li.resource-item a[href]')]
    if URL not in links:
        raise ValueError('Current official catalog does not advertise this archive')
    manifest = {'schema_version': 1, 'dataset_id': 'population_boundary_sources', 'metadata': {}, 'records': entries}
    write_json(raw / 'manifest.json', manifest)


def build(root, month):
    import shapefile
    from pyproj import CRS, Transformer
    from shapely import make_valid, orient_polygons, transform
    from shapely.geometry import shape, mapping

    raw = root / 'map-data/raw/validation'
    manifest = json.loads((raw / 'manifest.json').read_text(encoding='utf-8'))
    for source in manifest['records']:
        if (root / source['raw_file']).stat().st_size != source['bytes']:
            raise ValueError('Boundary source receipt/size mismatch')
    population = json.loads((root / f'data/official_cross_city/population_{month}.json').read_text(encoding='utf-8'))
    if population['metadata']['period'] != month:
        raise ValueError('Population month differs from requested month')
    with ZipFile(raw / 'villages.zip') as z:
        mains = [n[:-4] for n in z.namelist() if re.fullmatch(r'VILLAGE_NLSC_\d{7}\.shp', n, re.I)]
        if len(mains) != 1:
            raise ValueError('Expected one national official village shapefile')
        stem = mains[0]
        names = {Path(n).suffix.lower(): n for n in z.namelist() if n.lower().startswith(stem.lower() + '.')}
        version = re.search(r'(\d{3})(\d{2})(\d{2})$', stem)
        source_period = datetime(int(version[1]) + 1911, int(version[2]), int(version[3])).date().isoformat()
        crs = CRS.from_wkt(z.read(names['.prj']).decode('utf-8'))
        if crs.to_epsg() != 3824:
            raise ValueError('Expected official TWD97 geographic coordinates')
        converter = Transformer.from_crs(crs, 4326, always_xy=True)
        reader = shapefile.Reader(shp=BytesIO(z.read(names['.shp'])), shx=BytesIO(z.read(names['.shx'])),
                                  dbf=BytesIO(z.read(names['.dbf'])), encoding='utf-8')
        boundaries, keelung, repairs = [], [], []
        for sr in reader.iterShapeRecords():
            b = sr.record.as_dict()
            if b['COUNTYCODE'] not in CITIES:
                continue
            row = {'village_code': b['VILLCODE'], 'county_code': b['COUNTYCODE'], 'county_name': b['COUNTYNAME'],
                   'district_code': b['TOWNCODE'], 'district_name': b['TOWNNAME'], 'village_name': b['VILLNAME'] or None,
                   'source_note': b['NOTE'] or None}
            boundaries.append(row)
            if b['COUNTYCODE'] != '10017':
                continue
            geom = shape(sr.shape.__geo_interface__)
            if not geom.is_valid:
                geom = make_valid(geom)
                repairs.append(row['village_code'])
            geom = orient_polygons(transform(geom, converter.transform, interleaved=False))
            if geom.is_empty or not geom.is_valid or geom.geom_type not in {'Polygon', 'MultiPolygon'}:
                raise ValueError('Invalid Keelung village geometry')
            west, south, east, north = geom.bounds
            if not (121 < west <= east < 123 and 24 < south <= north < 26):
                raise ValueError('Keelung geometry outside expected geographic range')
            keelung.append({'type': 'Feature', 'id': row['village_code'], 'properties': row, 'geometry': mapping(geom)})
    records, extras = match(population, boundaries, source_period)
    metadata = {'sources': manifest['records'], 'population_source': population['metadata']['source'],
                'population_period': month, 'boundary_source_period': source_period, 'version_evidence': stem + '.shp',
                'grain': 'one population village code; unmatched boundary areas separately accounted',
                'record_count': len(records), 'status_counts': dict(Counter(r['status'] for r in records)),
                'boundary_record_count': len(boundaries), 'boundary_only_count': len(extras),
                'independent_review_status': 'pending',
                'limitations': ['逐筆核對官方區域代碼、縣市與區名；異體字及造字差異保留兩端原文，不自動校正。',
                                '里界版本與人口月份不同；完整版本對照不等於證明人口月份當時幾何完全相同。',
                                '未編定村里區塊沒有對應人口列，不視為零人口，也不將人口移配到其他里。']}
    output = {'schema_version': 1, 'dataset_id': 'regional_population_boundary_crosswalk', 'metadata': metadata,
              'records': records, 'boundary_only_records': extras}
    write_json(root / 'data/official_cross_city/population_boundary_crosswalk.json', output)
    geo = {'type': 'FeatureCollection', 'schema_version': 1, 'dataset_id': 'keelung_official_villages',
           'metadata': {'sources': manifest['records'], 'source_period': source_period,
                        'version_evidence': stem + '.shp', 'feature_count': len(keelung),
                        'crs': 'EPSG:4326', 'coordinate_order': ['longitude', 'latitude'],
                        'grain': 'one official village or unassigned area polygon',
                        'source_crs': 'EPSG:3824', 'coordinate_operation': converter.get_last_used_operation().description,
                        'coordinate_operation_accuracy_m': converter.get_last_used_operation().accuracy,
                        'geometry_repair_ids': repairs, 'independent_review_status': 'pending',
                        'license': {'name': '政府資料開放授權條款-第1版', 'url': 'https://data.gov.tw/license'},
                        'limitations': metadata['limitations']}, 'features': sorted(keelung, key=lambda f: f['id'])}
    write_json(root / 'map-data/admin/keelung_villages.geojson', geo)
    return metadata


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=ROOT)
    parser.add_argument('--month', required=True, type=validate_month)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--fetch', action='store_true')
    mode.add_argument('--offline', action='store_true')
    args = parser.parse_args(argv)
    if not (args.fetch or args.offline):
        print(json.dumps({'status': 'plan_only', 'population_month': args.month, 'boundary_catalog': PAGE}))
        return
    if args.fetch:
        download(args.root)
    metadata = build(args.root, args.month)
    print(json.dumps({k: metadata[k] for k in ['record_count', 'status_counts', 'boundary_only_count']}, ensure_ascii=False))


if __name__ == '__main__':
    main()
