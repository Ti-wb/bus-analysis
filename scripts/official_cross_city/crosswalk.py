"""Audit explicit official bus IDs and route-map links; never guess IDs from names."""
from __future__ import annotations

import argparse
import json
import os
import re
import tempfile
import time
from collections import Counter, defaultdict
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from urllib.request import Request, build_opener

from .common import ROOT, SafeRedirect, check_url, csv_rows, now, write_json
from .ntpc import SOURCES, urls
from .ntpc_pathdetail import MAX_ROWS, identifier, read_payload

FEED_PAGE = "https://data.taipei/dataset/detail?id=2a8f5f42-942c-4974-8366-b44e90ad9701"
SPEC_PAGE = "https://pto.gov.taipei/News_Content.aspx?n=A1DF07A86105B6BB&s=C668AE404DCD2EFE"
EBUS_URL = "https://ebus.gov.taipei/EBus2/RouteList2?ct=ntpc"
RAW_DIR = "raw_data/official_cross_city/crosswalk"
SOURCE_DEFINITIONS = {
    **{f"ntpc_{kind}.csv": {"url": urls(kind)[1], "page": urls(kind)[0], "max_mb": 16}
       for kind in SOURCES},
    **{f"ntpc_{kind}_feed.gz": {"url": f"https://tcgbusfs.blob.core.windows.net/ntpcbus/Get{kind.title()}.gz",
                                "page": FEED_PAGE, "max_mb": 8} for kind in ["route", "stop"]},
    "ntpc_pathdetail.gz": {"url": "https://tcgbusfs.blob.core.windows.net/ntpcbus/GetPathDetail.gz",
                           "page": FEED_PAGE, "max_mb": 8},
    "ebus_ntpc_routes.html": {"url": EBUS_URL, "page": EBUS_URL, "max_mb": 4},
}


def source_file(root, name, *, offline):
    """Keep this collector's raw bytes and receipts in its own directory."""
    spec = SOURCE_DEFINITIONS[name]
    hosts = {urlsplit(spec["url"]).hostname}
    directory = root / RAW_DIR
    path, receipt_path = directory / name, directory / (name + ".source.json")
    if offline:
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        if (receipt.get("requested_url") != spec["url"] or receipt.get("page_url") != spec["page"]
                or receipt.get("http_status") != 200 or not receipt.get("downloaded_at")
                or receipt.get("bytes") != path.stat().st_size
                or receipt.get("raw_file") != str(path.relative_to(root))):
            raise ValueError("Cached crosswalk source provenance/size mismatch")
        check_url(receipt["effective_url"], hosts)
        return path, receipt
    directory.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=directory, suffix=".part")
    try:
        opener = build_opener(SafeRedirect(hosts))
        request = Request(spec["url"], headers={"User-Agent": "bus-analysis-official-data/1.0", "Accept-Encoding": "identity"})
        started = time.monotonic()
        with os.fdopen(fd, "wb") as out, opener.open(request, timeout=60) as response:
            limit = spec["max_mb"] * 1024 * 1024
            declared = response.headers.get("Content-Length")
            if response.status != 200 or (declared and int(declared) > limit):
                raise ValueError("Expected complete bounded HTTP 200 source")
            effective = check_url(response.geturl(), hosts)
            count = 0
            while block := response.read(1024 * 1024):
                count += len(block)
                if count > limit or time.monotonic() - started > 180:
                    raise ValueError("Crosswalk source exceeds download budget")
                out.write(block)
            if not count or (declared and count != int(declared)):
                raise ValueError("Empty or truncated crosswalk source")
            receipt = {"page_url": spec["page"], "requested_url": spec["url"], "effective_url": effective,
                       "downloaded_at": now(), "source_data_time": None, "http_status": 200,
                       "http_last_modified": response.headers.get("Last-Modified"),
                       "content_type": response.headers.get("Content-Type"), "bytes": count,
                       "raw_file": str(path.relative_to(root))}
        receipt_path.unlink(missing_ok=True)
        os.replace(temporary, path)
        write_json(receipt_path, receipt)
        return path, receipt
    finally:
        Path(temporary).unlink(missing_ok=True)


def container(dataset_id, records, sources, **extra):
    return {"schema_version": 1, "dataset_id": dataset_id, "metadata": {
        "provider": "新北市政府交通局／大臺北公車／交通部 TDX（各來源分別標示）",
        "generated_at": now(), "source_data_time": None, "sources": sources,
        "grain": "one explicit source identifier relation or one unresolved source record",
        "field_units": {"*_id": "source namespace identifier", "sequence_no": "source sequence"},
        "independent_review_status": "pending", "specification_page": SPEC_PAGE,
        "source_terms": {"ntpc_csv": {"name": "政府資料開放授權條款-第1版", "url": "https://data.gov.tw/license"},
                         "ntpcbus": {"name": "政府資料開放授權條款-第1版", "url": "https://data.taipei/rule", "evidence_page": FEED_PAGE},
                         "ebus_route_list": {"license_status": "no_open_data_license_asserted", "url": "https://ebus.gov.taipei/Home/CopyrightNotice"},
                         "tdx": {"url": "https://tdx.transportdata.tw/term", "note": "依各 TDX 參考版本 metadata 保留使用條款，不沿用 NTPC CSV 授權。"}},
        "limitations": ["只代表所列版本的官方明示識別碼關聯；不推論跨版本或全北北基營運覆蓋。",
                        "路線、附線、站牌、實體站位與 NId 分別保留命名空間，不互相替代。",
                        "名稱僅保留為人工查核線索，不用來產生已核實 ID。",
                        "來源資料時間與下載時間分開；未知關聯不補成沒有服務。"], **extra},
        "records": records}


def nullable(value):
    return None if value is None or str(value).strip() == "" else str(value).strip()


def nid_identifier(value):
    # Official NId values include lettered branches, for example 04540152A0.
    if not isinstance(value, str) or not re.fullmatch(r"[0-9A-Z]{10}", value):
        raise ValueError("Expected an explicit ten-character official NId")
    return value


def feed_rows(path):
    payload = read_payload(path)
    if (not isinstance(payload, dict) or not isinstance(payload.get("EssentialInfo"), dict)
            or not isinstance(payload.get("BusInfo"), list) or not 0 < len(payload["BusInfo"]) <= MAX_ROWS):
        raise ValueError("Invalid bounded official feed container")
    if any(not isinstance(row, dict) for row in payload["BusInfo"]):
        raise ValueError("Invalid feed row")
    return payload["BusInfo"], payload["EssentialInfo"]


def compare_csv_feed(path, kind, feed):
    """Verify both populations using every CSV column, preserving feed ordinals."""
    by_id = defaultdict(list)
    for number, row in enumerate(feed, 1):
        by_id[identifier(row["Id"])].append((number, {k.lower(): nullable(v) for k, v in row.items()}))
    rows, duplicates, seen, matched = [], 0, set(), set()
    for line, row in csv_rows(path, SOURCES[kind]["required"], lower_headers=True):
        normalized = {k: nullable(v) for k, v in row.items()}
        identifier(normalized["id"])
        candidates = [number for number, r in by_id[normalized["id"]]
                      if all(key in r and r[key] == value for key, value in normalized.items())]
        if not candidates:
            raise ValueError(f"CSV/feed content conflict at {kind} line {line}; refresh both sources")
        matched.update(candidates)
        key = tuple(sorted(normalized.items()))
        duplicates += key in seen
        seen.add(key)
        rows.append((line, normalized))
    if len(matched) != len(feed):
        raise ValueError(f"CSV has unmatched feed records in {kind}; refresh both sources")
    return rows, {"csv_source_rows": len(rows), "feed_source_rows": len(feed),
                  "all_csv_columns_verified_rows": len(rows), "exact_duplicate_csv_rows": duplicates,
                  "null_equivalence": "CSV empty string and feed JSON null only"}


class RouteCategories(HTMLParser):
    """Read category panel IDs and their explicit javascript:go(NId) links."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack, self.current_panel, self.current_anchor = [], None, None
        self.headings, self.links = defaultdict(list), defaultdict(list)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "div" and re.fullmatch(r"collapse\d+", attrs.get("id", "")):
            self.current_panel = (attrs["id"], len(self.stack))
        if tag == "a":
            href = attrs.get("href", "")
            match = re.fullmatch(r"javascript:go\('([0-9A-Z]{10})'\)", href)
            self.current_anchor = {"heading": href[1:] if re.fullmatch(r"#collapse\d+", href) else None,
                                   "nid": match.group(1) if match else None, "parts": []}
        if tag not in {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}:
            self.stack.append(tag)

    def handle_data(self, value):
        if self.current_anchor:
            self.current_anchor["parts"].append(value)

    def handle_endtag(self, tag):
        if tag == "a" and self.current_anchor:
            item = self.current_anchor
            label = " ".join("".join(item["parts"]).split())
            if item["heading"]:
                self.headings[item["heading"]].append(label)
            if item["nid"] and self.current_panel:
                self.links[self.current_panel[0]].append((item["nid"], label))
            self.current_anchor = None
        if tag in self.stack:
            index = len(self.stack) - 1 - self.stack[::-1].index(tag)
            if self.current_panel and index <= self.current_panel[1]:
                self.current_panel = None
            del self.stack[index:]


def leapfrog_nids(html):
    parser = RouteCategories()
    parser.feed(html)
    panels = [key for key, names in parser.headings.items()
              if any(name in {"Jump Frog Bus", "跳蛙公車", "跳蛙專車"} for name in names)]
    if len(panels) != 1 or not parser.links[panels[0]]:
        raise ValueError("Missing or ambiguous explicit eBus leapfrog category")
    rows = parser.links[panels[0]]
    if len({r[0] for r in rows}) != len(rows):
        raise ValueError("Duplicate leapfrog NId links")
    return rows


def route_map_key(url):
    """Accept only the official MapOverview NId query, not an arbitrary image URL."""
    if not isinstance(url, str):
        return None
    value = urlsplit(url)
    try:
        query = parse_qs(value.query, strict_parsing=True)
    except ValueError:
        return None
    if (value.scheme != "https" or value.netloc != "ebus.gov.taipei" or value.path != "/MapOverview"
            or value.fragment or set(query) != {"nid"} or len(query["nid"]) != 1
            or not re.fullmatch(r"[0-9A-Z]{10}", query["nid"][0])):
        return None
    return query["nid"][0]


def route_bridge(feed, source):
    groups, seen, parents = {}, set(), {}
    for number, row in enumerate(feed, 1):
        route, branch, operator = (identifier(row[k]) for k in ["Id", "pathAttributeId", "providerId"])
        nid, branch_nid = nid_identifier(row["NId"]), nid_identifier(row["pathAttributeNId"])
        if branch in parents and parents[branch] != route:
            raise ValueError("Subroute has conflicting parent route IDs")
        parents[branch] = route
        if route_map_key(row.get("roadMapUrl")) != nid:
            raise ValueError("GetRoute NId/roadMapUrl conflict")
        key = (route, branch, operator)
        record = {"route_id": route, "subroute_id": branch, "operator_id": operator,
                  "route_nid": nid, "subroute_nid": branch_nid,
                  "ttia_path_id": nullable(row.get("ttiaPathId")), "route_map_url": row["roadMapUrl"],
                  "route_name": nullable(row.get("nameZh")), "subroute_name": nullable(row.get("pathAttributeName")),
                  "source_namespace": "ntpcbus", "reference_namespace": "ntpc_open_data",
                  "linkage_status": "verified_all_csv_columns_and_explicit_feed_ids",
                  "source_record_numbers": [number]}
        if key in groups:
            old = groups[key]
            if {k: v for k, v in old.items() if k != "source_record_numbers"} != {k: v for k, v in record.items() if k != "source_record_numbers"}:
                raise ValueError("Conflicting route crosswalk key")
            old["source_record_numbers"].append(number)
        else:
            groups[key] = record
    for row in groups.values():
        seen.add((row["route_id"], row["route_nid"]))
    ids, nids = defaultdict(set), defaultdict(set)
    for route, nid in seen:
        ids[route].add(nid)
        nids[nid].add(route)
    if any(len(v) != 1 for v in [*ids.values(), *nids.values()]):
        raise ValueError("Main route ID/NId is not one-to-one")
    return list(groups.values())


def stop_audit(stops, paths, source):
    index = {identifier(r["Id"]): r for r in stops}
    if len(index) != len(stops):
        raise ValueError("Duplicate GetStop ID")
    used, seen, cells = set(), set(), set()
    for row in paths:
        item, branch, stop = (identifier(row[k]) for k in ["id", "pathAttributeId", "stopId"])
        sequence = identifier(row["sequenceNo"])
        cell = (branch, row.get("type"), sequence)
        if item in seen or cell in cells or stop not in index:
            raise ValueError("Invalid or duplicate PathDetail identity")
        seen.add(item); cells.add(cell); used.add(stop)
    records = []
    for stop in sorted(set(index) - used, key=int):
        row = index[stop]
        label = nullable(row.get("nameZh"))
        marker = "暫時裁撤" if label and "暫時裁撤" in label else "施工取消停靠" if label and "施工取消停靠" in label else None
        records.append({"stop_id": stop, "route_id": identifier(row["routeId"]),
                        "stop_location_id": identifier(row["stopLocationId"]), "stop_name": label,
                        "sequence_no": int(identifier(row["seqNo"])), "direction_code_raw": nullable(row.get("goBack")),
                        "source_namespace": "ntpc_open_data", "reference_namespace": "ntpcbus",
                        "getstop_presence": "verified_all_csv_columns", "pathdetail_presence": "absent_in_snapshot",
                        "subroute_id": None, "linkage_status": "unresolved_no_explicit_path_membership",
                        "source_label_service_note": marker,
                        "cause_status": "not_verified_source_label_only" if marker else "unknown"})
    return records, {"reference_stop_count": len(index), "linked_stop_count": len(used),
                     "unlinked_stop_count": len(records), "source_label_note_counts": dict(Counter(r["source_label_service_note"] or "none" for r in records))}


def tdx_crosswalk(tdx, direct_routes, namespace):
    by_nid, by_id = defaultdict(list), defaultdict(list)
    for row in direct_routes:
        by_nid[row["route_nid"]].append(row)
        by_id[row["route_id"]].append(row)
    result = []
    tdx_nid_counts = Counter(route_map_key(r.get("route_map_image_url")) for r in tdx)
    for row in tdx:
        nid = route_map_key(row.get("route_map_image_url"))
        candidates = by_nid.get(nid, []) if nid else []
        ids = sorted({r["route_id"] for r in candidates})
        verified = len(ids) == 1 and tdx_nid_counts[nid] == 1
        direct_id = ids[0] if verified else None
        # Even a unique official map link must agree with the explicitly supplied route ID.
        if verified and identifier(row["route_id"]) != direct_id:
            verified = False
            direct_id = None
        result.append({"source_namespace": "tdx", "reference_namespace": namespace,
                       "tdx_route_uid": row.get("route_uid"), "tdx_route_id": row.get("route_id"),
                       "route_nid": nid, "reference_route_id": direct_id,
                       "route_map_url": row.get("route_map_image_url"),
                       "linkage_status": "verified_unique_official_map_nid_and_explicit_route_id" if verified else "unresolved_ambiguous_or_missing_official_map_bridge",
                       "candidate_reference_route_ids": ids})
    return result


def validate_tdx_reference(obj):
    records, meta = obj.get("records"), obj.get("metadata", {})
    if obj.get("schema_version") != 1 or not isinstance(records, list) or not records:
        raise ValueError("Invalid normalized TDX route snapshot")
    city = records[0].get("city")
    if city not in {"Taipei", "NewTaipei"} or any(r.get("city") != city for r in records):
        raise ValueError("TDX crosswalk requires one Taipei/NewTaipei city")
    sources = meta.get("sources")
    if not isinstance(sources, list) or len(sources) != 1:
        raise ValueError("Expected one official TDX source receipt")
    source = sources[0]
    value = urlsplit(source.get("download_url", ""))
    if (value.scheme != "https" or value.netloc != "tdx.transportdata.tw"
            or not re.fullmatch(rf"/api/historical/v2/Historical/Bus/Route/Date/20[0-9]{{2}}-[0-9]{{2}}-[0-9]{{2}}/City/{city}", value.path)
            or source.get("http_status") != 200 or not source.get("downloaded_at")
            or meta.get("record_count") != len(records)
            or not str(meta.get("scope_status", "")).startswith("complete_requested")):
        raise ValueError("TDX source provenance/count/completeness mismatch")
    uids, ids = set(), set()
    for row in records:
        rid, uid = identifier(row.get("route_id")), row.get("route_uid")
        if not isinstance(uid, str) or uid in uids or rid in ids:
            raise ValueError("Missing or duplicate TDX route identity")
        uids.add(uid); ids.add(rid)
    return city, records


def run(root, *, offline, tdx_paths=()):
    sources, files = {}, {}
    for name in SOURCE_DEFINITIONS:
        files[name], sources[name] = source_file(root, name, offline=offline)
    routes, route_info = feed_rows(files["ntpc_route_feed.gz"])
    stops, stop_info = feed_rows(files["ntpc_stop_feed.gz"])
    paths, path_info = feed_rows(files["ntpc_pathdetail.gz"])
    _, route_comparison = compare_csv_feed(files["ntpc_routes.csv"], "routes", routes)
    _, stop_comparison = compare_csv_feed(files["ntpc_stops.csv"], "stops", stops)
    bridge = route_bridge(routes, sources["ntpc_route_feed.gz"])
    branch_parent = {r["subroute_id"]: r["route_id"] for r in bridge}
    stop_parent = {identifier(r["Id"]): identifier(r["routeId"]) for r in stops}
    for row in paths:
        if branch_parent.get(identifier(row["pathAttributeId"])) != stop_parent.get(identifier(row["stopId"])):
            raise ValueError("PathDetail route/subroute/stop parent conflict")
    missing, stop_quality = stop_audit(stops, paths, sources["ntpc_pathdetail.gz"])
    category = leapfrog_nids(files["ebus_ntpc_routes.html"].read_text(encoding="utf-8"))
    frog = []
    for nid, label in category:
        found = [r for r in bridge if r["route_nid"] == nid]
        if not found:
            raise ValueError("Official leapfrog NId missing from GetRoute snapshot")
        frog.append({"route_nid": nid, "route_id": found[0]["route_id"],
                     "subroute_ids": sorted({r["subroute_id"] for r in found}),
                     "operator_ids": sorted({r["operator_id"] for r in found}),
                     "route_name": found[0]["route_name"], "official_category_link_label": label,
                     "source_namespace": "ebus_route_list", "reference_namespace": "ntpcbus",
                     "linkage_status": "verified_explicit_category_nid_and_getroute_ids"})
    csv_audit = []
    for line, row in csv_rows(files["ntpc_leapfrog.csv"], SOURCES["leapfrog"]["required"], lower_headers=True):
        csv_audit.append({"source_sequence": row["seqno"], "agency_codes_raw": row["agency_codes"],
                          "route_name": row["routename"], "source_row_number": line,
                          "route_id": None, "route_nid": None, "subroute_id": None,
                          "linkage_status": "unresolved_source_has_no_explicit_route_identifier",
                          "source_namespace": "ntpc_leapfrog_list"})
    src = list(sources.values())
    outputs = {
        "ntpc_feed_id_crosswalk.json": container("ntpc_feed_id_crosswalk", bridge, src,
            source_data_times_raw={"route": route_info.get("UpdateTime"), "stop": stop_info.get("UpdateTime"), "pathdetail": path_info.get("UpdateTime")},
            csv_feed_comparisons={"routes": route_comparison, "stops": stop_comparison},
            source_essential_info={"route": route_info, "stop": stop_info, "pathdetail": path_info}),
        "ntpc_unlinked_stops_crosswalk.json": container("ntpc_unlinked_stops_crosswalk", missing, src,
            coverage=stop_quality, pathdetail_source_rows=len(paths),
            source_data_times_raw={"stop": stop_info.get("UpdateTime"), "pathdetail": path_info.get("UpdateTime")}),
        "ntpc_leapfrog_crosswalk.json": container("ntpc_leapfrog_crosswalk", frog, src,
            classification_method="Explicit Jump Frog Bus category href NId; GetRoute supplies numeric main/subroute IDs.",
            csv_list_relation_status="not_established_no_explicit_route_id_in_csv"),
        "ntpc_leapfrog_list_crosswalk.json": container("ntpc_leapfrog_list_crosswalk", csv_audit, [sources["ntpc_leapfrog.csv"]],
            verified_record_count=0, unresolved_record_count=len(csv_audit)),
    }
    tdx_records, tdx_sources = [], []
    for path in tdx_paths:
        obj = json.loads(path.read_text(encoding="utf-8"))
        city, records = validate_tdx_reference(obj)
        if city == "NewTaipei":
            references, ns = bridge, "ntpcbus"
        elif city == "Taipei":
            references = []
            branches = json.loads((root / "data/taipei_feeds/route_branches.json").read_text(encoding="utf-8"))
            if (branches.get("dataset_id") != "taipei_route_branches"
                    or branches["metadata"]["source"].get("download_url") != "https://tcgbusfs.blob.core.windows.net/blobbus/GetRoute.gz"):
                raise ValueError("Expected the official Taipei GetRoute reference")
            for branch in branches["records"]:
                references.append({"route_id": branch["route_id"], "route_nid": branch["route_nid"]})
            tdx_sources.append({"normalized_file": "data/taipei_feeds/route_branches.json", **branches["metadata"]["source"]})
            ns = "taipei_blobbus"
        else:
            raise ValueError("TDX crosswalk supports only explicit Taipei/NewTaipei snapshots")
        tdx_records.extend(tdx_crosswalk(records, references, ns))
        tdx_sources.append({"normalized_file": str(path.relative_to(root)), "metadata": obj["metadata"]})
    if tdx_paths:
        outputs["tdx_route_crosswalk.json"] = container("tdx_route_crosswalk", tdx_records, tdx_sources,
            coverage=dict(Counter(r["linkage_status"] for r in tdx_records)))
    directory = root / "data/official_cross_city"
    for name, value in outputs.items():
        write_json(directory / name, value)
    return {name: len(value["records"]) for name, value in outputs.items()}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--tdx-route", type=Path, action="append", default=[], help="Normalized official TDX Taipei/NewTaipei route snapshot; repeatable")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--fetch", action="store_true")
    mode.add_argument("--offline", action="store_true")
    args = parser.parse_args(argv)
    if not (args.fetch or args.offline):
        print(json.dumps({"status": "plan_only_no_download", "sources": SOURCE_DEFINITIONS}, ensure_ascii=False))
        return
    print(json.dumps(run(args.root.resolve(), offline=args.offline,
                         tdx_paths=[p.resolve() for p in args.tdx_route]), ensure_ascii=False))


if __name__ == "__main__":
    main()
