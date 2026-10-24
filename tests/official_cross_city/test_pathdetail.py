"""Synthetic PathDetail safety tests; live observations are reviewed separately."""
import gzip
import io
import json
from unittest.mock import patch

from test_collectors import FixtureCase
from scripts.official_cross_city import ntpc, ntpc_pathdetail as subject


class PathDetailTests(FixtureCase):
    def payload(self):
        return {'EssentialInfo': {'Location': {'name': '台北市'}, 'UpdateTime': '2026/10/23 21:02:56'},
                'BusInfo': [{'id': 9, 'pathAttributeId': 10, 'stopId': 20, 'sequenceNo': 0,
                             'type': '0', 'typeAttribute': ''}]}

    def reference(self, kind):
        page, url = ntpc.urls(kind)
        records = ([{'subroute_id': '10', 'route_id': '30'}] if kind == 'routes'
                   else [{'stop_id': '20', 'route_id': '30'}, {'stop_id': '21', 'route_id': '30'}])
        for row in records:
            row['source_namespace'] = 'ntpc_open_data'
        return {'schema_version': 1, 'dataset_id': 'ntpc_' + kind,
                'metadata': {'source': {'page_url': page, 'requested_url': url, 'http_status': 200,
                                        'downloaded_at': 'synthetic'}}, 'records': records}

    def result(self, payload=None, routes=None, stops=None):
        path = self.root / 'test.gz'
        with gzip.open(path, 'wt', encoding='utf-8') as stream:
            json.dump(self.payload() if payload is None else payload, stream)
        return subject.normalize(path, self.source, routes or self.reference('routes'),
                                 stops or self.reference('stops'))

    def test_explicit_ids_null_coverage_and_source_anomaly_preserved(self):
        result = self.result()
        self.assertEqual(result['records'][0]['route_id'], '30')
        self.assertEqual(result['records'][0]['subroute_id'], '10')
        self.assertEqual(result['records'][0]['sequence_no'], 0)
        self.assertEqual(result['quality']['unlinked_stop_ids'], ['21'])
        self.assertEqual(result['quality']['linked_stop_count'], 1)
        self.assertEqual(result['metadata']['source_essential_info']['Location']['name'], '台北市')

    def test_plan_never_downloads(self):
        with patch.object(subject, 'fetch', side_effect=AssertionError('network')), patch('sys.stdout', new=io.StringIO()):
            subject.main(['--root', str(self.root)])

    def test_empty_or_invalid_schema_rejected(self):
        for payload in [{}, {'EssentialInfo': {}, 'BusInfo': []}, {'EssentialInfo': {}, 'BusInfo': [{}]}, []]:
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                self.result(payload)

    def test_unknown_ids_and_parent_conflict_rejected(self):
        for field in ['stopId', 'pathAttributeId']:
            payload = self.payload()
            payload['BusInfo'][0][field] = 999
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.result(payload)
        stops = self.reference('stops')
        stops['records'][0]['route_id'] = '999'
        with self.assertRaises(ValueError):
            self.result(stops=stops)

    def test_duplicate_id_or_sequence_cell_rejected(self):
        for changes in [{}, {'id': 11}]:
            payload = self.payload()
            payload['BusInfo'].append({**payload['BusInfo'][0], **changes})
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.result(payload)

    def test_invalid_numeric_ids_and_sequences_rejected(self):
        for field in ['id', 'stopId', 'pathAttributeId', 'sequenceNo']:
            for value in [True, -1, 1.5, None, '1.0', '']:
                payload = self.payload()
                payload['BusInfo'][0][field] = value
                with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                    self.result(payload)

    def test_namespace_and_provenance_not_guessed(self):
        for mutation in ['namespace', 'url', 'dataset', 'parent']:
            routes = self.reference('routes')
            if mutation == 'namespace':
                routes['records'][0]['source_namespace'] = 'taipei'
            elif mutation == 'url':
                routes['metadata']['source']['requested_url'] = 'https://evil.example/'
            elif mutation == 'dataset':
                routes['dataset_id'] = 'taipei_routes'
            else:
                routes['records'].append({**routes['records'][0], 'route_id': '999'})
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                self.result(routes=routes)

    def test_decompressed_and_row_limits(self):
        with patch.object(subject, 'MAX_DECODED_BYTES', 8), self.assertRaises(ValueError):
            self.result()
        with patch.object(subject, 'MAX_ROWS', 0), self.assertRaises(ValueError):
            self.result()

    def test_duplicate_json_keys_rejected(self):
        path = self.root / 'duplicate.gz'
        with gzip.open(path, 'wt') as stream:
            stream.write('{"BusInfo":[],"BusInfo":[1]}')
        with self.assertRaises(ValueError):
            subject.read_payload(path)
        with gzip.open(path, "wt") as stream:
            stream.write('{"extra":NaN}')
        with self.assertRaises(ValueError):
            subject.read_payload(path)

    def test_extra_fields_preserved_without_interpreting_codes(self):
        payload = self.payload()
        payload['BusInfo'][0].update({'future': 'value', 'type': 'unknown'})
        result = self.result(payload)
        self.assertEqual(result['records'][0]['source_extra_fields'], {'future': 'value'})
        self.assertEqual(result['records'][0]['type_raw'], 'unknown')

    def test_failed_normalization_preserves_output(self):
        directory = self.root / 'data/official_cross_city'
        directory.mkdir(parents=True)
        for kind in ['routes', 'stops']:
            (directory / ('ntpc_' + kind + '.json')).write_text(json.dumps(self.reference(kind)))
        output = directory / 'ntpc_pathdetail.json'
        output.write_text('previous good output')
        with patch.object(subject, 'fetch', return_value=(self.root/'bad.gz', {})), self.assertRaises(OSError):
            subject.main(['--root', str(self.root), '--offline'])
        self.assertEqual(output.read_text(), 'previous good output')

    def test_truncated_gzip_and_malformed_reference_rejected(self):
        path = self.root / 'truncated.gz'
        path.write_bytes(gzip.compress(b'{}')[:-4])
        with self.assertRaises(ValueError):
            subject.read_payload(path)
        for obj in [None, [], {}, {'metadata': []}, {'metadata': {'source': []}}]:
            with self.subTest(obj=obj), self.assertRaises(ValueError):
                subject.references(obj, 'routes')

    def test_one_stop_can_belong_to_multiple_subroutes(self):
        routes = self.reference('routes')
        routes['records'].append({**routes['records'][0], 'subroute_id': '11'})
        payload = self.payload()
        payload['BusInfo'].append({**payload['BusInfo'][0], 'id': 12, 'pathAttributeId': 11})
        result = self.result(payload, routes=routes)
        self.assertEqual(len(result['records']), 2)
        self.assertEqual({row['subroute_id'] for row in result['records']}, {'10', '11'})
        self.assertEqual(result['quality']['linked_stop_count'], 1)
        self.assertEqual(result['quality']['linked_subroute_count'], 2)
