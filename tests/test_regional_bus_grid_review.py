"""Independent-audit regressions use hand-counted fixtures, without a generator."""
from contextlib import redirect_stdout
import copy
import gzip
from io import StringIO
import json
import math
from pathlib import Path
import tempfile
import unittest

from pyproj import Geod
from shapely.geometry import box, mapping

from scripts import review_regional_bus_grid as review

CALENDAR = {day: int(day in {"monday", "tuesday", "wednesday", "thursday", "friday"})
            for day in review.WEEK}


def schedule(operator="OP1", *, frequency=False):
    entry = {"service_day": dict(CALENDAR)}
    entry.update({"start_time": "08:00", "end_time": "09:00", "min_headway_mins": 10, "max_headway_mins": 10}
                 if frequency else {"trip_id": "1", "stop_times": [{"departure_time": "08:00"}]})
    return {"route_uid": "TPE1", "sub_route_uid": "TPE10", "direction": 0, "operator_id": operator,
            "frequencys" if frequency else "timetables": [entry]}


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps(value).encode()
    path.write_bytes(gzip.compress(body, mtime=0) if path.suffix == ".gz" else body)


def fixture(root, *, unknown=False):
    """One tiny administrative polygon falls inside one cell at every size.

    OP1 has a timetable and a redundant frequency; OP2 has only a frequency.
    The independently hand-counted weekday total is 1 + 6 = 7, weekends 0.
    """
    source_metadata = []
    stop_row = {"route_uid": "TPE1", "sub_route_uid": "TPE10", "direction": 0,
                "stops": [{"stop_uid": "TPE_S1", "stop_position": {"position_lon": 121.500005, "position_lat": 25.050005}}]}
    for scope in review.SCOPES:
        for endpoint in ("StopOfRoute", "Schedule"):
            relative = f"data/official_cross_city/service_coverage/{scope}_{endpoint}.json.gz"
            suffix = "/InterCity" if scope == "InterCity" else "/City/" + scope
            receipt = {"scope": scope, "service": endpoint, "source_page": "https://tdx.transportdata.tw/api-service/swagger/basic/test",
                       "download_url": "https://tdx.transportdata.tw/api/basic/v2/Bus/" + endpoint + suffix,
                       "downloaded_at": "2026-10-24T12:00:00+08:00", "source_updated_at": None,
                       "raw_file": "raw_data/fixture.json"}
            records = []
            if scope == "Taipei":
                if endpoint == "StopOfRoute":
                    records = [stop_row, copy.deepcopy(stop_row)]
                elif not unknown:
                    records = [schedule(), schedule(frequency=True), schedule("OP2", frequency=True), schedule()]
            meta = {"sources": [receipt], "scope": scope, "service": endpoint,
                    "source_updated_at": None, "source_record_update_times": [], "statistical_period": None,
                    "record_count": len(records), "scope_status": "complete_published_endpoint_by_exhausted_page",
                    "grain": "one published route/subroute/direction/operator model"}
            write_json(root / relative, {"schema_version": 1, "dataset_id": f"fixture_{scope}_{endpoint}",
                                         "metadata": meta, "records": records})
            source_metadata.append({"scope": scope, "service": endpoint, "path": relative, "source_updated_at": None,
                                    "source_record_update_times": [], "source_pages": [receipt["source_page"]],
                                    "download_urls": [receipt["download_url"]], "raw_files": [receipt["raw_file"]],
                                    "records": len(records)})
    polygon = mapping(box(121.5, 25.05, 121.50001, 25.05001))
    meta = {"crs": "EPSG:4326", "coordinate_order": ["longitude", "latitude"], "source_period": "2026-08-17", "sources": []}
    write_json(root / review.GEOMETRIES[0], {"metadata": meta, "features": [
        {"properties": {"admin_code": code}, "geometry": polygon} for code in ("63000", "65000")]})
    write_json(root / review.GEOMETRIES[1], {"metadata": meta, "features": [
        {"properties": {"county_code": "10017"}, "geometry": polygon}]})
    for size in review.EXPECTED:
        geod = Geod(ellps="GRS80")
        dx = size / geod.inv(121.5, 25.05, 121.51, 25.05)[2] * .01
        dy = size / geod.inv(121.5, 25.05, 121.5, 25.06)[2] * .01
        ox, oy = math.floor(121.5 / dx) * dx, math.floor(25.05 / dy) * dy
        weekday, weekend = [0.] * 24, [0.] * 24
        weekday[8] = 7.
        cells = [[0, 0, 1, 1, int(unknown), *([None] * 3 if unknown else [weekday, weekend, list(weekend)])]]
        status = "no_schedule" if unknown else "ok"
        meta = {"source_data_time": None, "statistical_period": None, "units": dict(review.UNITS),
                "cell": f"{size} m; 350 m corridor" if size < 250 else f"{size} m full mask",
                "scope_city_codes": list(review.CITY_CODES), "geometry_sources": list(review.GEOMETRIES),
                "sources": source_metadata, "operator_ambiguous_service_models": 0, "sequence_subroutes": 1,
                "scheduled_subroutes": int(not unknown), "limitations": ["synthetic hand-counted fixture"],
                "built_at": "2026-10-24T12:00:00+08:00",
                "grid_definition": {"origin": [round(ox, 7), round(oy, 7)], "step": [dx, dy],
                                    "cell_m": size, "fields": list(review.FIELDS)},
                "status_counts": {k: int(k == status) for k in ("ok", "partial", "no_stop", "no_schedule")}}
        write_json(root / f"web/public/data/regional_bus_grid_{size}.json", {
            "schema_version": 1, "dataset_id": f"regional_bus_grid_{size}", "metadata": meta, "records": cells})


class IndependentRegionalGridTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        fixture(self.root)

    def mutate_grid(self, update, size=100):
        path = self.root / f"web/public/data/regional_bus_grid_{size}.json"
        doc = json.loads(path.read_text())
        update(doc)
        write_json(path, doc)

    def failures(self):
        result = review.audit(self.root)
        self.assertEqual(result["metadata"]["status"], "failed")
        return str(result)

    def test_all_four_grids_reconcile_hand_counted_operator_totals(self):
        result = review.audit(self.root)
        self.assertEqual(result["metadata"]["status"], "passed")
        self.assertEqual(result["metadata"]["record_count"], 4)
        for row in result["records"]:
            self.assertEqual(row["record_count"], 1)
            self.assertEqual(row["aggregates"]["weekday"]["daily_planned_departures_summed_over_cells"], 7)
            self.assertEqual(row["counts"]["stop_cell_memberships"], 1)
        self.assertIsNone(result["metadata"]["source_inputs"][0]["source_updated_at"])

    def test_required_envelope_fields_cannot_be_omitted(self):
        for field in ("schema_version", "dataset_id", "metadata", "records"):
            with self.subTest(field=field):
                fixture(self.root)
                self.mutate_grid(lambda d: d.pop(field))
                self.assertIn("required grid envelope missing or invalid", self.failures())

    def test_legacy_compact_document_and_duplicate_cells_are_rejected(self):
        def legacy(doc):
            definition = doc["metadata"].pop("grid_definition")
            cells = doc["records"]
            meta = doc["metadata"]
            doc.clear()
            doc.update({**definition, "metadata": meta, "cells": cells})
        self.mutate_grid(legacy)
        self.assertIn("required grid envelope missing or invalid", self.failures())
        fixture(self.root)
        self.mutate_grid(lambda d: d.__setitem__("cells", d["records"]))
        self.assertIn("required grid envelope missing or invalid", self.failures())

    def test_envelope_version_and_dataset_identity_are_checked(self):
        for field, value in (("schema_version", True), ("schema_version", 1.0),
                             ("schema_version", 2), ("dataset_id", "regional_bus_grid_500")):
            with self.subTest(field=field, value=value):
                fixture(self.root)
                self.mutate_grid(lambda d: d.__setitem__(field, value))
                self.assertIn("required grid envelope missing or invalid", self.failures())

    def test_grid_definition_fields_cannot_be_omitted(self):
        for field in ("origin", "step", "cell_m", "fields"):
            with self.subTest(field=field):
                fixture(self.root)
                self.mutate_grid(lambda d: d["metadata"]["grid_definition"].pop(field))
                self.assertIn("grid definition/schema drift", self.failures())

    def test_built_at_requires_iso_datetime_with_timezone(self):
        for value in (None, "", "not-a-date", "2026-10-24", "2026-10-24T12:00:00"):
            with self.subTest(value=value):
                fixture(self.root)
                self.mutate_grid(lambda d: d["metadata"].__setitem__("built_at", value))
                self.assertIn("built_at must be an ISO datetime with timezone", self.failures())
        fixture(self.root)
        self.mutate_grid(lambda d: d["metadata"].__setitem__("built_at", "2026-10-24T04:00:00Z"))
        self.assertEqual(review.audit(self.root)["metadata"]["status"], "passed")

    def test_numeric_tampering_is_rejected_despite_valid_structure(self):
        self.mutate_grid(lambda d: d["records"][0][5].__setitem__(8, 1.0))
        self.assertIn("hourly departures mismatch", self.failures())

    def test_unknown_is_not_accepted_as_zero(self):
        fixture(self.root, unknown=True)
        self.mutate_grid(lambda d: d["records"][0].__setitem__(5, [0.] * 24))
        self.assertIn("unknown/null changed to numeric", self.failures())

    def test_all_unknown_delivery_fails_minimum_service_gate(self):
        fixture(self.root, unknown=True)
        self.assertIn("no published service available", self.failures())

    def test_known_series_cannot_disappear(self):
        self.mutate_grid(lambda d: d["records"][0].__setitem__(5, None))
        self.assertIn("missing known", self.failures())

    def test_null_source_update_cannot_be_replaced_by_download_time(self):
        self.mutate_grid(lambda d: d["metadata"]["sources"][0].__setitem__("source_updated_at", "2026-10-24T12:00:00+08:00"))
        self.assertIn("source metadata drift", self.failures())

    def test_required_null_metadata_field_cannot_be_omitted(self):
        self.mutate_grid(lambda d: d["metadata"].pop("statistical_period"))
        self.assertIn("required source period/units fields missing", self.failures())

    def test_source_record_update_claim_is_reconciled_to_records(self):
        path = self.root / "data/official_cross_city/service_coverage/Taipei_Schedule.json.gz"
        doc = review.load(path)
        doc["metadata"]["source_updated_at"] = "2026-10-24T12:00:00+08:00"
        write_json(path, doc)
        self.assertIn("source update time differs", self.failures())

    def test_missing_mask_cell_is_rejected_even_if_status_counts_are_adjusted(self):
        def update(doc):
            doc["records"] = []
            doc["metadata"]["status_counts"]["ok"] = 0
        self.mutate_grid(update)
        self.assertIn("geographic coverage incomplete", self.failures())

    def test_stop_mapping_and_geometry_drift_are_rejected(self):
        self.mutate_grid(lambda d: d["records"][0].__setitem__(2, 2))
        self.assertIn("stop/service/unknown count mismatch", self.failures())
        fixture(self.root)
        self.mutate_grid(lambda d: d["metadata"]["grid_definition"]["origin"].__setitem__(0, 121.0))
        self.assertIn("grid origin/step mismatch", self.failures())

    def test_invalid_numeric_type_is_rejected(self):
        self.mutate_grid(lambda d: d["records"][0][5].__setitem__(8, True))
        self.assertIn("invalid weekday hourly series", self.failures())

    def test_missing_calendar_produces_unknown_not_zero(self):
        row = schedule()
        del row["timetables"][0]["service_day"]["tuesday"]
        self.assertEqual(review.recompute_service([row]), {d: None for d in review.DAYS})
        self.assertEqual(review.recompute_service([schedule()])["saturday"], [0.] * 24)

    def test_no_stop_times_requires_explicit_origin_departure(self):
        row = schedule()
        row["timetables"][0].pop("stop_times")
        row["timetables"][0]["arrival_time"] = "08:00"
        self.assertIsNone(review.recompute_service([row])["weekday"])

    def test_midnight_frequency_and_same_operator_precedence(self):
        row = schedule(frequency=True)
        row["frequencys"][0].update({"start_time": "23:30", "end_time": "00:30"})
        values = review.recompute_service([row])["weekday"]
        self.assertEqual((values[23], values[0], sum(values)), (3, 3, 6))
        self.assertEqual(review.recompute_service([schedule(), schedule(frequency=True)])["weekday"][8], 1)
        self.assertEqual(review.recompute_service([schedule(), schedule("OP2", frequency=True)])["weekday"][8], 7)

    def test_cli_replaces_old_pass_with_failed_report_and_nonzero_exit(self):
        output = self.root / review.REPORT
        with redirect_stdout(StringIO()):
            self.assertEqual(review.main(["--root", str(self.root)]), 0)
        self.assertEqual(json.loads(output.read_text())["metadata"]["status"], "passed")
        (self.root / "web/public/data/regional_bus_grid_500.json").unlink()
        with redirect_stdout(StringIO()):
            self.assertEqual(review.main(["--root", str(self.root)]), 1)
        result = json.loads(output.read_text())
        self.assertEqual(set(result), {"schema_version", "dataset_id", "metadata", "records"})
        self.assertEqual(result["metadata"]["status"], "failed")
        self.assertEqual(result["records"][-1]["status"], "failed")

    def test_cli_replaces_old_pass_when_nested_source_shape_is_malformed(self):
        output = self.root / review.REPORT
        path = self.root / "data/official_cross_city/service_coverage/Taipei_Schedule.json.gz"
        for malformed in (None, {"route_uid": "TPE1", "sub_route_uid": "TPE10", "timetables": [None]}):
            with self.subTest(malformed=malformed), redirect_stdout(StringIO()):
                fixture(self.root)
                self.assertEqual(review.main(["--root", str(self.root)]), 0)
                document = review.load(path)
                document["records"] = [malformed]
                document["metadata"]["record_count"] = 1
                write_json(path, document)
                self.assertEqual(review.main(["--root", str(self.root)]), 1)
                result = json.loads(output.read_text())
                self.assertEqual(result["metadata"]["status"], "failed")
                self.assertTrue(result["metadata"]["failures"])


if __name__ == "__main__":
    unittest.main()
