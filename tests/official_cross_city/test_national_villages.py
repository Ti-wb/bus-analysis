"""Synthetic administrative identities and polygons; never government observations."""
import io
from pathlib import Path
import tempfile
import unittest
from zipfile import ZipFile

try:
    import shapefile
    from pyproj import CRS
    from shapely.geometry import box
except ImportError:
    shapefile = None

from scripts.official_cross_city import national_villages as module


@unittest.skipIf(shapefile is None, "GIS project dependencies required; covered in web CI")
class NationalVillageTests(unittest.TestCase):
    def pop(self, code="63000010001", name="測試里"):
        return {"dataset_id": "moi_village_population_age_monthly", "metadata": {"period": "2026-09"},
                "records": [{"sex": "total", "period": "2026-09", "area_id": code,
                             "district_id": code[:8], "county_name": "臺北市", "area_name": "臺北市測試區",
                             "village_name": name, "population": 101, "age_counts": [1] * 101}]}

    def boundary(self, code="63000010001", name="測試里", vintage="2026-08-17", geometry=None):
        return {"area_id": code, "district_id": code[:8], "county_code": code[:5],
                "county_name": "臺北市", "district_name": "測試區", "village_name": name,
                "note": None, "boundary_layer": "VILLAGE_NLSC_1150817" if vintage else "Village_Sanhe",
                "boundary_vintage": vintage, "geometry": geometry if geometry is not None else box(121.5, 25, 121.51, 25.01)}

    def roster(self, code="63000010001", name="測試里"):
        return {code: {"village_code": code, "district_code": code[:8], "village_name": name, "source_row_number": 1}}

    def run_join(self, population=None, roster=None, boundaries=None):
        return module.reconcile(population or self.pop(), roster if roster is not None else self.roster(),
                                boundaries if boundaries is not None else [self.boundary()], [], "2026-09")

    def test_different_code_same_count_is_not_matching_roster(self):
        rows, features, summary = self.run_join(roster=self.roster("63000010002"))
        self.assertEqual(summary["population_missing_from_roster"], ["63000010001"])
        self.assertEqual(summary["roster_missing_from_population"], ["63000010002"])
        self.assertEqual(features[0]["properties"]["boundary_join_status"], "roster_identity_mismatch")
        self.assertFalse(next(row for row in rows if row["area_id"] == "63000010001")["allocation_eligible"])

    def test_literal_name_mismatch_retained_without_replacement(self):
        rows, features, _ = self.run_join(boundaries=[self.boundary(name="相似字里")])
        self.assertEqual(rows[0]["boundary_identity_status"], "name_mismatch")
        self.assertEqual(features[0]["properties"]["village_name"], "測試里")
        self.assertEqual(features[0]["properties"]["boundary_village_name"], "相似字里")

    def test_source_ages_and_open_ended_groups_retained(self):
        rows, features, summary = self.run_join()
        props = features[0]["properties"]
        self.assertEqual(props["age_counts"], [1] * 101)
        self.assertEqual(props["age_groups"]["age_75_plus"], 26)
        self.assertEqual(props["age_groups"]["age_65_plus"], 36)
        self.assertEqual(summary["population_sum"], 101)
        self.assertFalse(rows[0]["same_month_geometry_verified"])

    def test_missing_age_never_becomes_zero(self):
        population = self.pop(); population["records"][0]["age_counts"][100] = None
        _, features, _ = self.run_join(population=population)
        self.assertIsNone(features[0]["properties"]["age_groups"]["age_65_plus"])
        self.assertEqual(features[0]["properties"]["age_groups"]["age_0_14"], 15)

    def test_undated_supplement_and_both_overlap_sides_excluded(self):
        boundaries = [self.boundary(), self.boundary("63000010002", vintage=None)]
        rows, _, summary = self.run_join(boundaries=boundaries)
        self.assertEqual({row["boundary_join_status"] for row in rows}, {"geometry_conflict"})
        self.assertTrue(all(not row["allocation_eligible"] for row in rows))
        self.assertEqual(summary["excluded_source_population_known_sum"], 101)
        self.assertEqual(len(summary["supplemental_overlap_records"]), 1)
        self.assertIsNone(next(row for row in rows if row["area_id"].endswith("002"))["boundary_vintage"])

    def test_unassigned_alphanumeric_id_has_no_zero_population(self):
        unassigned = self.boundary("63000010S01", name=None)
        unassigned["note"] = "未編定村里"
        _, features, _ = self.run_join(boundaries=[self.boundary(), unassigned])
        props = next(feature["properties"] for feature in features if feature["properties"]["area_id"].endswith("S01"))
        self.assertEqual(props["boundary_join_status"], "unassigned")
        self.assertIsNone(props["population"])
        self.assertTrue(all(value is None for value in props["age_groups"].values()))

    def archive(self, directory, *, extra=False, supplemental=False, unassigned=False, unassigned_name=""):
        path = Path(directory) / "boundaries.zip"
        with ZipFile(path, "w") as archive:
            stems = ["VILLAGE_NLSC_1150817"] + (["Village_Sanhe"] if supplemental else []) + (["Unexpected"] if extra else [])
            for index, stem in enumerate(stems):
                buffers = {ext: io.BytesIO() for ext in ("shp", "shx", "dbf")}
                writer = shapefile.Writer(**buffers, shapeType=shapefile.POLYGON)
                for name in ("VILLCODE", "COUNTYCODE", "TOWNCODE", "COUNTYNAME", "TOWNNAME", "VILLNAME", "NOTE"):
                    writer.field(name, "C", 40)
                writer.poly([[[121.5, 25], [121.5, 25.01], [121.51, 25.01], [121.51, 25], [121.5, 25]]])
                code = "63000010S01" if unassigned else f"6300001000{index + 1}"
                writer.record(code, "63000", "63000010", "臺北市", "測試區", unassigned_name if unassigned else "測試里", "未編定村里" if unassigned else "")
                writer.close()
                for ext, buffer in buffers.items():
                    archive.writestr(stem + "." + ext, buffer.getvalue())
                archive.writestr(stem + ".prj", CRS.from_epsg(3824).to_wkt())
        return path

    def test_all_layers_read_and_supplement_not_given_main_vintage(self):
        with tempfile.TemporaryDirectory() as directory:
            rows, layers = module.archive_layers(self.archive(directory, supplemental=True))
        self.assertEqual(len(rows), 2)
        self.assertEqual({row["boundary_vintage"] for row in rows}, {None, "2026-08-17"})
        self.assertEqual(len(layers), 2)

    def test_unknown_layer_requires_review(self):
        with tempfile.TemporaryDirectory() as directory, self.assertRaisesRegex(ValueError, "every layer"):
            module.archive_layers(self.archive(directory, extra=True))

    def test_archive_preserves_unassigned_non_numeric_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            rows, _ = module.archive_layers(self.archive(directory, unassigned=True))
        self.assertEqual(rows[0]["area_id"], "63000010S01")

    def test_non_numeric_identity_with_named_village_is_not_unassigned(self):
        with tempfile.TemporaryDirectory() as directory, self.assertRaisesRegex(ValueError, "identity"):
            module.archive_layers(self.archive(directory, unassigned=True, unassigned_name="測試里"))


if __name__ == "__main__":
    unittest.main()
