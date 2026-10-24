"""Index reviewed additive datasets without treating references as observations."""
import gzip
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read_json(file):
    if file.suffix == '.gz':
        with gzip.open(file, 'rb') as stream:
            body = stream.read(128 * 1024 * 1024 + 1)
        if len(body) > 128 * 1024 * 1024:
            raise ValueError('Catalog input exceeds decoded byte budget')
        return json.loads(body)
    return json.loads(file.read_text(encoding='utf-8'))


def requires_review(meta, rows):
    def uncertain(row):
        status = str(row.get('status', ''))
        return (str(row.get('linkage_status', '')).startswith('unresolved') or
                bool(row.get('requires_source_review') or row.get('source_review_flags')) or
                'requires_review' in status or 'requires_source_review' in status or
                status.startswith(('unverified', 'unresolved', 'not_validated')))
    containers = [container for row in rows if isinstance(row, dict)
                  for container in [row, row.get('properties', {})] if isinstance(container, dict)]
    return bool(meta.get('requires_source_review') or meta.get('source_review_flags') or
                any(uncertain(row) for row in containers))


def build(root=ROOT):
    groups = [
        ('data/official_cross_city/*crosswalk.json', 'data/quality/id_crosswalk_review.json'),
        ('data/official_cross_city/population_2026-09.json', 'data/quality/regional_validation_review.json'),
        ('data/official_cross_city/population_boundary_crosswalk.json', 'data/quality/regional_validation_review.json'),
        ('data/official_cross_city/regional_routes/*.json', 'data/quality/regional_validation_review.json'),
        ('map-data/admin/keelung_villages.geojson', 'map-data/quality/keelung_review.json'),
        ('data/tdx_bus_travel_time/observations/*.json*', 'data/quality/tdx_bus_travel_time_review.json'),
        ('data/tdx_bus_travel_time/coverage.json', 'data/quality/tdx_bus_travel_time_review.json'),
        ('data/tdx_bus_travel_time/delivery.json', 'data/quality/tdx_bus_travel_time_review.json'),
        ('data/official_cross_city/boundary_evidence/*.json', 'data/quality/boundary_evidence_review.json'),
        ('data/official_cross_city/id_evidence/*.json*', 'data/quality/id_evidence_review.json'),
        ('data/official_cross_city/service_coverage/*.json.gz', 'data/quality/service_coverage_review.json'),
        ('data/official_cross_city/service_coverage/coverage.json', 'data/quality/service_coverage_review.json'),
        ('data/official_cross_city/taipei_id_evidence/*.json*', 'data/quality/taipei_id_evidence_review.json'),
        ('data/official_cross_city/regional_service_evidence/*.json*', 'data/quality/regional_service_evidence_review.json'),
        ('data/official_cross_city/walking_transfers/*.json*', 'map-data/quality/walking_evidence_review.json'),
        ('map-data/walking/*.geojson', 'map-data/quality/walking_evidence_review.json'),
        ('map-data/walking/further_evidence.json', 'map-data/quality/walking_further_evidence_review.json'),
        ('data/tdx_road_traffic/observations/*.json', 'data/quality/tdx_road_traffic_review.json'),
        ('data/tdx_road_traffic/availability/*.json', 'data/quality/tdx_road_traffic_review.json'),
        ('data/tdx_road_traffic/delivery.json', 'data/quality/tdx_road_traffic_review.json'),
        ('data/tdx_road_traffic/reference_diagnostics.json', 'data/quality/tdx_road_traffic_review.json'),
    ]
    evidence_kinds = {
        'data/official_cross_city/service_coverage/coverage.json': 'acquisition_coverage',
        'data/tdx_bus_travel_time/coverage.json': 'acquisition_coverage',
        'data/tdx_bus_travel_time/delivery.json': 'acquisition_delivery',
        'data/tdx_road_traffic/delivery.json': 'acquisition_delivery',
        'data/tdx_road_traffic/reference_diagnostics.json': 'id_reference_diagnostics',
    }
    records = {}
    for pattern, report_file in groups:
        report_path = root / report_file
        report = json.loads(report_path.read_text(encoding='utf-8')) if report_path.exists() else {}
        status = (report.get('metadata', {}).get('independent_review_status') or
                  report.get('metadata', {}).get('overall_status') or report.get('metadata', {}).get('status', 'pending'))
        reviewed_outputs = {r.get('output_path') or r.get('path') or r.get('output_file'): r for r in report.get('records', [])
                            if r.get('output_path') or r.get('path') or r.get('output_file')}
        reviewed_datasets = {r.get('dataset_id') or r.get('dataset'): r for r in report.get('records', [])
                             if r.get('dataset_id') or r.get('dataset')}
        for file in sorted(root.glob(pattern)):
            if file.name == 'manifest.json' or not file.name.endswith(('.json', '.json.gz', '.geojson')):
                continue
            path = file.relative_to(root).as_posix()
            if path.endswith('/population_boundary_crosswalk.json') and 'id_crosswalk' in report_file:
                continue
            obj = read_json(file)
            meta = obj['metadata']
            rows = obj.get('records', obj.get('features'))
            dataset_id = obj.get('dataset_id') or meta.get('dataset_id')
            assert isinstance(rows, list) and dataset_id
            needs_source_review = requires_review(meta, rows)
            period = meta.get('statistical_period') or meta.get('period') or meta.get('source_period') or meta.get('population_period')
            if period is None and meta.get('requested_date'):
                period = {k: meta.get(k) for k in ['requested_date', 'version_start', 'version_end']}
            reviewed = reviewed_outputs.get(path) or reviewed_datasets.get(dataset_id) or reviewed_datasets.get(file.stem, {})
            review_status = reviewed.get('review_status') or reviewed.get('status') or status
            records[path] = {'dataset_id': dataset_id, 'path': path, 'record_count': len(rows),
                             'kind': evidence_kinds.get(path) or meta.get('data_kind') or
                                     ('geographic_features' if obj.get('type') == 'FeatureCollection' else 'normalized_records'),
                             'grain': meta.get('grain'), 'source_updated_at': meta.get('source_updated_at', meta.get('source_data_time')),
                             'period': period,
                             'scope_status': meta.get('scope_status'), 'units': meta.get('units', meta.get('field_units')),
                             'requires_source_review': bool(needs_source_review),
                             'data_acceptance_status': 'requires_source_review' if needs_source_review else 'within_documented_scope_only',
                             'independent_review': {'status': review_status, 'report': report_file if report else None},
                             'limitations': meta.get('limitations', [])}
    return {'schema_version': 1, 'dataset_id': 'additional_transport_validation_catalog',
            'metadata': {'kind': 'dataset_index_not_observations', 'record_count': len(records),
                         'source_updated_at': None, 'validation_status': 'data/quality/pr_validation.json',
                         'update_command': 'uv run scripts/build_validation_catalog.py',
                         'limitations': ['各來源粒度不同，筆數不可跨資料相加當成同一統計量。',
                                         '巡檢通過僅適用所列快照與範圍，未完成的 ID／地理／營運驗收另列。']},
            'records': list(records.values())}


if __name__ == '__main__':
    result = build()
    (ROOT / 'data/official_cross_city/validation_catalog.json').write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(f'Indexed {len(result["records"])} additive datasets')
