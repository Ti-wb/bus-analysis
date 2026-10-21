#!/usr/bin/env python3
"""Build Taipei/New Taipei administrative GIS layers from official NLSC vectors."""

import argparse
from collections import Counter
from datetime import datetime
import json
from pathlib import Path
import re
import subprocess
from urllib.parse import urlparse
import zipfile
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup
from pyproj import CRS, Transformer
import shapefile
from shapely.geometry import shape, mapping
from shapely import make_valid, orient_polygons, transform

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / 'map-data/raw/admin'
OUT = ROOT / 'map-data/admin'
SOURCES = {
    'cities': {'page_url': 'https://data.gov.tw/dataset/7442', 'title': '直轄市、縣市界線(TWD97經緯度)'},
    'districts': {'page_url': 'https://data.gov.tw/dataset/7441', 'title': '鄉鎮市區界線(TWD97經緯度)'},
    'villages': {'page_url': 'https://data.gov.tw/dataset/7438', 'title': '村里界圖(TWD97經緯度)'},
}
CITY_CODES = {'63000': '臺北市', '65000': '新北市'}
LICENSE = {'name': '政府資料開放授權條款－第1版', 'url': 'https://data.gov.tw/license',
    'attribution': '內政部國土測繪中心；政府資料開放平臺'}


def now():
    return datetime.now(ZoneInfo('Asia/Taipei')).isoformat(timespec='seconds')


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = RAW / (path.name + '.part')
    temporary.write_text(json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(',', ':')) + '\n', encoding='utf-8')
    temporary.replace(path)


class Downloads:
    def __init__(self, offline):
        self.offline = offline
        RAW.mkdir(parents=True, exist_ok=True)
        self.path = RAW / 'sources.json'
        self.entries = json.loads(self.path.read_text()) if self.path.exists() else {}

    def fetch(self, url, name, page_url):
        path = RAW / name
        if self.offline:
            if not path.is_file() or not path.stat().st_size or self.entries.get(name, {}).get('url') != url:
                raise ValueError(f'Missing offline source or changed URL: {name}')
            return path
        if urlparse(url).scheme != 'https' or urlparse(url).hostname not in {'data.gov.tw', 'www.tgos.tw', 'tgos.tw'}:
            raise ValueError(f'Unexpected official source: {url}')
        temporary = RAW / (name + '.download')
        result = subprocess.run(['curl', '--fail', '--location', '--silent', '--show-error',
            '--proto', '=https', '--proto-redir', '=https', '--connect-timeout', '30',
            '--max-time', '480', '--retry', '2', '--retry-delay', '3', '--max-filesize', '300000000',
            '--output', str(temporary), '--write-out', '%{json}', url], capture_output=True, text=True)
        if result.returncode:
            temporary.unlink(missing_ok=True)
            raise RuntimeError(result.stderr.strip())
        info = json.loads(result.stdout)
        temporary.replace(path)
        self.entries[name] = {'page_url': page_url, 'url': url, 'raw_file': path.relative_to(ROOT).as_posix(),
            'downloaded_at': now()}
        if info['url_effective'] != url:
            self.entries[name]['resolved_url'] = info['url_effective']
        write(self.path, self.entries)
        print(f'Downloaded {name}', flush=True)
        return path


def acquire(d, layer):
    source = SOURCES[layer]
    page = d.fetch(source['page_url'], f'{layer}_page.html', source['page_url'])
    soup = BeautifulSoup(page.read_text(), 'html.parser')
    links = [li.find('a', href=True)['href'] for li in soup.select('li.resource-item')
        if 'SHP' in li.get_text() and li.find('a', href=True)]
    if len(links) != 1:
        raise ValueError(f'Expected one official SHP resource for {layer}: {links}')
    archive = d.fetch(links[0], f'{layer}.zip', source['page_url'])
    return archive


def round_coordinates(value):
    if isinstance(value, (list, tuple)):
        return [round_coordinates(v) for v in value]
    return round(value, 9)


def version_date(stem):
    match = re.search(r'_(\d{3})(\d{2})(\d{2})$', stem)
    if not match:
        raise ValueError(f'Unknown official boundary vintage: {stem}')
    return datetime(int(match[1]) + 1911, int(match[2]), int(match[3])).date().isoformat()


def normalize(d, layer, archive):
    directory = RAW / layer
    directory.mkdir(exist_ok=True)
    pattern = {'cities': r'COUNTY_MOI_\d+\.shp$', 'districts': r'TOWN_MOI_\d+\.shp$',
        'villages': r'VILLAGE_NLSC_\d+\.shp$'}[layer]
    with zipfile.ZipFile(archive) as z:
        names = [n for n in z.namelist() if re.search(pattern, n, re.I)]
        if len(names) != 1:
            raise ValueError(f'Expected one national main SHP for {layer}: {names}')
        main = names[0]
        base = main[:-4]
        members = {Path(n).suffix.lower(): n for n in z.namelist()
            if n.lower().startswith(base.lower() + '.') and Path(n).suffix.lower() in {'.shp', '.shx', '.dbf', '.prj', '.cpg'}}
        if set(members) != {'.shp', '.shx', '.dbf', '.prj', '.cpg'}:
            raise ValueError('Incomplete official shapefile')
        for member in members.values():
            target = directory / Path(member).name
            target.write_bytes(z.read(member))
    shp = directory / Path(main).name
    prj = directory / Path(members['.prj']).name
    cpg = directory / Path(members['.cpg']).name
    source_crs = CRS.from_wkt(prj.read_text())
    if source_crs.to_epsg() != 3824:
        raise ValueError(f'Unexpected source CRS: {source_crs.name}')
    encoding = cpg.read_text().strip()
    to_wgs84 = Transformer.from_crs(source_crs, 'EPSG:4326', always_xy=True)
    reader = shapefile.Reader(str(shp), encoding=encoding)
    features = []
    repairs = []
    full_precision = []
    native_fields = {'cities': 'COUNTYCODE', 'districts': 'TOWNCODE', 'villages': 'VILLCODE'}
    admin_level = {'cities': 'city', 'districts': 'district', 'villages': 'village'}[layer]
    for sr in reader.iterShapeRecords():
        raw = sr.record.as_dict()
        city_code = raw['COUNTYCODE'].strip()
        if city_code not in CITY_CODES:
            continue
        if raw['COUNTYNAME'] != CITY_CODES[city_code]:
            raise ValueError('Official city code and name disagree')
        code = raw[native_fields[layer]].strip()
        geometry = shape(sr.shape.__geo_interface__)
        if geometry.is_empty:
            raise ValueError(f'Empty official geometry: {code}')
        if not geometry.is_valid:
            original_type = geometry.geom_type
            geometry = make_valid(geometry)
            repairs.append({'admin_code': code, 'method': 'shapely.make_valid', 'before_type': original_type, 'after_type': geometry.geom_type})
        if geometry.geom_type not in {'Polygon', 'MultiPolygon'}:
            raise ValueError(f'Non-polygon administrative boundary: {code}')
        geometry = transform(geometry, to_wgs84.transform, interleaved=False)
        mapped = mapping(geometry)
        rounded = {'type': mapped['type'], 'coordinates': round_coordinates(mapped['coordinates'])}
        rounded_geometry = shape(rounded)
        if rounded_geometry.is_valid and not rounded_geometry.is_empty:
            geometry = rounded_geometry
        else:
            full_precision.append(code)
        if not geometry.is_valid or geometry.is_empty:
            raise ValueError(f'Invalid output administrative geometry: {code}')
        geometry = orient_polygons(geometry, exterior_cw=False)
        bounds = geometry.bounds
        if not (120 < bounds[0] <= bounds[2] < 123 and 24 < bounds[1] <= bounds[3] < 26):
            raise ValueError(f'Boundary outside northern Taiwan: {code}')
        district_code = raw.get('TOWNCODE') or None
        name = raw['COUNTYNAME'] if layer == 'cities' else raw['TOWNNAME'] if layer == 'districts' else raw['VILLNAME']
        english = raw.get({'cities': 'COUNTYENG', 'districts': 'TOWNENG', 'villages': 'VILLENG'}[layer]) or None
        props = {'admin_code': code, 'admin_level': admin_level,
            'parent_code': None if layer == 'cities' else city_code if layer == 'districts' else district_code,
            'name': name, 'name_en': english, 'city_code': city_code, 'city_name': raw['COUNTYNAME'],
            'district_code': district_code, 'district_name': raw.get('TOWNNAME') or None,
            'source_county_id': raw.get('COUNTYID') or None, 'source_town_id': raw.get('TOWNID') or None,
            'source_note': raw.get('NOTE') or None}
        features.append({'type': 'Feature', 'id': code, 'bbox': list(bounds),
            'properties': props, 'geometry': mapping(geometry)})
    features.sort(key=lambda f: f['id'])
    codes = [f['id'] for f in features]
    if len(codes) != len(set(codes)):
        raise ValueError(f'Duplicate official codes in {layer}')
    counts = dict(Counter(f['properties']['city_code'] for f in features))
    expected = {'cities': {'63000': 1, '65000': 1}, 'districts': {'63000': 12, '65000': 29}}
    if layer in expected and counts != expected[layer]:
        raise ValueError(f'Unexpected city/district counts: {counts}')
    if set(counts) != set(CITY_CODES):
        raise ValueError(f'Missing selected city in {layer}')
    source_period = version_date(shp.stem)
    page_text = BeautifulSoup((RAW / f'{layer}_page.html').read_text(), 'html.parser').get_text(' ', strip=True)
    page_updated = re.search(r'詮釋資料更新時間\s*(\d{4}-\d{2}-\d{2} \d{2}:\d{2})', page_text)
    source = {**d.entries[archive.name], 'provider': '內政部國土測繪中心',
        'title': SOURCES[layer]['title'], 'source_period': source_period,
        'version_evidence': shp.name, 'catalog_updated_at': page_updated[1] if page_updated else None,
        'original_crs': source_crs.to_string(), 'original_crs_name': source_crs.name,
        'original_crs_wkt': prj.read_text(), 'encoding': encoding, 'license': LICENSE}
    operation = to_wgs84.get_last_used_operation()
    bbox = [min(f['bbox'][0] for f in features), min(f['bbox'][1] for f in features),
        max(f['bbox'][2] for f in features), max(f['bbox'][3] for f in features)]
    metadata = {'title': f'雙北{ {"cities":"市界", "districts":"區界", "villages":"里界"}[layer]}',
        'sources': [source], 'source_period': source_period, 'crs': 'EPSG:4326',
        'coordinate_order': ['longitude', 'latitude'], 'grain': f'one feature per official {admin_level} code',
        'scope': {'city_codes': CITY_CODES, 'selection': 'Exact source COUNTYCODE membership; not a bounding-box selection.'},
        'license': LICENSE, 'source_record_count': len(reader), 'feature_count': len(features), 'city_counts': counts,
        'coordinate_processing': {'source_crs': 'EPSG:3824', 'target_crs': 'EPSG:4326',
            'operation': operation.description, 'operation_accuracy_m': operation.accuracy,
            'decimal_places': 9, 'simplified': False, 'full_precision_feature_ids': full_precision,
            'ring_orientation': 'RFC 7946: exterior counter-clockwise, holes clockwise', 'geometry_repairs': repairs},
        'code_fields': {'admin_code': native_fields[layer], 'city_code': 'COUNTYCODE', 'district_code': 'TOWNCODE',
            'source_county_id': 'COUNTYID', 'source_town_id': 'TOWNID'},
        'limitations': ['資料版本由官方主 SHP 檔名識別，網站詮釋資料更新日期不當作界線生效日期。',
            '保留官方圖形，不以2015統計區邊界替代行政里界；不同層級來源版本可不同。',
            '來源為TWD97[2020]經緯度，PROJ識別為EPSG:3824；轉WGS84所採operation與精度見coordinate_processing。',
            '上下級以官方行政代碼關聯；保留各層原始界線，未用父層圖形裁切或強行對齊不同版本邊界。'],
        'raw_source_policy': 'Raw ZIP and extracted shapefiles are optional local cache under map-data/raw/admin, excluded from Git.'}
    obj = {'type': 'FeatureCollection', 'schema_version': 1, 'dataset_id': f'taipei_new_taipei_admin_{layer}',
        'metadata': metadata, 'bbox': bbox, 'features': features}
    write(OUT / f'{layer}.geojson', obj)
    print(f'{layer}: {len(features)} features, version {source_period}', flush=True)
    return obj


def build_index(layers):
    by_level = {name: {f['id']: f for f in obj['features']} for name, obj in layers.items()}
    records = []
    for layer, obj in layers.items():
        for feature in obj['features']:
            props = feature['properties']
            if layer == 'districts' and props['parent_code'] not in by_level['cities']:
                raise ValueError('District parent code not found')
            if layer == 'villages' and props['parent_code'] not in by_level['districts']:
                raise ValueError('Village parent code not found')
            if layer != 'cities':
                parent_layer = 'cities' if layer == 'districts' else 'districts'
                parent = by_level[parent_layer][props['parent_code']]['properties']
                if parent['city_code'] != props['city_code']:
                    raise ValueError('Child city code differs from parent')
            geometry = shape(feature['geometry'])
            point = geometry.representative_point()
            if not geometry.covers(point):
                raise ValueError('Label point is not on administrative surface')
            records.append({**props, 'bbox': feature['bbox'], 'label_point': [point.x, point.y],
                'geometry_file': f'map-data/admin/{layer}.geojson', 'feature_id': feature['id'],
                'source_period': obj['metadata']['source_period']})
    output = {'schema_version': 1, 'dataset_id': 'taipei_new_taipei_admin_index',
        'metadata': {'title': '雙北行政區階層與地圖標籤索引', 'crs': 'EPSG:4326',
            'coordinate_order': ['longitude', 'latitude'], 'record_count': len(records),
            'label_point_method': 'Shapely representative_point (point on surface), validated within the final polygon.',
            'sources': [source for obj in layers.values() for source in obj['metadata']['sources']],
            'license': LICENSE, 'limitations': ['標籤點僅供地圖標示，不是行政中心、人口重心或面積質心。']},
        'records': records}
    write(OUT / 'admin_index.json', output)
    return output


def build_manifest(layers, index=None):
    records = []
    for layer, obj in layers.items():
        path = OUT / f'{layer}.geojson'
        records.append({'dataset_id': obj['dataset_id'], 'path': path.relative_to(ROOT).as_posix(),
            'feature_count': len(obj['features']), 'bytes': path.stat().st_size, 'bbox': obj['bbox'],
            'source_period': obj['metadata']['source_period'], 'sources': obj['metadata']['sources'],
            'limitations': obj['metadata']['limitations']})
    if index is not None:
        path = OUT / 'admin_index.json'
        records.append({'dataset_id': index['dataset_id'], 'path': path.relative_to(ROOT).as_posix(),
            'record_count': len(index['records']), 'bytes': path.stat().st_size, 'source_period': None,
            'sources': index['metadata']['sources'], 'limitations': index['metadata']['limitations']})
    manifest = {'schema_version': 1, 'dataset_id': 'taipei_new_taipei_admin_manifest',
        'metadata': {'scope': CITY_CODES, 'license': LICENSE, 'crs': 'EPSG:4326',
            'update_command': 'uv run scripts/update_map_admin.py',
            'offline_command': 'uv run scripts/update_map_admin.py --offline'}, 'records': records}
    write(OUT / 'manifest.json', manifest)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--offline', action='store_true', help='Rebuild from retained local source files.')
    parser.add_argument('--layers', nargs='+', choices=list(SOURCES), default=list(SOURCES))
    parser.add_argument('--download-only', action='store_true')
    args = parser.parse_args()
    d = Downloads(args.offline)
    layers = {}
    for layer in args.layers:
        archive = acquire(d, layer)
        if not args.download_only:
            layers[layer] = normalize(d, layer, archive)
    if layers:
        for layer in SOURCES:
            path = OUT / f'{layer}.geojson'
            if layer not in layers and path.exists():
                layers[layer] = json.loads(path.read_text())
        index = build_index(layers) if set(layers) == set(SOURCES) else None
        build_manifest(layers, index)


if __name__ == '__main__':
    main()
