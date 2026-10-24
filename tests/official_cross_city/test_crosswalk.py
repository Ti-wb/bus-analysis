import copy
import gzip
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.official_cross_city import crosswalk as c


def route(route_id=1, branch_id=10, nid="0463000100"):
    return {"Id": route_id, "pathAttributeId": branch_id, "providerId": 7,
            "nameZh": "同名路線", "NId": nid, "pathAttributeNId": nid[:8] + "01",
            "roadMapUrl": "https://ebus.gov.taipei/MapOverview?nid=" + nid}


def tdx(route_id="1", nid="0463000100"):
    return {"route_id": route_id, "route_uid": "NWT" + route_id, "city": "NewTaipei",
            "route_map_image_url": "https://ebus.gov.taipei/MapOverview?nid=" + nid}


class CrosswalkTest(unittest.TestCase):
    def test_category_accepts_explicit_nid_and_lettered_route(self):
        html = "<a href='#collapse8'>Jump Frog Bus</a><div id='collapse8'><a href=\"javascript:go('04540152A0')\">same name</a></div>"
        self.assertEqual(c.leapfrog_nids(html), [("04540152A0", "same name")])

    def test_category_requires_one_explicit_panel_and_no_duplicate_ids(self):
        for html in ["<p>跳蛙 0463000100</p>",
                     "<a href='#collapse8'>Jump Frog Bus</a><div id='collapse8'><a href=\"javascript:go('0463000100')\">a</a><a href=\"javascript:go('0463000100')\">b</a></div>",
                     "<a href='#collapse8'>Jump Frog Bus</a><a href='#collapse9'>跳蛙公車</a>"]:
            with self.subTest(html=html), self.assertRaises(ValueError):
                c.leapfrog_nids(html)

    def test_map_bridge_never_accepts_null_generic_foreign_or_multi_nid_url(self):
        for url in [None, "https://ebus.gov.taipei/MapOverview?nid=null",
                    "https://example.com/MapOverview?nid=0463000100",
                    "https://ebus.gov.taipei/image.png", "https://ebus.gov.taipei/MapOverview?bad",
                    "https://ebus.gov.taipei/MapOverview?nid=0463000100&nid=0463000200",
                    "http://ebus.gov.taipei/MapOverview?nid=0463000100"]:
            with self.subTest(url=url):
                self.assertIsNone(c.route_map_key(url))

    def test_csv_feed_checks_all_fields_and_normalizes_only_empty_to_null(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "routes.csv"
            path.write_text("id,providerid,namezh,pathattributeid,extra\n1,7,同名路線,10,\n", encoding="utf-8")
            feed = [{**route(), "extra": None}]
            _, quality = c.compare_csv_feed(path, "routes", feed)
            self.assertEqual(quality["all_csv_columns_verified_rows"], 1)
            feed[0]["nameZh"] = "other"
            with self.assertRaisesRegex(ValueError, "content conflict"):
                c.compare_csv_feed(path, "routes", feed)

    def test_explicit_route_bridge_preserves_multiple_operators_and_subroutes(self):
        feed = [route(), {**route(branch_id=11), "providerId": 8}, route()]
        result = c.route_bridge(feed, {})
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]["source_record_numbers"], [1, 3])
        self.assertEqual(result[1]["subroute_id"], "11")

    def test_csv_feed_rejects_unmatched_routes_and_variants(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "routes.csv"
            path.write_text("id,providerid,namezh,pathattributeid\n1,7,同名路線,10\n", encoding="utf-8")
            for extra in [route(route_id=2, branch_id=20, nid="0463000200"),
                          route(branch_id=11), {**route(), "providerId": 8}]:
                with self.subTest(extra=extra), self.assertRaisesRegex(ValueError, "unmatched feed"):
                    c.compare_csv_feed(path, "routes", [route(), extra])
            _, quality = c.compare_csv_feed(path, "routes", [route(), route()])
            self.assertEqual(quality["feed_source_rows"], 2)

    def test_unmatched_feed_route_cannot_replace_verified_crosswalk(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            raw = root / c.RAW_DIR
            raw.mkdir(parents=True)
            (raw / "ntpc_routes.csv").write_text("id,providerid,namezh,pathattributeid\n1,7,同名路線,10\n")
            for name, rows in [("route_feed", [route(), route(2, 20, "0463000200")]),
                               ("stop_feed", [{"Id": 5}]), ("pathdetail", [{"id": 6}])]:
                (raw / ("ntpc_" + name + ".gz")).write_bytes(gzip.compress(json.dumps({"EssentialInfo": {}, "BusInfo": rows}).encode()))
            output = root / "data/official_cross_city/ntpc_feed_id_crosswalk.json"
            output.parent.mkdir(parents=True)
            output.write_text("previous successful output")
            with patch.object(c, "source_file", side_effect=lambda root, name, **kw: (raw / name, {})):
                with self.assertRaisesRegex(ValueError, "unmatched feed"):
                    c.run(root, offline=True)
            self.assertEqual(output.read_text(), "previous successful output")

    def test_route_bridge_rejects_ambiguous_nid_conflicting_parent_and_map(self):
        cases = [[route(), route(route_id=2)],
                 [route(), route(route_id=2, nid="0463000200")],
                 [{**route(), "roadMapUrl": "https://ebus.gov.taipei/MapOverview?nid=0463000200"}]]
        for feed in cases:
            with self.subTest(feed=feed), self.assertRaises(ValueError):
                c.route_bridge(feed, {})

    def test_tdx_requires_unique_official_map_and_explicit_id_agreement(self):
        direct = c.route_bridge([route(), route(branch_id=11)], {})
        good = c.tdx_crosswalk([tdx()], direct, "ntpcbus")
        self.assertEqual(good[0]["reference_route_id"], "1")
        for rows in [[tdx("2")], [tdx(), tdx("2")], [tdx(nid="null")]]:
            with self.subTest(rows=rows):
                self.assertTrue(all(r["reference_route_id"] is None for r in c.tdx_crosswalk(rows, direct, "ntpcbus")))

    def test_unlinked_stops_remain_unknown_even_when_name_says_suspended(self):
        stops = [{"Id": 1, "routeId": 2, "stopLocationId": 3, "nameZh": "A(暫時裁撤)", "seqNo": 4, "goBack": "1"},
                 {"Id": 5, "routeId": 2, "stopLocationId": 3, "nameZh": "A", "seqNo": 5, "goBack": "1"}]
        paths = [{"id": 6, "pathAttributeId": 7, "stopId": 5, "sequenceNo": 0, "type": "1"}]
        rows, quality = c.stop_audit(stops, paths, {})
        self.assertEqual(quality["unlinked_stop_count"], 1)
        self.assertIsNone(rows[0]["subroute_id"])
        self.assertEqual(rows[0]["cause_status"], "not_verified_source_label_only")

    def test_stop_audit_rejects_unknown_or_duplicate_path_identity(self):
        stops = [{"Id": 5, "routeId": 2, "stopLocationId": 3, "nameZh": "A", "seqNo": 5}]
        row = {"id": 6, "pathAttributeId": 7, "stopId": 5, "sequenceNo": 0, "type": "1"}
        for paths in [[row, row], [{**row, "stopId": 999}]]:
            with self.subTest(paths=paths), self.assertRaises(ValueError):
                c.stop_audit(stops, paths, {})

    def test_tdx_reference_requires_official_receipt_count_and_complete_scope(self):
        obj = {"schema_version": 1, "records": [tdx()], "metadata": {"record_count": 1,
               "scope_status": "complete_requested_route_master_by_response_count", "sources": [{
               "download_url": "https://tdx.transportdata.tw/api/historical/v2/Historical/Bus/Route/Date/2026-10-23/City/NewTaipei?%24top=100000",
               "http_status": 200, "downloaded_at": "2026-10-24T10:00:00+08:00"}]}}
        self.assertEqual(c.validate_tdx_reference(obj)[0], "NewTaipei")
        for change in [{"record_count": 2}, {"scope_status": "sample_only"}, {"sources": []}]:
            invalid = copy.deepcopy(obj)
            invalid["metadata"].update(change)
            with self.subTest(change=change), self.assertRaises(ValueError):
                c.validate_tdx_reference(invalid)

    def test_plan_only_does_not_download(self):
        with patch.object(c, "source_file") as fetch, patch("builtins.print"):
            c.main([])
        fetch.assert_not_called()

    def test_wrong_offline_receipt_cannot_be_reused(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            p = root / c.RAW_DIR / "ntpc_routes.csv"
            p.parent.mkdir(parents=True)
            p.write_text("abc")
            (p.parent / (p.name + ".source.json")).write_text(json.dumps({"bytes": 3, "http_status": 200}))
            with self.assertRaisesRegex(ValueError, "provenance"):
                c.source_file(root, p.name, offline=True)

    def test_late_invalid_tdx_reference_preserves_previous_outputs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            raw = root / c.RAW_DIR
            raw.mkdir(parents=True)
            (raw / "ntpc_routes.csv").write_text("id,providerid,namezh,pathattributeid\n1,7,同名路線,10\n")
            (raw / "ntpc_stops.csv").write_text("id,routeid,namezh,seqno,pgp,goback,longitude,latitude,stoplocationid\n5,1,A,0,0,0,121.5,25,3\n")
            (raw / "ntpc_leapfrog.csv").write_text("type,seqno,agency_codes,routename,start_and_end,weekday_operation_hours,holiday_operation_hours\n跳蛙公車,9,agency,同名路線,A-B,description,description\n")
            stop = {"Id": 5, "routeId": 1, "nameZh": "A", "seqNo": 0, "pgp": "0", "goBack": "0", "longitude": "121.5", "latitude": "25", "stopLocationId": 3}
            path = {"id": 6, "pathAttributeId": 10, "stopId": 5, "sequenceNo": 0, "type": "0"}
            for name, rows in [("route_feed", [route()]), ("stop_feed", [stop]), ("pathdetail", [path])]:
                (raw / ("ntpc_" + name + ".gz")).write_bytes(gzip.compress(json.dumps({"EssentialInfo": {}, "BusInfo": rows}).encode()))
            (raw / "ebus_ntpc_routes.html").write_text("<a href='#collapse8'>Jump Frog Bus</a><div id='collapse8'><a href=\"javascript:go('0463000100')\">route</a></div>")
            output = root / "data/official_cross_city/ntpc_feed_id_crosswalk.json"
            output.parent.mkdir(parents=True)
            output.write_text("previous successful output")
            invalid = root / "invalid.json"
            invalid.write_text('{"schema_version":1,"records":[]}')
            with patch.object(c, "source_file", side_effect=lambda root, name, **kw: (raw / name, {})):
                with self.assertRaisesRegex(ValueError, "TDX route snapshot"):
                    c.run(root, offline=True, tdx_paths=[invalid])
            self.assertEqual(output.read_text(), "previous successful output")


if __name__ == "__main__":
    unittest.main()
