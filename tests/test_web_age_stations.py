"""Keep regional station identity and village-age inputs separate from older models."""
from copy import deepcopy
import gzip
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts import build_web_data as web
from scripts.build_web_data import age_catchment_shard_index, age_catchment_shards, village_age_station_points


def station(ident, coordinate=(121.5, 25.0), name="同名站"):
    return {"type": "Feature", "properties": {"id": ident, "name": name},
            "geometry": {"type": "Point", "coordinates": list(coordinate)}}


def collection(*features):
    return {"type": "FeatureCollection", "features": list(features)}


class VillageAgeStationInputsTests(unittest.TestCase):
    def setUp(self):
        self.points = {"bus": {"100": [121.5, 25.0]}, "mrt": {"100": [121.51, 25.01]},
                       "youbike": {"100": [121.52, 25.02]}}

    def test_original_ids_and_coordinate_variants_are_retained_without_merging(self):
        before = deepcopy(self.points)
        result = village_age_station_points(self.points, collection(
            station("NWT100"), station("NWT200:121.5:25.0"), station("NWT200:121.6:25.1", (121.6, 25.1)),
        ))
        self.assertEqual(set(result["bus"]), {"100", "NWT100", "NWT200:121.5:25.0", "NWT200:121.6:25.1"})
        self.assertEqual(result["bus"]["NWT200:121.6:25.1"], [121.6, 25.1])
        self.assertEqual(result["mrt"], self.points["mrt"])
        self.assertEqual(result["youbike"], self.points["youbike"])
        self.assertEqual(self.points, before)  # older statistical-area/grid inputs remain unchanged
        result["bus"]["new-only-age"] = [121.4, 25.1]
        self.assertNotIn("new-only-age", self.points["bus"])

    def test_duplicate_or_colliding_id_does_not_replace_a_station(self):
        for features in ((station("100"),), (station("NWT100"), station("NWT100"))):
            with self.subTest(features=features), self.assertRaisesRegex(ValueError, "Duplicate bus station ID"):
                village_age_station_points(self.points, collection(*features))

    def test_original_source_id_and_finite_point_are_required(self):
        for feature in (station(""), station(100), station("NWT100", (121.5, float("nan"))),
                        station("NWT100", (True, 25.0))):
            with self.subTest(feature=feature), self.assertRaises(ValueError):
                village_age_station_points(self.points, collection(feature))
        line = station("NWT100")
        line["geometry"]["type"] = "LineString"
        with self.assertRaisesRegex(ValueError, "Invalid regional bus station point"):
            village_age_station_points(self.points, collection(line))

    def test_far_island_station_explicitly_fails_northern_projection_scope(self):
        with self.assertRaisesRegex(ValueError, "Unsupported projection scope.*NWTfar"):
            village_age_station_points(self.points, collection(station("NWTfar", (118.3, 24.4))))
        self.assertEqual(len(self.points["bus"]), 1)


class AgeCatchmentShardTests(unittest.TestCase):
    def canonical(self):
        return {"schema_version": 1, "dataset_id": "web_station_age_catchments", "radii": [200, 350, 500],
                "metadata": {"population_period": "2026-09", "source": {"receipt": "preserved"}},
                "records": {kind: {ident: [{"population_estimate": count, "missing": None}]
                                   for count, ident in enumerate(["100", "NWT100", "臺北站", "😀", *map(str, range(200))])}
                            for kind in ("bus", "mrt", "youbike")}}

    def test_hash_fixed_vectors_agree_with_node_textencoder_and_math_imul(self):
        # Independently calculated in Node; kind namespaces and UTF-8 bytes matter.
        for kind, ident, expected in [("bus", "100", 24), ("mrt", "100", 31), ("youbike", "100", 24),
                                      ("bus", "NWT100", 13), ("bus", "NWT200:121.5:25.0", 14),
                                      ("bus", "臺北站", 13), ("bus", "😀", 4)]:
            with self.subTest(kind=kind, ident=ident):
                self.assertEqual(age_catchment_shard_index(kind, ident), expected)

    def test_all_32_shards_cover_canonical_records_once_without_changing_values(self):
        canonical = self.canonical()
        original = deepcopy(canonical)
        shards = age_catchment_shards(canonical)
        self.assertEqual(len(shards), 32)
        restored, seen = {kind: {} for kind in canonical["records"]}, set()
        for index, shard in enumerate(shards):
            self.assertEqual(set(shard["records"]), {"bus", "mrt", "youbike"})
            for field in ("schema_version", "dataset_id", "radii"):
                self.assertEqual(shard[field], canonical[field])
            self.assertEqual({key: value for key, value in shard["metadata"].items() if key != "shard"}, canonical["metadata"])
            self.assertEqual(shard["metadata"]["shard"], {
                "algorithm": "fnv1a32-utf8-mod32", "index": index, "count": 32,
                "station_count": sum(map(len, shard["records"].values())),
            })
            for kind, records in shard["records"].items():
                for ident, estimate in records.items():
                    self.assertNotIn((kind, ident), seen)
                    seen.add((kind, ident))
                    self.assertEqual(age_catchment_shard_index(kind, ident), index)
                    restored[kind][ident] = estimate
        self.assertEqual(restored, canonical["records"])
        self.assertEqual(canonical, original)

    def test_nested_shard_paths_have_identical_json_and_gzip_payloads(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(web, "OUT", Path(directory)), patch("builtins.print"):
            web.write_age_catchment_shards(self.canonical())
            paths = sorted((Path(directory) / "station_age_catchments").glob("*.json"))
            self.assertEqual(len(paths), 32)
            self.assertEqual(paths[0].name, "part-00.json")
            self.assertEqual(paths[-1].name, "part-31.json")
            for path in paths:
                encoded = path.read_bytes()
                self.assertEqual(gzip.decompress(path.with_suffix(".json.gz").read_bytes()), encoded)
                self.assertEqual(json.loads(encoded)["metadata"]["shard"]["count"], 32)


if __name__ == "__main__":
    unittest.main()
