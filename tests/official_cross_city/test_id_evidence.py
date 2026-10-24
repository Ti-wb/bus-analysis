import gzip
import json
from pathlib import Path
import tempfile
import unittest
from copy import deepcopy
from urllib.parse import urlencode

from scripts.official_cross_city import id_evidence as e


def branch(route_id=1, nid="0463000100"):
    return {"Id": route_id, "NId": nid, "pathAttributeId": route_id * 10, "nameZh": "A-B", "departureZh": "A", "destinationZh": "B",
            "goFirstBusTime": "0700", "goLastBusTime": "0800", "backFirstBusTime": "1700", "backLastBusTime": "1800"}


class IdEvidenceTest(unittest.TestCase):
    def csv_file(self, directory):
        path = Path(directory) / "frogs.csv"
        path.write_text("type,seqno,agency_codes,routename,start_and_end,weekday_operation_hours,holiday_operation_hours\n"
                        "跳蛙公車,987,382290000H,A-B,A-B,去程：0700-0800；返程：1700-1800,去程： - ；返程： - \n", encoding="utf-8")
        return path

    def test_full_field_equivalence_accepts_official_tuple_not_source_sequence(self):
        with tempfile.TemporaryDirectory() as directory:
            rows = e.leapfrog_equivalence(self.csv_file(directory), [branch()], [{"route_nid": "0463000100", "route_id": "1"}])
        self.assertEqual(rows[0]["source_sequence"], "987")
        self.assertEqual(rows[0]["route_id"], "1")
        self.assertEqual(rows[0]["matching_subroute_ids"], ["10"])

    def test_same_name_with_changed_schedule_keeps_id_null_and_records_conflict(self):
        with tempfile.TemporaryDirectory() as directory:
            route = {**branch(), "goLastBusTime": "0810"}
            row = e.leapfrog_equivalence(self.csv_file(directory), [route], [{"route_nid": "0463000100", "route_id": "1"}])[0]
        self.assertIsNone(row["route_id"])
        self.assertIn("weekday_clocks", row["conflicting_candidate_evidence"][0]["differing_fields"])

    def test_multiple_official_ids_with_same_content_are_not_arbitrarily_selected(self):
        with tempfile.TemporaryDirectory() as directory:
            rows = [branch(), branch(2, "0463000200")]
            category = [{"route_id": "1", "route_nid": "0463000100"}, {"route_id": "2", "route_nid": "0463000200"}]
            row = e.leapfrog_equivalence(self.csv_file(directory), rows, category)[0]
        self.assertIsNone(row["route_id"])

    def test_invalid_clock_or_missing_endpoints_fail_closed(self):
        with self.assertRaises(ValueError):
            e.hours("去程：2700-2800；返程： - ")
        with self.assertRaises(ValueError):
            e.content_fields({**branch(), "departureZh": None})

    def test_missing_nid_requires_authority_and_subroute_operator_tuple_agreement(self):
        tdx = [{"route_id": "1", "route_uid": "TPE1", "route_map_image_url": "https://ebus.gov.taipei/MapOverview?nid=null",
                "route_name": {"zh_tw": "A-B"}, "sub_routes": [{"sub_route_id": "10", "operator_i_ds": ["7"]}]}]
        direct = [{"route_id": "1", "subroute_id": "10", "operator_id": "7", "route_name_zh": "A-B"}]
        xml = [{"authority_code": "TPE", "route_id": "1", "subroute_id": "10", "operator_id": "7", "route_name": "A-B"}]
        self.assertEqual(e.missing_nid_routes(tdx, direct, xml)[0]["reference_route_id"], "1")
        with self.assertRaises(ValueError):
            e.missing_nid_routes(tdx, direct, [{**xml[0], "operator_id": "8"}])
        with self.assertRaises(ValueError):
            e.missing_nid_routes(tdx, direct, [{**xml[0], "authority_code": "NWT"}])

    def test_route_page_requires_explicit_nid_and_preserves_null_oneway_array(self):
        body = {"UniRouteId": "0463000100", "GoDirStops": [{"UniStopId": "stop"}], "BackDirStops": None}
        html = "var routeJsonString = JSON.stringify(" + json.dumps(body) + ");"
        self.assertIsNone(e.route_page(html, "0463000100")["BackDirStops"])
        with self.assertRaises(ValueError):
            e.route_page(html, "0463000200")
        with self.assertRaises(ValueError):
            e.route_page('var routeJsonString = JSON.stringify({"UniRouteId":"0463000100"});', "0463000100")

    def test_today_status_and_physical_nid_do_not_invent_branch_or_permanent_withdrawal(self):
        missing = [{"stop_id": "3", "route_id": "1", "stop_location_id": "9"}]
        stops = [{"Id": 3, "routeId": 1, "stopLocationId": 9, "nameZh": "A", "seqNo": 2, "goBack": "0"}]
        locations = [{"id": 9, "nid": "explicit_station_nid"}]
        pages = {"1": {"GoDirStops": [{"UniStopId": "explicit_station_nid", "Name": "A"}], "BackDirStops": None}}
        eta = [{"RouteID": 1, "StopID": 3, "EstimateTime": "-4"}]
        result = e.stop_evidence(missing, [], stops, locations, [], eta, pages, {"1": "0463000100"})[0]
        self.assertEqual(result["today_service_status"], "official_not_operating_today")
        self.assertEqual(result["route_page_relation"], "verified_explicit_stop_location_nid")
        self.assertIsNone(result["subroute_id"])
        self.assertEqual(result["permanent_withdrawal_status"], "not_established")
        result = e.stop_evidence(missing, [], stops, locations, [], [{**eta[0], "EstimateTime": "-1"}], pages, {"1": "0463000100"})[0]
        self.assertEqual(result["today_service_status"], "requires_review")

    def test_unsafe_xml_is_rejected_without_entity_resolution(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bad.gz"
            path.write_bytes(gzip.compress(b'<!DOCTYPE x [<!ENTITY a "bad">]><x/>'))
            with self.assertRaises(ValueError):
                e.fare_rows(path)


class StopSequenceEvidenceTest(unittest.TestCase):
    def fixture(self):
        routes = [{"Id": 1, "pathAttributeId": 10, "providerId": 7, "nameZh": "route", "pathAttributeName": "branch"}]
        stops = [{"Id": sid, "routeId": 1, "stopLocationId": 20 + sid, "nameZh": "stop" + str(sid), "seqNo": index,
                  "goBack": "1", "longitude": "121.5", "latitude": "25.0"} for index, sid in enumerate([3, 4])]
        paths = [{"id": 100 + sid, "pathAttributeId": 10, "stopId": sid, "sequenceNo": index + 9,
                  "type": "0", "typeAttribute": ""} for index, sid in enumerate([3, 4])]
        model = {"RouteID": "1", "RouteUID": "NWT1", "RouteName": {"Zh_tw": "route"}, "SubRouteID": "10", "SubRouteUID": "NWT10",
                 "SubRouteName": {"Zh_tw": "branch"}, "Direction": 1, "City": "NewTaipei", "CityCode": "NWT", "UpdateTime": "2026-10-24T09:00:00+08:00", "VersionID": 1,
                 "Operators": [{"OperatorID": "7"}], "Stops": [
                     {"StopID": str(sid), "StopUID": "NWT" + str(sid), "StationID": str(20 + sid), "StopName": {"Zh_tw": "stop" + str(sid)},
                      "StopSequence": index + 1, "StopPosition": {"PositionLon": 121.5, "PositionLat": 25.0}} for index, sid in enumerate([3, 4])]}
        return [("page", 1, model)], routes, stops, paths, []

    def test_direction_comes_from_getstop_not_pathdetail_type_or_ordinal_offset(self):
        memberships, sequences, _ = e.stop_sequence_evidence(*self.fixture())
        self.assertEqual([r["direction"] for r in memberships], ["1", "1"])
        self.assertEqual([r["direct_pathdetail_sequence_no"] for r in memberships], [9, 10])
        self.assertEqual(sequences[0]["status"], "verified_full_stop_id_sequence")

    def test_only_explicit_virtual_annotation_classifies_omission_and_gaps_are_preserved(self):
        args = self.fixture()
        args[2][0]["nameZh"] = "路段(虛擬站不停靠)"
        args[0][0][2]["Stops"] = args[0][0][2]["Stops"][1:]
        _, sequences, _ = e.stop_sequence_evidence(*args)
        self.assertEqual(sequences[0]["tdx_stop_sequence_values"], [2])
        self.assertFalse(sequences[0]["tdx_sequence_is_contiguous_one_based"])
        self.assertEqual(sequences[0]["status"], "verified_order_with_explicit_non_boarding_virtual_points_omitted")
        args[2][0]["nameZh"] = "temporarily absent"
        _, sequences, _ = e.stop_sequence_evidence(*args)
        self.assertEqual(sequences[0]["status"], "requires_source_review_sequence_conflict")

    def test_source_coordinate_conflict_is_preserved_without_changing_id_acceptance(self):
        args = self.fixture()
        args[0][0][2]["Stops"][0]["StopPosition"]["PositionLon"] = 121.51
        memberships, _, _ = e.stop_sequence_evidence(*args)
        self.assertEqual(memberships[0]["position_comparison"], "source_position_conflict")
        self.assertEqual(memberships[0]["direct_position"]["longitude"], 121.5)
        self.assertEqual(memberships[0]["tdx_position"]["longitude"], 121.51)
        self.assertEqual(memberships[0]["status"], "verified_explicit_internal_id_and_membership")

    def test_wrong_authority_station_direction_and_duplicate_page_models_rejected(self):
        for field, value in [("StopUID", "TPE3"), ("StationID", "999")]:
            args = self.fixture()
            args[0][0][2]["Stops"][0][field] = value
            with self.assertRaises(ValueError):
                e.stop_sequence_evidence(*args)
        args = self.fixture()
        args[0][0][2]["Direction"] = 0
        with self.assertRaises(ValueError):
            e.stop_sequence_evidence(*args)
        args = self.fixture()
        args[0].append(deepcopy(args[0][0]))
        with self.assertRaises(ValueError):
            e.stop_sequence_evidence(*args)

    def test_absence_accounting_does_not_invent_subroute_or_operational_cause(self):
        args = self.fixture()
        args[2].append({"Id": 5, "routeId": 1, "stopLocationId": 25, "nameZh": "absent"})
        args[4].append({"stop_id": "5", "route_id": "1", "stop_location_id": "25"})
        _, _, accounts = e.stop_sequence_evidence(*args)
        self.assertEqual(accounts[0]["tdx_membership_count"], 0)
        self.assertIsNone(accounts[0]["subroute_id"])
        self.assertIsNone(accounts[0]["operational_cause"])

    def test_retained_pagination_requires_actual_short_tail_and_matching_storage_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "page.json.gz"
            raw = b'[{}]'
            path.write_bytes(gzip.compress(raw))
            receipt = {"scope": "NewTaipei", "service": "StopOfRoute", "source_id": "page", "raw_file": "page.json.gz",
                       "http_status": 200, "status": "downloaded", "storage_encoding": "gzip", "stored_bytes": path.stat().st_size,
                       "bytes": len(raw), "record_count": 1, "top": 2, "skip": 0,
                       "download_url": "https://tdx.transportdata.tw/api/basic/v2/Bus/StopOfRoute/City/NewTaipei?" + urlencode(
                           {"$format": "JSON", "$top": 2, "$skip": 0, "$orderby": "RouteUID,SubRouteUID,Direction"})}
            manifest = root / "raw_data/official_cross_city/service_coverage/manifest.json"
            manifest.parent.mkdir(parents=True)
            manifest.write_text(json.dumps({"records": [receipt]}))
            self.assertEqual(len(e.retained_tdx_stop_models(root)[0]), 1)
            receipt["top"] = 1
            receipt["download_url"] = receipt["download_url"].replace("%24top=2", "%24top=1")
            manifest.write_text(json.dumps({"records": [receipt]}))
            with self.assertRaises(ValueError):
                e.retained_tdx_stop_models(root)
            receipt["top"] = 2
            receipt["download_url"] = receipt["download_url"].replace("%24top=1", "%24top=2")
            receipt["record_count"] = 2
            manifest.write_text(json.dumps({"records": [receipt]}))
            with self.assertRaises(ValueError):
                e.retained_tdx_stop_models(root)
            receipt["record_count"] = 1
            receipt["stored_bytes"] += 1
            manifest.write_text(json.dumps({"records": [receipt]}))
            with self.assertRaises(ValueError):
                e.retained_tdx_stop_models(root)


if __name__ == "__main__":
    unittest.main()
