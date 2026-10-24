"""Official, code-scoped village-name evidence and boundary release acceptance.

Run with --offline after fetching the listed official sources. This module never
rewrites population names, boundary geometries, or another collector's outputs.
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import re
import unicodedata
import zipfile
from pathlib import Path

from .common import ROOT, now, write_json

RAW = Path("raw_data/official_cross_city/boundary_evidence")
OUT = Path("data/official_cross_city/boundary_evidence")
REGIONS = {"63000": "臺北市", "65000": "新北市", "10017": "基隆市"}
REPORT_PAGE = "https://bmap-nlsc.moi.gov.tw/downloadFile.aspx?INSID=20230131172149832"
REPORT_URL = "https://bmap-nlsc.moi.gov.tw/_upload/file/20230131172149832/480090bd6ab3428d9b38809a2bfad587.pdf"
NLSC_PAGE = "https://www.nlsc.gov.tw/NLSC_Content.aspx?n=11987&sms=9680&s=340491"
CHANGE_URL = "https://ws.moi.gov.tw/Download.ashx?u=LzAwMS9VcGxvYWQvNDA3L3JlbGZpbGUvOTY4MC8zNDA0OTEvZDQyNGI4ZGMtMDY0Ni00MDQ1LTlkZGMtNzc0ZDVjZTFhNzc0LnBkZg%3d%3d&n=MTE15bm0OOaciO%2boiOaUv%2bWNgOeVjOe3mueVsOWLlea4heWWri5wZGY%3d"
VILLAGE_URL = "https://www.tgos.tw/tgos/VirtualDir/Product/a04697c8-64db-450a-a105-3eb471c45abd/%E6%9D%91(%E9%87%8C)%E7%95%8C(TWD97%E7%B6%93%E7%B7%AF%E5%BA%A6).zip"
NEWS_URL = "https://whgis-nlsc.moi.gov.tw/nlscapi.ashx/MapOtherController/ListMapNews?Lan=MAP"
NTPC_PAGE = "https://data.ntpc.gov.tw/datasets/8bbd1aca-752c-4df0-b515-1cfd88b36274"
NTPC_URL = "https://data.ntpc.gov.tw/api/datasets/8bbd1aca-752c-4df0-b515-1cfd88b36274/csv?page=0&size=2000"

# Literal pairs are authorized only for these administrative identities. The
# report's PDF page 94 (printed 83), table 3-1, enumerates all six exact codes;
# page 96 (printed 85), table 3-2, confirms bracketed replacement characters.
ALIASES = {
    "65000030017": ("中和區", "瓦\U000fffa8里", "瓦[磘]里", "瓦磘里", "private_use_character"),
    "65000030054": ("中和區", "灰\U000fffa8里", "灰[磘]里", "灰磘里", "private_use_character"),
    "65000070007": ("樹林區", "\U000fffb5寮里", "[獇]寮里", "獇寮里", "private_use_character"),
    "65000120027": ("瑞芳區", "濓新里", "濂新里", "濓新里", "standard_toponym_variant"),
    "65000120028": ("瑞芳區", "濓洞里", "濂洞里", "濓洞里", "standard_toponym_variant"),
    "65000200004": ("坪林區", "石𥕢里", "石[曹]里", "石𥕢里", "rare_character_replacement"),
}


def envelope(dataset_id, records, **metadata):
    return {"schema_version": 1, "dataset_id": dataset_id,
            "metadata": {"record_count": len(records), "source_updated_at": None, **metadata}, "records": records}


def parse_ris(text):
    """Parse the official three-column UTF-8 village table without recoding IDs."""
    result = {}
    for line, row in enumerate(csv.reader(io.StringIO(text.lstrip("\ufeff"))), 1):
        if len(row) != 3:
            raise ValueError(f"RIS row {line}: expected three columns")
        code, parent, name = row
        if not re.fullmatch(r"\d{11}", code) or not re.fullmatch(r"\d{8}", parent) or not name:
            raise ValueError(f"RIS row {line}: malformed identity")
        if code[:8] != parent or code in result:
            raise ValueError(f"RIS row {line}: duplicate or conflicting hierarchy")
        result[code] = {"village_code": code, "district_code": parent,
                        "village_name": name, "source_row_number": line}
    if not result:
        raise ValueError("RIS table is empty")
    return result


def compare_code_versions(before, after):
    records = []
    for code in sorted(before.keys() | after.keys()):
        a, b = before.get(code), after.get(code)
        if a is None or b is None or (a["district_code"], a["village_name"]) != (b["district_code"], b["village_name"]):
            records.append({"village_code": code, "county_code": code[:5],
                            "change_kind": "added" if a is None else "deleted" if b is None else "name_or_parent_changed",
                            "before": a, "after": b})
    return records


def verify_alias(code, population_name, boundary_name, district_name):
    """Fail closed: no global Unicode replacements or name-only joins."""
    expected = ALIASES.get(code)
    if expected is None or (district_name, population_name, boundary_name) != expected[:3]:
        return None
    return {"status": "verified_official_code_scoped_name_alias", "display_name": expected[3],
            "difference_kind": expected[4], "same_month_geometry_verified": False}


def read_boundary_archive(path):
    import shapefile
    from shapely.geometry import shape
    with zipfile.ZipFile(path) as archive:
        names = [n[:-4] for n in archive.namelist() if re.fullmatch(r"VILLAGE_NLSC_\d{7}\.shp", n)]
        if len(names) != 1:
            raise ValueError("Expected one dated NLSC village layer")
        stem = names[0]
        reader = shapefile.Reader(**{ext: io.BytesIO(archive.read(stem + "." + ext))
                                     for ext in ("shp", "shx", "dbf")}, encoding="utf-8")
        features = {}
        for row in reader.iterShapeRecords():
            props = row.record.as_dict()
            if props["COUNTYCODE"] not in REGIONS:
                continue
            code = props["VILLCODE"]
            if code in features:
                raise ValueError("Duplicate NLSC boundary ID")
            features[code] = (props, shape(row.shape.__geo_interface__))
        prj = archive.read(stem + ".prj").decode("utf-8-sig")
    return stem, features, prj


def compare_boundary_versions(before, after):
    rows = []
    for county_code, county_name in REGIONS.items():
        a = {k: v for k, v in before.items() if v[0]["COUNTYCODE"] == county_code}
        b = {k: v for k, v in after.items() if v[0]["COUNTYCODE"] == county_code}
        common = a.keys() & b.keys()
        geometry_changed = sorted(k for k in common if not a[k][1].equals(b[k][1]))
        coordinates_changed = sorted(k for k in common if not a[k][1].equals_exact(b[k][1], tolerance=0))
        attributes_changed = sorted(k for k in common if a[k][0] != b[k][0])
        rows.append({"county_code": county_code, "county_name": county_name,
                     "baseline_feature_count": len(a), "current_feature_count": len(b),
                     "added_codes": sorted(b.keys() - a.keys()), "removed_codes": sorted(a.keys() - b.keys()),
                     "geometry_changed_codes": geometry_changed, "coordinate_changed_codes": coordinates_changed,
                     "attribute_changed_codes": attributes_changed,
                     "current_distribution_matches_baseline": not (a.keys() != b.keys() or geometry_changed or coordinates_changed or attributes_changed),
                     "same_month_geometry_verified": False})
    return rows


def source_receipt(root, name, *, map_source=False):
    path = root / (Path("map-data/raw/boundary_evidence") if map_source else RAW) / name
    receipt = json.loads(path.with_name(path.name + ".source.json").read_text())
    if receipt["bytes"] != path.stat().st_size or receipt["http_status"] != 200 or not receipt.get("downloaded_at"):
        raise ValueError(f"Missing or inconsistent source receipt: {name}")
    return path, receipt


def build(root=ROOT):
    import pdfplumber
    from bs4 import BeautifulSoup
    raw = root / RAW
    sources = []
    names = ["ris_current.html", "ris_history.html", "11506VillageUTF8.txt", "11509VillageUTF8.txt",
             "11509_Village_d.txt", "nlsc_august.html", "nlsc_august_changes.pdf",
             "nlsc_111_boundary_maintenance.pdf", "nlsc_map_news.json", "ntpc_boundaries_page.html",
             "ntpc_boundaries.csv", "zhonghe_villages.pdf", "shulin_village.html",
             "ruifang_villages.html", "pinglin_villages.html"]
    for name in names:
        _, receipt = source_receipt(root, name)
        period = {"11506VillageUTF8.txt": "2026-06", "11509VillageUTF8.txt": "2026-09",
                  "11509_Village_d.txt": "2026-09", "nlsc_111_boundary_maintenance.pdf": "2022",
                  "nlsc_august_changes.pdf": "2026-08-17"}.get(name)
        sources.append({"source_id": Path(name).stem, **receipt, "source_period": period})
    current_path, receipt = source_receipt(root, "current_villages.zip", map_source=True)
    sources.append({"source_id": "current_nlsc_villages", **receipt})
    old = parse_ris((raw / "11506VillageUTF8.txt").read_text(encoding="utf-8-sig"))
    new = parse_ris((raw / "11509VillageUTF8.txt").read_text(encoding="utf-8-sig"))
    changes = compare_code_versions(old, new)
    deleted_text = (raw / "11509_Village_d.txt").read_text(encoding="utf-8-sig")
    deleted_codes = set(re.findall(r"(?m)^(\d{11})\s", deleted_text))
    if deleted_codes != {r["village_code"] for r in changes if r["change_kind"] == "deleted"}:
        raise ValueError("September deletion notice and version diff disagree")
    crosswalk = json.loads((root / "data/official_cross_city/population_boundary_crosswalk.json").read_text())
    population_rows = crosswalk["records"]
    for row in population_rows:
        code = row["population_area_code"]
        if code not in new or new[code]["village_name"] != row["population_village_name"]:
            raise ValueError("Population and September RIS identity/name do not match")
    with pdfplumber.open(raw / "nlsc_111_boundary_maintenance.pdf") as pdf:
        report_text = {n: pdf.pages[n-1].extract_text() or "" for n in (93, 94, 95, 96)}
    aliases = []
    for row in population_rows:
        code = row["population_area_code"]
        if code not in ALIASES:
            continue
        if row["boundary_village_code"] != code:
            raise ValueError("An alias cannot connect different population/boundary IDs")
        district = row["population_area_name"].removeprefix("新北市")
        checked = verify_alias(code, row["population_village_name"], row["boundary_village_name"], district)
        if not checked or code not in old or old[code]["village_name"] != new[code]["village_name"]:
            raise ValueError("Unrecognized or changed official alias")
        # The report table's font extraction maps some distinct PUA glyphs to
        # one glyph. Verify the exact code and classified pair, not that glyph.
        lines = [line for line in report_text[94].splitlines() if line.startswith(code + " ")]
        if len(lines) != 1 or lines[0].count(code) != 2:
            raise ValueError("Official report table 3-1 lacks an unambiguous code pair")
        normalized = unicodedata.normalize("NFKC", lines[0])
        expected_report = row["boundary_village_name"].replace("[", "").replace("]", "")
        if ALIASES[code][4] != "rare_character_replacement" and expected_report not in normalized:
            raise ValueError("Official report name differs from the approved pair")
        if "[" in row["boundary_village_name"] and not any(code in line and row["boundary_village_name"] in line for line in report_text[96].splitlines()):
            raise ValueError("Official report table 3-2 lacks the literal replacement")
        aliases.append({"village_code": code, "district_code": new[code]["district_code"],
                        "county_name": "新北市", "district_name": district,
                        "population_name": row["population_village_name"], "boundary_name": row["boundary_village_name"],
                        "ris_september_name": new[code]["village_name"],
                        "ris_september_source_row_number": new[code]["source_row_number"],
                        "population_period": crosswalk["metadata"]["population_period"],
                        "boundary_version": crosswalk["metadata"]["boundary_source_period"],
                        **checked, "official_evidence": [{"source_page": REPORT_PAGE, "download_url": REPORT_URL,
                        "document_date": "2022-12-23", "pdf_page": 94, "printed_page": 83, "table": "3-1",
                        "code_scoped_pair": True}, *([{"source_page": REPORT_PAGE, "download_url": REPORT_URL,
                        "document_date": "2022-12-23", "pdf_page": 96, "printed_page": 85, "table": "3-2"}]
                        if "[" in row["boundary_village_name"] else [])]})
    if set(r["village_code"] for r in aliases) != set(ALIASES):
        raise ValueError("The six official alias identities were not all accounted for")
    write_json(root / OUT / "name_aliases.json", envelope("official_village_name_aliases", aliases,
               scope="Only the six exact code/name/district tuples; preserve both original strings",
               status="verified_official_code_scoped_name_aliases", source_document_date="2022-12-23",
               limitations=["此表確認行政身分與來源用字對照，不以字串替換擴張到其他代碼。", "正式名稱對照不證明同月份幾何等價。"],
               independent_review_status="pending"))
    region_checks = []
    for prefix, name in REGIONS.items():
        a = {k: v for k, v in old.items() if k.startswith(prefix)}
        b = {k: v for k, v in new.items() if k.startswith(prefix)}
        local_changes = [r for r in changes if r["county_code"] == prefix]
        pop_count = sum(r["county_code"] == prefix for r in population_rows)
        region_checks.append({"county_code": prefix, "county_name": name,
                              "june_version_count": len(a), "september_version_count": len(b),
                              "population_count": pop_count, "changes": local_changes,
                              "population_all_codes_and_names_match_september_ris": pop_count == len(b),
                              "status": "code_names_unchanged" if not local_changes else "code_name_changes_present",
                              "same_month_geometry_verified": False})
    write_json(root / OUT / "code_version_checks.json", envelope("ris_village_code_version_checks", region_checks,
               before_version="2026-06", after_version="2026-09", national_before_count=len(old), national_after_count=len(new),
               national_changes=changes, september_deletion_notice_count=len(deleted_codes),
               source_page="https://www.ris.gov.tw/documents/html/5/1/167.html",
               limitations=["月版代碼檔不是每日幾何版本；代碼與名稱穩定不代表里界從未釐整。"], independent_review_status="pending"))
    with pdfplumber.open(raw / "nlsc_august_changes.pdf") as pdf:
        change_text = pdf.pages[0].extract_text() or ""
    geometry_changes = []
    for code, village in (("65000100016", "坪頂里"), ("65000100019", "民生里")):
        if not any(code in line and village in line and "2026/08/17" in line and "村里界釐整" in line for line in change_text.splitlines()):
            raise ValueError("NLSC August correction notice does not match")
        geometry_changes.append({"village_code": code, "county_code": "65000", "county_name": "新北市",
                                 "district_code": "65000100", "district_name": "淡水區", "village_name": village,
                                 "change_kind": "village_boundary_rectification", "source_reason": "村里界釐整",
                                 "correction_date": "2026-08-17", "publication_date": "2026-08-26",
                                 "source_page": NLSC_PAGE, "download_url": CHANGE_URL, "pdf_page": 1,
                                 "extraction_status": "table_fields_visually_verified"})
    write_json(root / OUT / "boundary_changes.json", envelope("nlsc_august_2026_boundary_corrections", geometry_changes,
               coverage="Official 2026 August release correction list only", source_period="2026-08-17",
               independent_review_status="pending"))
    baseline_path = root / "map-data/raw/validation/villages.zip"
    baseline_stem, baseline, baseline_prj = read_boundary_archive(baseline_path)
    current_stem, current, current_prj = read_boundary_archive(current_path)
    if baseline_prj != current_prj:
        raise ValueError("Boundary CRS changed; explicit transformation review is required")
    geometry_checks = compare_boundary_versions(baseline, current)
    news = json.loads((raw / "nlsc_map_news.json").read_text())
    news_rows = []
    import html
    for item in news:
        content = BeautifulSoup(html.unescape(item["NewsContent"]), "html.parser").get_text(" ", strip=True)
        date = item["NewsDate"].replace("/", "-")
        if date >= "2026-08-17":
            village_boundary = any(term in unicodedata.normalize("NFKC", content)
                                   for term in ("村里界", "村(里)界", "界線釐清", "界線釐整"))
            news_rows.append({"news_id": str(item["NewsID"]), "publication_date": date,
                              "mentions_village_boundaries": village_boundary,
                              "topic": "village_boundary_correction" if village_boundary else "land_use" if "國土利用" in content else "three_dimensional_road_model" if "三維道路模型" in content else "other"})
    write_json(root / OUT / "current_distribution_check.json", envelope("nlsc_current_boundary_distribution_acceptance", geometry_checks,
               baseline_archive=str(baseline_path.relative_to(root)), baseline_layer=baseline_stem,
               current_layer=current_stem, current_download_source=receipt, source_release_date="2026-08-17",
               geometry_comparison="Shapely topological equality, exact coordinate equality with tolerance 0, and complete DBF attribute equality, joined by exact VILLCODE",
               release_news_source=NEWS_URL, release_news_returned_count=len(news), observed_release_news=news_rows,
               status="current_official_distribution_checked",
               temporal_conclusion=f"{receipt['downloaded_at'][:10]} 官方目前下載成果為 {current_stem}，北北基逐圖徵比較結果另列；未取得獨立 2026-09 幾何快照。",
               limitations=["目前發布成果一致不是地方在兩日期間沒有法定異動的證明。", "News API 返回的清單沒有完備歷史涵蓋宣告，不以未列公告證明沒有異動。", "人口2026-09可按已核實身分連接此明示版本圖層；分析須同時顯示人口月份與幾何版本。"], independent_review_status="pending"))
    municipal_rows = [{k.split(",")[0]: v for k, v in r.items()} for r in csv.DictReader((raw / "ntpc_boundaries.csv").read_text(encoding="utf-8-sig").splitlines())]
    pairs = [(code, ALIASES[code][0], str(int(code[5:7])), str(int(code[8:]))) for code in ALIASES]
    diagnostics = []
    for code, district, local_district, local_village in pairs:
        matches = [r for r in municipal_rows if (r["admit"], r["admit_id"], r["admiv_id"]) == (district, local_district, local_village)]
        if len(matches) != 1:
            raise ValueError("Municipal source-specific tuple is not unique")
        diagnostics.append({"reference_village_code": code, "district_name": district,
                            "municipal_admit_id": local_district, "municipal_admiv_id": local_village,
                            "municipal_name": matches[0]["admiv"], "ris_name": new[code]["village_name"],
                            "status": "diagnostic_only_not_an_approved_id_bridge", "geometry_used": False})
    write_json(root / OUT / "municipal_source_diagnostics.json", envelope("ntpc_village_boundary_source_diagnostics", diagnostics,
               source_page=NTPC_PAGE, download_url=NTPC_URL, downloaded_row_count=len(municipal_rows),
               source_updated_at=None, page_stated_dataset_count=1039, page_preview_count=1032,
               current_ris_new_taipei_count=len([k for k in new if k.startswith("65000")]),
               limitations=["地方資料頁資料量顯示1039，API全頁取回及頁面預覽仍為1032；未核實其幾何日期，不替代NLSC版本。", "admit_id與admiv_id為來源自身代碼；此診斷只列出候選tuple，不將補零拼接當作核實橋接。", "瓦嗂、灰嗂、蜣寮及石嘈等字串與地方里長頁及戶政用字不一致，不採為正式別名。"],
               independent_review_status="pending"))
    outputs = sorted(str(p.relative_to(root)) for p in (root / OUT).glob("*.json") if p.name != "manifest.json")
    write_json(root / OUT / "manifest.json", envelope("official_boundary_evidence_sources", sources,
               generated_at=now(), population_period="2026-09", geometry_version="2026-08-17", outputs=outputs,
               independent_review_status="pending", license={"name": "政府資料開放授權條款-第1版", "url": "https://data.gov.tw/license"}))
    return {"aliases": len(aliases), "population_ris_matches": len(population_rows),
            "national_code_changes": len(changes), "published_geometry_corrections": len(geometry_changes),
            "geometry_checks": geometry_checks}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--offline", action="store_true", help="Reconcile retained official source files and receipts")
    args = parser.parse_args()
    if not args.offline:
        parser.error("Use --offline with the documented official files and receipts; source URLs are listed in the manifest")
    print(json.dumps(build(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
