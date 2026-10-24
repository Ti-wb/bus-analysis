"""Index reviewed additive datasets without treating references as observations."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def build():
    groups = [
        ('data/official_cross_city/*crosswalk.json', 'data/quality/id_crosswalk_review.json'),
        ('data/official_cross_city/population_2026-09.json', 'data/quality/regional_validation_review.json'),
        ('data/official_cross_city/population_boundary_crosswalk.json', 'data/quality/regional_validation_review.json'),
        ('data/official_cross_city/regional_routes/*.json', 'data/quality/regional_validation_review.json'),
        ('map-data/admin/keelung_villages.geojson', 'map-data/quality/keelung_review.json'),
        ('data/tdx_bus_travel_time/observations/*.json', 'data/quality/tdx_bus_travel_time_review.json'),
        ('data/tdx_road_traffic/observations/*.json', 'data/quality/tdx_road_traffic_review.json'),
        ('data/tdx_road_traffic/availability/*.json', 'data/quality/tdx_road_traffic_review.json'),
    ]
    records = {}
    for pattern, report_file in groups:
        report_path = ROOT / report_file
        report = json.loads(report_path.read_text(encoding='utf-8')) if report_path.exists() else {}
        status = report.get('metadata', {}).get('independent_review_status') or report.get('metadata', {}).get('overall_status', 'pending')
        reviewed_outputs = {r['output_path']: r for r in report.get('records', []) if r.get('output_path')}
        for file in sorted(ROOT.glob(pattern)):
            path = file.relative_to(ROOT).as_posix()
            if path.endswith('/population_boundary_crosswalk.json') and 'id_crosswalk' in report_file:
                continue
            obj = json.loads(file.read_text(encoding='utf-8'))
            meta = obj['metadata']
            rows = obj.get('records', obj.get('features'))
            assert isinstance(rows, list) and obj.get('dataset_id')
            needs_source_review = meta.get('requires_source_review') or any(
                str(r.get('linkage_status', '')).startswith('unresolved') or 'requires_review' in str(r.get('status', ''))
                for r in rows if isinstance(r, dict))
            period = meta.get('statistical_period') or meta.get('period') or meta.get('source_period') or meta.get('population_period')
            if period is None and meta.get('requested_date'):
                period = {k: meta.get(k) for k in ['requested_date', 'version_start', 'version_end']}
            reviewed = reviewed_outputs.get(path, {})
            review_status = reviewed.get('review_status') or reviewed.get('status') or status
            records[path] = {'dataset_id': obj['dataset_id'], 'path': path, 'record_count': len(rows),
                             'kind': meta.get('data_kind') or ('geographic_features' if obj.get('type') == 'FeatureCollection' else 'normalized_records'),
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
