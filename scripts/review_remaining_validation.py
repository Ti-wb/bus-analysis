"""Reconcile current independent evidence without turning unknowns into passes.

Stdlib only. This consolidates the named source reviews; it does not repeat their
GIS/visual source audits, import producer code or request a business API.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime
import gzip
import json
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
SERVICE_DIR = "data/official_cross_city/service_coverage"
REGIONAL_DIR = "data/official_cross_city/regional_service_evidence"
BOUNDARY_DIR = "data/official_cross_city/boundary_evidence"
WALKING_DIR = "data/official_cross_city/walking_transfers"
SCOPES = ("Taipei", "NewTaipei", "Keelung", "InterCity")
SERVICES = ("StopOfRoute", "Schedule")


def read(root, path):
    path = root / path
    with (gzip.open(path, "rt", encoding="utf-8") if path.suffix == ".gz"
          else path.open(encoding="utf-8")) as stream:
        return json.load(stream)


def check(condition, message):
    if not condition:
        raise ValueError(message)


def require_review(doc, entry, path, *, generated_at=None):
    """A prior review of another generation cannot certify a refreshed output."""
    check(entry["record_count"] == len(doc["records"]), path + ": reviewed count differs")
    check(doc["metadata"]["record_count"] == len(doc["records"]), path + ": envelope count differs")
    check(entry["status"].startswith(("pass", "passed")), path + ": source review did not pass")
    if generated_at is not None:
        check(doc["metadata"].get("generated_at") == generated_at, path + ": reviewed generation differs")


def require_schedule_support(rows, schedule_support):
    """Published partial rows are evidence; partial absence remains unknown."""
    for row in rows:
        support = schedule_support[row["source_scope"]]
        check(support in {"not_acquired", "partial_published_endpoint", "complete_published_endpoint"},
              "unknown Schedule support status")
        check(support != "not_acquired" or not row.get("unassigned_operator_schedule_evidence_ids"),
              "unacquired Schedule has invented candidate evidence")
        complete_identity = support == "complete_published_endpoint" and row["operator_id"] is not None
        for field, count in (("schedule_publication_status", len(row["schedule_evidence_ids"])),
                             ("service_day_publication_status", row["service_day_object_count"]),
                             ("special_day_publication_status", row["special_day_object_count"])):
            check(support != "not_acquired" or count == 0, "unacquired Schedule has invented evidence")
            expected = ("published" if count else "indeterminate_missing_schedule_operator"
                        if row.get("unassigned_operator_schedule_evidence_ids") and not row["schedule_evidence_ids"]
                        else "not_observed_in_complete_published_endpoint" if complete_identity
                        else "indeterminate_partial_or_not_requested")
            check(row[field] == expected,
                  f"{row['source_scope']}: {field} conflicts with endpoint/identity/evidence support")


def further_summary(rows):
    unresolved = [r for r in rows if r["status"].startswith("requires_source_review")]
    return {
        "record_count": len(rows),
        "status_counts": dict(Counter(r["status"] for r in rows)),
        "legal_transfers_verified": sum(r["legal_transfer_verified"] is True for r in rows),
        "unresolved_crossing_edge_count": sum(r.get("osm_edge_id") is not None for r in unresolved),
        "unresolved_crossing_location_ids": sorted({r["review_location_id"] for r in unresolved if r.get("osm_edge_id")}),
        "unresolved_external_connector_count": sum(r.get("osm_edge_id") is None for r in unresolved),
    }


def review(root=ROOT):
    review_files = {
        "boundary": "data/quality/boundary_evidence_review.json",
        "service": "data/quality/service_coverage_review.json",
        "regional": "data/quality/regional_service_evidence_review.json",
        "walking": "map-data/quality/walking_evidence_review.json",
        "further": "map-data/quality/walking_further_evidence_review.json",
    }
    reviews = {key: read(root, path) for key, path in review_files.items()}
    for key, doc in reviews.items():
        check(doc["metadata"].get("status", "").startswith(("pass", "passed")), key + ": independent review not passed")

    endpoint_rows = []
    endpoint_docs = {}
    for entry in reviews["service"]["records"]:
        path = entry["path"]
        doc = read(root, path)
        require_review(doc, entry, path)
        md = doc["metadata"]
        check(entry["checks"]["failure_count"] == 0, path + ": review has failures")
        check(md["scope_status"] in {"complete_published_endpoint_by_exhausted_page", "partial_contiguous_pages"}, path + ": unknown endpoint scope")
        check(entry["reviewed_source_ids"] == [r["source_id"] for r in md["sources"]], path + ": reviewed source pages differ")
        check(entry["source_downloaded_at"] == [r["downloaded_at"] for r in md["sources"]], path + ": reviewed HTTP snapshots differ")
        raw_updates = sorted({r["update_time"] for r in doc["records"] if r.get("update_time")})
        check(entry["source_periods"] == raw_updates, path + ": source update times differ")
        endpoint_rows.append({
            "scope": entry["scope"], "service": entry["service"], "file": path,
            "record_count": len(doc["records"]), "publication_status": ("complete_published_endpoint"
                if md["scope_status"] == "complete_published_endpoint_by_exhausted_page" else "partial_published_endpoint"),
            "source_updated_at": md.get("source_updated_at"), "source_record_update_times": raw_updates,
            "route_uid_count": entry["diagnostics"]["route_uid_count"],
            "nested_stop_count": entry["diagnostics"]["nested_stop_count"],
            "timetable_count": entry["diagnostics"]["timetable_count"],
            "frequency_count": entry["diagnostics"]["frequency_count"],
            "service_day_object_count": entry["diagnostics"]["service_day_object_count"],
            "special_day_object_count": entry["diagnostics"]["special_day_object_count"],
            "source_pages": [{k: r.get(k) for k in ("source_id", "source_page", "download_url", "raw_file", "downloaded_at", "source_updated_at")}
                             for r in md["sources"]],
        })
        endpoint_docs[(entry["scope"], entry["service"])] = doc

    regional_docs = {}
    regional_inventory = []
    for entry in reviews["regional"]["records"]:
        path = entry["output_path"]
        doc = read(root, path)
        require_review(doc, entry, path, generated_at=entry["reviewed_generated_at"])
        regional_docs[Path(path).name] = doc
        regional_inventory.append({"file": path, "dataset_id": doc["dataset_id"], "record_count": len(doc["records"]),
                                   "reviewed_generated_at": entry["reviewed_generated_at"]})
    check(set(regional_docs) == {"stop_geography.json.gz", "service_models.json.gz", "schedule_calendars.json.gz",
                                "coverage_summary.json", "support.json"}, "regional five-file inventory differs")
    support = regional_docs["support.json"]["records"]
    wanted = {(scope, service) for scope in SCOPES for service in SERVICES}
    support_index = {(r["source_scope"], r["service"]): r for r in support}
    check(set(support_index) == wanted and len(support) == len(wanted), "four-scope/eight-endpoint support configuration differs")
    wanted_acquired = {k for k, r in support_index.items() if r["publication_support"] != "not_acquired"}
    endpoint_index = {(r["scope"], r["service"]): r for r in endpoint_rows}
    check(set(endpoint_index) == wanted_acquired and len(endpoint_rows) == len(wanted_acquired),
          "reviewed endpoint inventory differs from regional support; regenerate source/regional reviews")
    regional_inputs = {r["path"]: r for r in regional_docs["support.json"]["metadata"]["source_inputs"]}
    for k, row in endpoint_index.items():
        supported = support_index[k]
        check((supported["publication_support"], supported["record_count"], supported["input_file"])
              == (row["publication_status"], row["record_count"], row["file"]), "support and reviewed endpoint disagree")
        source_input = regional_inputs[row["file"]]
        check(source_input["source_ids"] == [r["source_id"] for r in row["source_pages"]]
              and source_input["downloaded_at"] == [r["downloaded_at"] for r in row["source_pages"]],
              "regional source lineage differs from independently reviewed endpoint")
    schedule_support = {scope: support_index[(scope, "Schedule")]["publication_support"] for scope in SCOPES}
    incomplete_schedule_scopes = [scope for scope in SCOPES if schedule_support[scope] != "complete_published_endpoint"]
    models = regional_docs["service_models.json.gz"]["records"]
    require_schedule_support(models, schedule_support)
    geography = regional_docs["stop_geography.json.gz"]["records"]
    regional_md = reviews["regional"]["metadata"]
    check(len(models) == regional_md["totals"]["operator_relation_models"], "operator-relation total differs")
    check(len(geography) == regional_md["totals"]["unique_stop_geography_variants"], "stop variant total differs")
    check(dict(Counter(r["schedule_publication_status"] for r in models)) == regional_md["schedule_publication_status_counts"],
          "published/unobserved/unknown model totals differ")
    missing_models = [{k: r[k] for k in ("source_scope", "route_uid", "subroute_uid", "direction", "operator_id")}
                      for r in models if r["schedule_publication_status"] == "not_observed_in_complete_published_endpoint"]
    missing_scope_counts = Counter(r["source_scope"] for r in missing_models)
    unknown_model_count = sum(r["schedule_publication_status"].startswith("indeterminate_") for r in models)
    missing_operator_model_count = sum(r["schedule_publication_status"] == "indeterminate_missing_schedule_operator" for r in models)
    route_uid_gaps = []
    for scope in SCOPES:
        stop_uids = {r["route_uid"] for r in endpoint_docs[(scope, "StopOfRoute")]["records"]}
        schedule_doc = endpoint_docs.get((scope, "Schedule"))
        seen_uids = {r["route_uid"] for r in schedule_doc["records"]} if schedule_doc else set()
        route_uid_gaps.append({"source_scope": scope, "schedule_publication_support": schedule_support[scope],
            "stopofroute_route_uid_count": len(stop_uids), "published_schedule_route_uid_count": len(seen_uids) if schedule_doc else None,
            "complete_endpoint_route_uids_not_observed": sorted(stop_uids - seen_uids) if schedule_support[scope] == "complete_published_endpoint" else None})
    support_counts = Counter(r["publication_support"] for r in support)
    endpoint_scope_text = (f"{len(endpoint_rows)}個已取得端點（{support_counts['complete_published_endpoint']}完整、{support_counts['partial_published_endpoint']}部分）")
    operational_missing = []
    if incomplete_schedule_scopes:
        operational_missing.append({"scope": "/".join(incomplete_schedule_scopes) + " Schedule", "fields": ["尚未完整取得的班表端點"],
            "interpretation": f"依support逐端點區分未取得與partial；已發布的列仍可核實。{unknown_model_count}個業者關係模型為unknown，不是無班表或停駛。"})
    if missing_operator_model_count:
        operational_missing.append({"scope": "三碼班表候選缺少OperatorID", "fields": ["來源明示業者或可核實的五維歸屬"],
            "interpretation": f"{missing_operator_model_count}個站序業者關係有相同RouteUID/SubRouteUID/Direction的班表，但來源未發布業者，保持indeterminate_missing_schedule_operator。候選日曆另列，不套用或按多業者複製為已核班表。"})
    elif unknown_model_count and not incomplete_schedule_scopes:
        operational_missing.append({"scope": "未完成五維身分的站序模型", "fields": ["來源缺少的業者身分或其他連接條件"],
            "interpretation": f"端點完整仍有{unknown_model_count}個關係無法判定，保持unknown，不強制當作缺班表。"})
    if missing_models:
        breakdown = "、".join(f"{scope} {missing_scope_counts[scope]}個" for scope in SCOPES if missing_scope_counts[scope])
        operational_missing.append({"scope": "完整發布端點之未觀察五維模型", "fields": ["來源缺列原因及跨版本有效性"],
            "interpretation": f"{len(missing_models)}個站序業者關係未觀察相同五維班表（{breakdown}）。這是發布關聯缺列，不是{len(missing_models)}線停駛；整條RouteUID缺列另列清冊。"})
    operational_missing.append({"scope": "四取用範圍實際營運", "fields": ["指定驗收日期的實際行駛事件、臨時停駛/改道/當期例外的權威確認"],
        "interpretation": "發布站序、班表與日曆不能保證每班實際行駛；全國主檔數不是北北基路線數。"})

    transfers = read(root, WALKING_DIR + "/transfers.json")
    graphs = read(root, WALKING_DIR + "/graphs.json.gz")
    manifest = read(root, "map-data/walking/source_manifest.json")
    walking_md = reviews["walking"]["metadata"]
    for name, doc in (("transfers", transfers), ("graphs", graphs), ("source_manifest", manifest)):
        check(doc["metadata"]["generated_at"] == walking_md["reviewed_generated_at"][name], name + ": walking review is stale")
        check(doc["metadata"]["record_count"] == len(doc["records"]), name + ": walking envelope count differs")
    check(len(transfers["records"]) == walking_md["case_count"], "walking case count differs")
    check(sum(r["status"] != "unverified" for r in transfers["records"]) == walking_md["accepted_transfer_count"] == 0,
          "accepted transfer population changed; requires semantic review")
    check(all(r["walking_distance_m"] is None and not r["path_edge_ids"] for r in transfers["records"]),
          "an unverified case exposes a usable path or walking cost")
    graph_counts = [{"area_id": r["area_id"], "nodes": len(r["nodes"]), "strict_edges": len(r["edges"]),
                     "candidate_edges": len(r["candidate_edges"]), "source_data_time": r["source_data_time"]} for r in graphs["records"]]
    check(len(graph_counts) == len(walking_md["graph_review"]), "walking graph population differs")
    for actual, reviewed in zip(graph_counts, walking_md["graph_review"]):
        check(actual["area_id"] == reviewed["area_id"] and actual["nodes"] == reviewed["raw_nodes"]
              and all(actual[k] == reviewed[k] for k in ("strict_edges", "candidate_edges")), "walking graph counts differ")
    further = read(root, "map-data/walking/further_evidence.json")
    further_review = reviews["further"]["records"][0]
    require_review(further, further_review, further_review["path"])
    check(datetime.fromisoformat(reviews["further"]["metadata"]["reviewed_at"]) >= datetime.fromisoformat(further["metadata"]["generated_at"]),
          "further evidence was generated after its independent review")
    fs = further_summary(further["records"])
    check(fs["status_counts"] == further_review["status_counts"] and fs["legal_transfers_verified"] == further_review["accepted_new_transfers"] == 0,
          "further evidence acceptance/status count differs")
    check(len(further["metadata"]["source_files"]) == further_review["source_files_checked"], "further source inventory differs")

    aliases = read(root, BOUNDARY_DIR + "/name_aliases.json")
    versions = read(root, BOUNDARY_DIR + "/code_version_checks.json")
    changes = read(root, BOUNDARY_DIR + "/boundary_changes.json")
    distribution = read(root, BOUNDARY_DIR + "/current_distribution_check.json")
    population = read(root, "data/official_cross_city/population_boundary_crosswalk.json")
    boundary_review = {r["dataset"]: r for r in reviews["boundary"]["records"]}
    check(len(aliases["records"]) == boundary_review["name_aliases"]["record_count"] == 6, "six official alias evidence records differ")
    check(all(r["status"] == "verified_official_code_scoped_name_alias" for r in aliases["records"]), "a name alias remains unverified")
    check(len(population["records"]) == boundary_review["code_version_checks"]["population_rows"], "population identity count differs")
    check(all(r["population_all_codes_and_names_match_september_ris"] and not r["changes"] for r in versions["records"]),
          "regional code/name version evidence changed")
    check(len(changes["records"]) == boundary_review["boundary_changes"]["record_count"], "official geometry change count differs")
    check(all(r["current_distribution_matches_baseline"] for r in distribution["records"]), "current published geometry differs")
    check(all(r["same_month_geometry_verified"] is False for r in distribution["records"]),
          "same-month geometry conclusion changed; obtain independent temporal review")

    records = [{
        "validation_item_id": "population_and_village_boundaries",
        "validation_status": "official_identity_and_published_vintage_verified_temporal_geometry_unproven",
        "may_mark_pr_validation_complete": False,
        "required_inputs": ["六筆名稱的正式對照", "人口月份身分清冊", "人口月份與圖層版本之完整幾何沿革依據"],
        "available_fields": {
            "population_period": population["metadata"]["population_period"],
            "boundary_source_period": population["metadata"]["boundary_source_period"],
            "population_rows": len(population["records"]), "official_code_scoped_alias_count": len(aliases["records"]),
            "boundary_only_unassigned_feature_count": population["metadata"]["boundary_only_count"],
            "unchanged_regional_code_name_versions": [{k: r[k] for k in ("county_code", "county_name", "june_version_count", "september_version_count")} for r in versions["records"]],
            "official_boundary_change_records": changes["records"],
            "current_published_geometry_comparison": distribution["records"],
        },
        "missing_fields": [{"scope": "2026-08-17圖層至2026-09人口月份", "fields": ["完整幾何版本沿革或同月權威幾何"],
                            "interpretation": "當前下載版相同與戶政碼表未變不能證明期間沒有法定或幾何異動；News API未宣告完整歷史涵蓋。"}],
        "evidence": {"independent_review": review_files["boundary"],
                     "boundary_evidence_files": [BOUNDARY_DIR + "/" + name + ".json" for name in
                                                 ("name_aliases", "code_version_checks", "boundary_changes", "current_distribution_check", "manifest")],
                     "population_crosswalk": "data/official_cross_city/population_boundary_crosswalk.json"},
        "conclusion": "1652里身分與6筆造字/異體字已正式核實；可同時標明2026-09人口與2026-08-17圖層使用。原驗收要求的完整跨版本幾何等價仍未證明，不能勾選。",
    }, {
        "validation_item_id": "legal_walking_transfer", "validation_status": "bounded_graphs_and_candidates_verified_no_legal_transfer_accepted",
        "may_mark_pr_validation_complete": False,
        "required_inputs": ["具體路徑的行人通行權與當期限制", "出口及正確可上車站牌的已核實連接", "過街/樓層/障礙物的連續拓樸及端到端路徑驗收"],
        "available_fields": {
            "scope": transfers["metadata"]["scope"], "case_count": len(transfers["records"]),
            "accepted_transfer_count": walking_md["accepted_transfer_count"], "graph_counts": graph_counts,
            "topology": graphs["metadata"]["topology"],
            "official_facility_source_counts": walking_md["source_record_counts"],
            "selected_official_facility_features": walking_md["facility_review"]["selected_feature_count"],
            "candidate_review": walking_md["candidate_review"], "further_evidence_summary": fs,
            "further_source_files_checked": further_review["source_files_checked"],
            "signal_source_rows": further_review["source_signals"], "signals_with_coordinates": further_review["signals_with_coordinates"],
            "further_findings": [{k: r[k] for k in ("evidence_id", "transfer_id", "status", "finding", "legal_transfer_verified")}
                                | {k: r[k] for k in ("temporal_limit", "historical_notice_limit", "used_partial_length_m", "used_partial_road_checks") if k in r}
                                for r in further["records"]],
        },
        "missing_fields": [
            {"scope": "士林出口1至796候選", "fields": ["中正路187巷與中山北路車道兩處當期穿越證據"],
             "interpretation": "四條保守待核邊仍存在；第5邊整邊末端交會在實用81.020358m片段之外，該釐清不解除前段兩處待核。"},
            {"scope": "關渡出口1與70056等18案例", "fields": ["站外可地理核實步行連接與具體可接受端到端路徑"],
             "interpretation": "出口前地址與官方站圖只核實位置描述；不能據此造直線或步行時間。18案例皆未接受。"},
            {"scope": "當期通行", "fields": ["當期封閉/施工/障礙物/過街許可證據"],
             "interpretation": "號誌近鄰不是行穿證據；2026-04至07及2021至2022公告不可當作2026-10開放或封閉證明；不以OSM缺圖證明無其他設施。"},
        ],
        "evidence": {"independent_reviews": [review_files["walking"], review_files["further"]],
                     "transfers": WALKING_DIR + "/transfers.json", "graphs": WALKING_DIR + "/graphs.json.gz",
                     "source_manifest": "map-data/walking/source_manifest.json", "further": "map-data/walking/further_evidence.json"},
        "conclusion": "已取得並核對三區原始節點/相鄰邊、官方設施與18案例，以及6筆後續補證。這些資料可重算候選，但0條合法轉乘被接受，原驗收仍不可勾選。",
    }, {
        "validation_item_id": "keelung_and_intercity_operational_coverage",
        "validation_status": "published_endpoints_and_regional_membership_verified_actual_operations_not_observed",
        "may_mark_pr_validation_complete": False,
        "required_inputs": ["指定來源/日期/範圍之完整站序與上下客限制", "逐路線/附線/方向/業者班表、ServiceDay與SpecialDays", "驗收日期實際營運事件或可核實運行證據"],
        "available_fields": {
            "published_endpoint_count": len(endpoint_rows), "published_endpoints": endpoint_rows,
            "configured_endpoint_count": len(support), "endpoint_support_status_counts": dict(support_counts),
            "regional_outputs": regional_inventory, "regional_source_support": support,
            "complete_endpoint_route_uid_gaps": route_uid_gaps,
            "regional_totals": regional_md["totals"], "stop_geography_status_counts": regional_md["geography_status_counts"],
            "schedule_publication_status_counts": regional_md["schedule_publication_status_counts"],
            "source_scope_city_coverage": regional_docs["coverage_summary.json"]["records"],
            "boundary_membership": regional_md["boundary_review"],
            "join_key": regional_docs["service_models.json.gz"]["metadata"]["join_key"],
            "complete_endpoint_unobserved_schedule_models": missing_models,
        },
        "missing_fields": operational_missing,
        "evidence": {"independent_reviews": [review_files["service"], review_files["regional"]],
                     "route_master_review": "data/quality/regional_validation_review.json", "source_inventory": REGIONAL_DIR + "/support.json"},
        "conclusion": f"{endpoint_scope_text}及五份區域證據已獨立巡檢；以明示ID/原座標及已核版本里界covers判定站點會員，保留全國模型。可按support完成指定已取得發布資料的欄位/涵蓋核對，不能完成原文實際完整營運驗收。",
    }]
    return {
        "schema_version": "1.0", "dataset_id": "remaining_validation_review",
        "metadata": {
            "reviewed_at": datetime.now(ZoneInfo("Asia/Taipei")).isoformat(timespec="seconds"),
            "reviewer": "/root/main_integration", "source_updated_at": None, "record_count": len(records),
            "overall_status": "unresolved_validation_items", "evidence_reconciliation_status": "passed",
            "business_api_requests_performed": 0, "script": "scripts/review_remaining_validation.py",
            "scope": "只核對下列獨立來源報告與目前產物的筆數/版本/來源頁及已知未知邊界；不宣稱全球來源皆已查盡。",
            "method": "Stdlib consolidation with reviewed generation/source receipt binding, endpoint inventories, operator-relation status recounts and walking case/graph/further-evidence reconciliation; source-level GIS/visual reviews remain separately attributed.",
            "independent_source_reviews": [{"file": path, "reviewed_at": reviews[key]["metadata"]["reviewed_at"],
                                             "reviewer": reviews[key]["metadata"].get("reviewer_agent", reviews[key]["metadata"].get("reviewer")),
                                             "status": reviews[key]["metadata"]["status"]} for key, path in review_files.items()],
            "original_pr_validation_items": [
                {"validation_id": "population_and_village_boundaries", "original_item": "人口與里界完整版本對照。",
                 "may_mark_complete": False, "remaining_record_ids": ["population_and_village_boundaries"]},
                {"validation_id": "combined_ids_services_travel_walking", "original_item": "跳蛙及其他跨來源 ID、114 站牌、基隆／公路客運全量涵蓋、站間旅時與合法步行轉乘。",
                 "may_mark_complete": False, "remaining_record_ids": ["legal_walking_transfer", "keelung_and_intercity_operational_coverage"],
                 "separately_reviewed_partial_components": ["data/quality/id_evidence_review.json", "data/quality/taipei_id_evidence_review.json", "data/quality/tdx_bus_travel_time_review.json"],
                 "interpretation": "ID與旅時各有獨立已交付範圍；本報告以尚未通過的步行/實際營運即足以判定合併原項不可勾選，不重複取代其他項的完整巡檢。"},
            ],
            "limitations": ["未知、完整端點未見與未取用分開；均不得推論無服務或永久撤站。", "來源巡檢通過與原驗收完成不同；本報告沒有新增合法步行邊、實際營運事件或同月幾何等價證據。", "本script不重新執行來源GIS與圖面目視驗證；重跑來源reviewer後再重跑本script，保留審查者歸屬。"],
        }, "records": records,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--output", type=Path, default=Path("data/quality/remaining_validation_review.json"))
    args = parser.parse_args()
    report = review(args.root)
    target = args.output if args.output.is_absolute() else args.root / args.output
    target.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps(report, ensure_ascii=False, allow_nan=False, indent=2) + "\n"
    temporary = target.with_name(target.name + ".part")
    temporary.write_text(body, encoding="utf-8")
    temporary.replace(target)
    print(json.dumps({"file": str(target), "evidence_reconciliation_status": report["metadata"]["evidence_reconciliation_status"],
                      "original_items_may_mark_complete": [r["may_mark_complete"] for r in report["metadata"]["original_pr_validation_items"]]}))


if __name__ == "__main__":
    main()
