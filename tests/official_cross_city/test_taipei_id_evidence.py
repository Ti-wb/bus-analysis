import unittest

from scripts.official_cross_city.taipei_id_evidence import reconcile, separation_m


def fixture():
    direct = {"GetRoute": [{"Id": 10, "pathAttributeId": 20, "providerId": 30}],
              "GetStop": [{"Id": 40, "routeId": 10, "goBack": "0", "stopLocationId": 50,
                           "nameZh": "甲站", "longitude": "121.5", "latitude": "25", "pgp": "", "seqNo": 0}],
              "GetStopLocation": [{"id": 50}],
              "GetPathDetail": [{"pathAttributeId": 20, "stopId": 40, "sequenceNo": 0}]}
    models = [{"route_id": "10", "route_uid": "TPE10", "sub_route_id": "20", "sub_route_uid": "TPE20", "direction": 0,
               "operators": [{"operator_id": "30"}], "stops": [{"stop_id": "40", "stop_uid": "TPE40", "station_id": "50",
               "stop_name": {"zh_tw": "甲站"}, "stop_sequence": 1, "stop_boarding": 0,
               "stop_position": {"position_lon": 121.5, "position_lat": 25}}]}]
    return direct, models


class TaipeiIdentityTests(unittest.TestCase):
    def test_explicit_identity_keeps_missing_boarding_and_original_ordinals(self):
        direct, models = fixture()
        members, sequences, unlisted = reconcile(direct, models)
        self.assertEqual(members[0]["status"], "verified_explicit_source_identity")
        self.assertIsNone(members[0]["direct_boarding_code"])
        self.assertEqual(members[0]["tdx_boarding_code"], 0)
        self.assertIsNone(members[0]["station_uid"])
        self.assertEqual(sequences[0]["direct_path_sequence_values"], [0])
        self.assertEqual(sequences[0]["tdx_stop_sequence_values"], [1])
        self.assertEqual(unlisted, [])

    def test_names_and_positions_cannot_substitute_for_a_different_id(self):
        direct, models = fixture()
        models[0]["stops"][0].update(stop_id="41", stop_uid="TPE41")
        members, sequences, unlisted = reconcile(direct, models)
        self.assertEqual(members[0]["status"], "requires_review_identifier_relation")
        self.assertEqual(sequences[0]["status"], "requires_review_sequence_difference")
        self.assertEqual(unlisted[0]["stop_id"], "40")

    def test_same_numeric_id_in_another_authority_is_rejected(self):
        direct, models = fixture()
        models[0]["stops"][0]["stop_uid"] = "NWT40"
        with self.assertRaises(ValueError):
            reconcile(direct, models)

    def test_station_direction_or_operator_conflict_does_not_accept_identity(self):
        for change in ("station", "direction", "operator"):
            direct, models = fixture()
            if change == "station":
                models[0]["stops"][0]["station_id"] = "51"
            elif change == "direction":
                models[0]["direction"] = 1
            else:
                models[0]["operators"][0]["operator_id"] = "31"
            with self.subTest(change=change):
                self.assertEqual(reconcile(direct, models)[0][0]["status"], "requires_review_identifier_relation")

    def test_only_explicit_virtual_label_can_explain_an_omitted_point(self):
        direct, models = fixture()
        direct["GetStop"].append({**direct["GetStop"][0], "Id": 41, "nameZh": "乙站(虛擬站不停靠)"})
        direct["GetPathDetail"].append({"pathAttributeId": 20, "stopId": 41, "sequenceNo": 5})
        self.assertEqual(reconcile(direct, models)[1][0]["status"], "verified_order_with_explicit_virtual_points_omitted")
        direct["GetStop"][-1]["nameZh"] = "乙站"
        self.assertEqual(reconcile(direct, models)[1][0]["status"], "requires_review_sequence_difference")

    def test_loop_repetition_is_compared_as_an_ordered_sequence(self):
        direct, models = fixture()
        direct["GetPathDetail"].append({"pathAttributeId": 20, "stopId": 40, "sequenceNo": 5})
        self.assertEqual(reconcile(direct, models)[1][0]["status"], "requires_review_sequence_difference")
        models[0]["stops"].append({**models[0]["stops"][0], "stop_sequence": 8})
        self.assertEqual(reconcile(direct, models)[1][0]["status"], "verified_full_stop_id_sequence")

    def test_coordinate_conflicts_are_retained_without_changing_identity(self):
        direct, models = fixture()
        models[0]["stops"][0]["stop_position"]["position_lon"] = 121.501
        member = reconcile(direct, models)[0][0]
        self.assertEqual(member["status"], "verified_explicit_source_identity")
        self.assertIn("source_coordinate_difference", member["source_review_flags"])
        self.assertGreater(member["source_coordinate_separation_m"], 100)
        self.assertEqual(member["direct_coordinates"], [121.5, 25])
        self.assertIsNone(separation_m(None, [121.5, 25]))


if __name__ == "__main__":
    unittest.main()
