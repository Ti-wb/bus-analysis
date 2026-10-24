import gzip
import json
from pathlib import Path
import tempfile
import unittest

from scripts.build_validation_catalog import build, requires_review, review_for_output


class ValidationCatalogTests(unittest.TestCase):
    def test_new_schedule_file_cannot_inherit_old_aggregate_pass(self):
        report = {'metadata': {'status': 'passed'}, 'records': []}
        self.assertEqual(review_for_output(report, 'data/official_cross_city/service_coverage/Taipei_Schedule.json.gz',
                                          'schedule', {}, []), 'pending_missing_dataset_review')

    def test_explicit_other_path_cannot_match_by_dataset_name(self):
        report = {'metadata': {'status': 'passed'}, 'records': [
            {'path': 'old.json', 'dataset_id': 'same_dataset', 'status': 'passed'}]}
        self.assertEqual(review_for_output(report, 'new.json', 'same_dataset', {}, []),
                         'pending_missing_dataset_review')

    def test_matching_count_does_not_certify_a_different_source_snapshot(self):
        path = 'data/official_cross_city/service_coverage/Taipei_Schedule.json.gz'
        report = {'records': [{'path': path, 'status': 'passed', 'record_count': 1,
                              'reviewed_source_ids': ['page0'], 'source_downloaded_at': ['old']} ]}
        metadata = {'sources': [{'source_id': 'page0', 'downloaded_at': 'new'}]}
        self.assertEqual(review_for_output(report, path, 'schedule', metadata, [{}]),
                         'pending_source_snapshot_review')
        metadata['sources'][0]['downloaded_at'] = 'old'
        self.assertEqual(review_for_output(report, path, 'schedule', metadata, [{}]), 'passed')

    def test_same_count_changed_raw_file_cannot_reuse_road_review(self):
        report = {'records': [{'path': 'road.json', 'status': 'passed', 'record_count': 1,
                              'source_id': 'road', 'raw_file': 'old.json', 'http_status': 200}]}
        metadata = {'sources': [{'source_id': 'road', 'raw_file': 'new.json', 'http_status': 200}]}
        self.assertEqual(review_for_output(report, 'road.json', 'road', metadata, [{}]),
                         'pending_source_snapshot_review')

    def test_boundary_report_dataset_specific_status_is_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            folder = root / 'data/official_cross_city/boundary_evidence'
            folder.mkdir(parents=True)
            (folder / 'municipal_source_diagnostics.json').write_text(json.dumps({
                'schema_version': 1, 'dataset_id': 'municipal_diagnostics', 'metadata': {}, 'records': []}))
            quality = root / 'data/quality'
            quality.mkdir()
            (quality / 'boundary_evidence_review.json').write_text(json.dumps({
                'metadata': {'status': 'passed_with_temporal_limitations'}, 'records': [
                    {'dataset': 'municipal_source_diagnostics', 'status': 'passed_as_diagnostics_only'}]}))
            record = build(root)['records'][0]
            self.assertEqual(record['independent_review']['status'], 'passed_as_diagnostics_only')

    def test_gzip_and_geojson_are_indexed_without_losing_review_boundaries(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            observations = root / 'data/tdx_bus_travel_time/observations'
            observations.mkdir(parents=True)
            obj = {'schema_version': 1, 'dataset_id': 'travel', 'metadata': {'source_updated_at': None},
                   'records': [{'route_uid': '001', 'run_time': None}]}
            (observations / 'one.json.gz').write_bytes(gzip.compress(json.dumps(obj).encode()))
            quality = root / 'data/quality'
            quality.mkdir()
            path = 'data/tdx_bus_travel_time/observations/one.json.gz'
            (quality / 'tdx_bus_travel_time_review.json').write_text(json.dumps({'metadata': {'status': 'passed'},
                 'records': [{'path': path, 'status': 'passed_with_source_limitations'}]}))
            geo = root / 'map-data/walking'
            geo.mkdir(parents=True)
            (geo / 'candidate_paths.geojson').write_text(json.dumps({'type': 'FeatureCollection',
                'metadata': {'dataset_id': 'candidate', 'requires_source_review': True}, 'features': []}))
            (observations / 'bad.json.part').write_text('not a completed source')
            catalog = build(root)
            by_path = {r['path']: r for r in catalog['records']}
            self.assertEqual(len(by_path), 2)
            self.assertEqual(by_path[path]['record_count'], 1)
            self.assertEqual(by_path[path]['independent_review']['status'], 'passed_with_source_limitations')
            candidate = by_path['map-data/walking/candidate_paths.geojson']
            self.assertTrue(candidate['requires_source_review'])
            self.assertEqual(candidate['independent_review']['status'], 'pending')

    def test_unverified_walking_and_conflicting_ids_cannot_become_accepted(self):
        for status in ['unverified', 'requires_review_published_field_conflict', 'unresolved_membership',
                       'not_validated_insufficient_inputs', 'requires_source_review']:
            with self.subTest(status=status):
                self.assertTrue(requires_review({}, [{'status': status}]))
        self.assertFalse(requires_review({}, [{'status': 'verified_full_stop_id_sequence'}]))
        self.assertTrue(requires_review({}, [{'status': 'verified_explicit_source_identity',
                                              'source_review_flags': ['source_coordinate_difference']}]))
        self.assertTrue(requires_review({}, [{'requires_source_review': True}]))
        self.assertTrue(requires_review({}, [{'type': 'Feature', 'properties': {'status': 'unverified'}}]))
        self.assertTrue(requires_review({'source_review_flags': ['uncertain_vintage']}, []))


if __name__ == '__main__':
    unittest.main()
