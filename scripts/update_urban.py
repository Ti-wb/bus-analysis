#!/usr/bin/env python3
"""Download and normalize Taipei urban transport and facility open data."""
import argparse
import csv
from contextlib import nullcontext
from datetime import datetime, date
from collections import Counter
import gzip
import io
import json
import math
from pathlib import Path
import re
import subprocess
import tempfile
from urllib.parse import urljoin, urlparse
import xml.etree.ElementTree as ET
import zipfile
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup
from pyproj import CRS, Transformer
import shapefile
from shapely.geometry import shape

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "raw_data/urban"
OUT = ROOT / "data/urban"
TZ = ZoneInfo("Asia/Taipei")
PAGES = {
    "bus_lanes": "https://data.taipei/dataset/detail?id=f0a32701-d708-4dd7-beb1-9450b4e5eb8e",
    "parking": "https://data.taipei/dataset/detail?id=d5c0656b-5250-4179-a491-c94daa56ef2c",
    "curb_parking": "https://data.taipei/dataset/detail?id=5a911ea5-1694-4301-808e-e1780d971611",
    "bus_restrictions": "https://data.taipei/dataset/detail?id=f524d134-c5d6-4417-bbb5-c37038b69922",
    "public_toilets": "https://data.taipei/dataset/detail?id=ca205b54-a06f-4d84-894c-d6ab5079ce79",
    "tmc_travel_times": "https://data.gov.tw/dataset/135753",
}

def now():
    return datetime.now(TZ).isoformat(timespec="seconds")

def write_json(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".part")
    with temp.open("w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, allow_nan=False, indent=2)
        f.write("\n")
    temp.replace(path)

class Downloads:
    def __init__(self, offline=False):
        self.offline = offline
        self.path = RAW / "manifest.json"
        self.entries = json.loads(self.path.read_text()) if self.path.exists() else {}

    def fetch(self, url, name, page_url, source_updated_at=None):
        if urlparse(url).scheme != "https":
            raise ValueError(f"HTTPS required: {url}")
        path = RAW / name
        path.parent.mkdir(parents=True, exist_ok=True)
        old = self.entries.get(name)
        if self.offline:
            if not old or not path.exists() or not path.stat().st_size:
                raise ValueError(f"Missing or empty offline source: {name}")
            return path
        temp = path.with_suffix(path.suffix + ".part")
        headers = path.with_suffix(path.suffix + ".headers")
        command = ["curl", "--fail", "--location", "--silent", "--show-error",
                   "--proto", "=https", "--proto-redir", "=https",
                   "--connect-timeout", "20", "--max-time", "240", "--retry", "2",
                   "--user-agent", "bus-analysis/1.0", "--dump-header", str(headers),
                   "--output", str(temp), "--write-out", "%{json}", url]
        try:
            p = subprocess.run(command, capture_output=True, text=True, timeout=800)
            if p.returncode:
                raise RuntimeError(p.stderr.strip())
            stat = json.loads(p.stdout)
            if stat["http_code"] != 200 or not temp.stat().st_size:
                raise ValueError(f"Unexpected download: {name}")
            response_headers = {}
            for line in headers.read_text().splitlines():
                if line.startswith("HTTP/"):
                    response_headers = {}
                elif ":" in line:
                    k,v = line.split(":",1)
                    response_headers[k.lower()] = v.strip()
            temp.replace(path)
            self.entries[name] = {"page_url": page_url, "download_url": url,
                                  "resolved_url": stat["url_effective"],
                                  "raw_file": str(path.relative_to(ROOT)),
                                  "downloaded_at": now(), "source_updated_at": source_updated_at,
                                  "bytes": path.stat().st_size,
                                  "http_last_modified": response_headers.get("last-modified"),
                                  "content_type": response_headers.get("content-type")}
            write_json(self.path, self.entries)
            print(name, path.stat().st_size, flush=True)
            return path
        finally:
            temp.unlink(missing_ok=True)
            headers.unlink(missing_ok=True)

def discover_page(path, url):
    soup=BeautifulSoup(path.read_text(),"html.parser")
    items=[]
    for a in soup.select("a[href]"):
        if a.get_text(strip=True) != "下載":
            continue
        tr=a.find_parent("tr")
        if not tr:
            continue
        cells=tr.find_all("td")
        items.append({"title":cells[0].get_text(strip=True), "format":cells[2].get_text(strip=True),
                      "source_updated_at":cells[3].get_text(strip=True), "url":urljoin(url,a["href"])})
    return items

def acquire(dl):
    resources={}
    for topic,url in PAGES.items():
        page=dl.fetch(url,topic+"_page.html",url)
        if topic == "tmc_travel_times":
            soup=BeautifulSoup(page.read_text(),"html.parser")
            links=[urljoin(url,a["href"]) for a in soup.select("a[href]") if "data.taipei/api" in a["href"]]
            resources[topic]=[{"url":u,"title":"TMC feed descriptor","format":"CSV","source_updated_at":None} for u in dict.fromkeys(links)]
        else:
            resources[topic]=discover_page(page,url)
        for i,r in enumerate(resources[topic]):
            if topic == "parking" and "CMS" in r["title"]:
                continue
            extension=r["format"].lower()
            if extension == "shp": extension="zip"
            name=f"{topic}_{i}.{extension}"
            stamp=r["source_updated_at"]
            if stamp and re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}", stamp):
                stamp=datetime.fromisoformat(stamp).replace(tzinfo=TZ).isoformat()
            else:
                stamp=None
            dl.fetch(r["url"],name,url,stamp)
            r["raw_file"]=name
    descriptor=csv_rows("tmc_travel_times_0.csv")
    if len(descriptor)!=1:
        raise ValueError("Unexpected travel-time descriptor")
    dl.fetch(descriptor[0]["介接網址"],"tmc_live.xml",PAGES["tmc_travel_times"])
    write_json(RAW/"resources.json",resources)
    return resources

def text_or_none(value):
    return str(value).strip() or None if value is not None else None

def numeric(value):
    s=text_or_none(value)
    if s is None or s in {"-","—","N/A"}:
        return None
    n=float(s.replace(",",""))
    if not math.isfinite(n):
        raise ValueError(f"Non-finite source number {s}")
    return int(n) if n.is_integer() else n

def csv_rows(name):
    raw=(RAW/name).read_bytes()
    for encoding in ["utf-8-sig","cp950"]:
        try:
            content=raw.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise ValueError(f"Unsupported CSV encoding: {name}")
    return [{k.strip():text_or_none(v) for k,v in row.items()} for row in csv.DictReader(io.StringIO(content))]

def metadata(dl,names,grain,units,limitations,source_time=None,**kwargs):
    sources=[]
    for n in names:
        s=dict(dl.entries[n])
        s["source_resource_updated_at"]=s.pop("source_updated_at",None)
        s["source_data_time"]=source_time
        if n.endswith(".csv"):
            try:
                (RAW/n).read_bytes().decode("utf-8-sig");s["source_encoding"]="utf-8-sig"
            except UnicodeDecodeError:
                (RAW/n).read_bytes().decode("cp950");s["source_encoding"]="cp950"
        sources.append(s)
    return {"sources":sources,"source_data_time":source_time,
            "statistical_period":None,"grain":grain,"units":units,
            "timezone":"Asia/Taipei","missing_values":"Blank source values become null; no missing values imputed as zero.",
            "limitations":limitations,**kwargs}

def dataset(dataset_id,meta,records):
    return {"schema_version":1,"dataset_id":dataset_id,"metadata":meta,"records":records}

def geojson(dataset_id,meta,features):
    return {"type":"FeatureCollection","schema_version":1,"dataset_id":dataset_id,
            "metadata":{"crs":"EPSG:4326","coordinate_order":["longitude","latitude"],**meta},"features":features}

def feature(fid,properties,geometry):
    return {"type":"Feature","id":str(fid),"properties":properties,"geometry":geometry}

def point(lon,lat):
    if lon is None or lat is None:
        return None
    if not (119 <= lon <=123 and 21 <=lat <=27):
        return None
    return {"type":"Point","coordinates":[round(lon,7),round(lat,7)]}

def parse_bus_lanes(dl):
    ns={"k":"http://www.opengis.net/kml/2.2"}
    tree=ET.parse(RAW/"bus_lanes_0.kml")
    fields={"項次":"source_item_id","路線":"corridor_name","路段":"road_name","起點":"start_location",
            "終點":"end_location","長度公里":"length_km","布設方式":"lane_placement","行駛方向":"direction_description",
            "實施日期":"implementation_date_raw"}
    features=[]
    for i,p in enumerate(tree.findall(".//k:Placemark",ns),1):
        props={fields[e.attrib["name"]]:text_or_none(e.text) for e in p.findall(".//k:SimpleData",ns)}
        props["length_km"]=numeric(props["length_km"])
        props["name"]=p.findtext("k:name",namespaces=ns)
        d=props["implementation_date_raw"]
        m=re.fullmatch(r"(\d+)年(\d+)月",d or "")
        props["implementation_month"]=f"{int(m[1])+1911:04d}-{int(m[2]):02d}" if m else None
        parts=[]
        for line in p.findall(".//k:LineString/k:coordinates",ns):
            coords=[]
            for s in line.text.split():
                lon,lat,*_=map(float,s.split(","))
                if point(lon,lat) is None:raise ValueError("KML out-of-region coordinate")
                coords.append([lon,lat])
            parts.append(coords)
        if not parts:raise ValueError("Missing bus lane geometry")
        geometry={"type":"LineString","coordinates":parts[0]} if len(parts)==1 else {"type":"MultiLineString","coordinates":parts}
        if not shape(geometry).is_valid:raise ValueError("Invalid KML geometry")
        features.append(feature(f"lane-{i}",props,geometry))
    meta=metadata(dl,["bus_lanes_0.kml"],"one mapped bus-lane corridor section",{"length_km":"km"},
                  ["線形不代表運量。路段名稱與路廊統計尚未做未驗證關聯。"],
                  field_mapping={v:k for k,v in fields.items()},source_crs="KML WGS84 per KML standard",
                  source_resource_updated_at=dl.entries["bus_lanes_0.kml"]["source_updated_at"],
                  coordinate_processing="KML longitude,latitude retained; altitude zero omitted.")
    return {"bus_lanes.geojson":geojson("taipei_bus_lanes",meta,features)}

def parse_bus_restrictions(dl):
    fields={"項次":"source_item_id","縣市別":"city","縣市別代碼":"city_code","鄉鎮市區":"district",
            "地址-行政區域代碼":"district_code","路線路名":"road_name","路線樁號及起迄地名":"section_description",
            "管制通行措施":"restriction_description","路況概述":"road_condition_description"}
    rows=[]
    for i,r in enumerate(csv_rows("bus_restrictions_0.csv"),1):
        rows.append({"record_id":f"restriction-{i}","source_row":i+1,**{out:r[src] for src,out in fields.items()}})
    dup={k:v for k,v in Counter(r["source_item_id"] for r in rows).items() if v>1}
    meta=metadata(dl,["bus_restrictions_0.csv"],"one source road restriction row",{},
                  ["來源只有道路文字，沒有線形或路網邊 ID；未自行地理編碼。",
                   "保留完整時段、車長及重量條件文字，不能直接當全年全天禁止所有公車。",
                   "來源項次有重複，record_id 為本快照列序識別，不是官方 ID。"],
                  field_mapping={v:k for k,v in fields.items()},source_item_id_duplicates=dup)
    return {"bus_restrictions.json":dataset("taipei_bus_restrictions",meta,rows)}

def parse_toilets(dl):
    fields={"行政區":"district","公廁類別":"category","公廁名稱":"name","公廁地址":"address",
            "經度":"longitude","緯度":"latitude","管理單位":"managing_unit","座數":"toilet_unit_count",
            "特優級":"excellent_grade_count","優等級":"good_grade_count","普通級":"normal_grade_count",
            "改善級":"improvement_grade_count","無障礙廁座數":"accessible_toilet_count","親子廁座數":"family_toilet_count",
            "其他設施":"other_facilities","照護床位置":"care_bed_location","污物盆位置":"slop_sink_location"}
    numbers={"longitude","latitude","toilet_unit_count","excellent_grade_count","good_grade_count",
             "normal_grade_count","improvement_grade_count","accessible_toilet_count","family_toilet_count"}
    features=[];invalid=[];unreconciled=[]
    for i,r in enumerate(csv_rows("public_toilets_0.csv"),1):
        p={out:numeric(r[src]) if out in numbers else r[src] for src,out in fields.items()}
        p["source_row"]=i+1
        geom=point(p["longitude"],p["latitude"])
        if geom is None:invalid.append(i+1)
        for k in numbers-{"longitude","latitude"}:
            if p[k] is not None and p[k]<0:raise ValueError("Negative toilet count")
        grade_sum=sum(p[k] for k in ["excellent_grade_count","good_grade_count","normal_grade_count","improvement_grade_count"])
        if grade_sum!=p["toilet_unit_count"]:unreconciled.append(i+1)
        features.append(feature(f"toilet-{i}",p,geom))
    meta=metadata(dl,["public_toilets_0.csv"],"one public-toilet source location",{k:"座（來源用語）" for k in numbers-{"longitude","latitude"}},
                  ["來源無官方公廁 ID；feature ID 只代表本快照列序。公廁存在不代表公車駕駛隨時可進入、停車或休息。",
                   "來源的座數和分級數可能不相等，保留原值；無障礙與親子欄位可能重疊，不相加推估總量。"],
                  source_crs="EPSG:4326 inferred from explicit longitude/latitude columns and Taiwan bounds",
                  field_mapping={v:k for k,v in fields.items()},quality={"invalid_coordinate_rows":invalid,"grade_sum_differs_from_total_rows":unreconciled})
    return {"public_toilets.geojson":geojson("taipei_public_toilets",meta,features)}

def parking_time(root):
    raw=root.findtext("UPDATETIME")
    if raw is None:return None
    # Here CST is the Taipei data provider's local clock, explicitly resolved to UTC+8.
    return datetime.strptime(raw,"%a %b %d %H:%M:%S CST %Y").replace(tzinfo=TZ).isoformat()

def parse_fares(parent):
    rows=[]
    for e in parent.findall("FareRule"):
        rows.append({"parking_type_code":text_or_none(e.findtext("ParkingType")),
                     "rate_type_code":text_or_none(e.findtext("RateType")),
                     "week_type_raw":text_or_none(e.findtext("WeekType")),
                     "chargeable_start_time_raw":text_or_none(e.findtext("ChargeableSTime")),
                     "chargeable_end_time_raw":text_or_none(e.findtext("ChargeableETime")),
                     "parking_rates_twd":numeric(e.findtext("ParkingRates")),
                     "charge_unit":text_or_none(e.findtext("CUnit"))})
    return rows

def parse_parking(dl):
    static=ET.parse(RAW/"parking_2.xml").getroot()
    live=ET.parse(RAW/"parking_0.xml").getroot()
    static_time=parking_time(static);live_time=parking_time(live)
    fields={"ID":"parking_id","AREA":"district","NAME":"name","TYPE":"availability_type_code",
            "TYPE2":"ownership_type","PTYPE":"parking_type_code","SUMMARY":"space_summary",
            "ADDRESS":"address","TEL":"telephone","PAYEX":"fee_description","SERVICETIME":"service_time_description",
            "TW97X":"twd97_x","TW97Y":"twd97_y","TOTALCAR":"total_car_spaces","TOTALMOTOR":"total_motorcycle_spaces",
            "TOTALBUS":"total_bus_spaces","Pregnancy_First":"pregnancy_priority_spaces","Handicap_First":"disability_priority_spaces",
            "TOTALLARGEMOTOR":"total_heavy_motorcycle_spaces","ChargingStation":"charging_station_count",
            "Taxi_OneHR_Free":"taxi_one_hour_free_code","AED_Equipment":"aed_equipment_code",
            "CellSignal_Enhancement":"cell_signal_enhancement_code","Accessibility_Elevator":"accessibility_elevator_code",
            "Phone_Charge":"phone_charge_code","Child_Pickup_Area":"child_pickup_area_code","Handicap_Discount":"disability_discount_description"}
    nums={"TW97X","TW97Y","TOTALCAR","TOTALMOTOR","TOTALBUS","Pregnancy_First","Handicap_First","TOTALLARGEMOTOR","ChargingStation"}
    transform=Transformer.from_crs(3826,4326,always_xy=True)
    static_features=[];invalid=[]
    for row in static.findall("PARK"):
        p={out:numeric(row.findtext(src)) if src in nums else text_or_none(row.findtext(src)) for src,out in fields.items()}
        p["max_info_fare_rules"]=parse_fares(row.find("MAXInfo"))
        p["fare_info_rules"]=parse_fares(row.find("FareInfo"))
        p["entrances"]=[]
        for entry in row.findall("EntranceCoord/EntrancecoordInfo"):
            lat=numeric(entry.findtext("Xcod"));lon=numeric(entry.findtext("Ycod"))
            p["entrances"].append({"source_xcod":lat,"source_ycod":lon,
                                    "description":text_or_none(entry.findtext("Address")),
                                    "coordinates":point(lon,lat)["coordinates"] if point(lon,lat) else None})
        x,y=p["twd97_x"],p["twd97_y"]
        geom=None
        if x is not None and y is not None and 250000<x<400000 and 2600000<y<2900000:
            geom=point(*transform.transform(x,y))
        if geom is None:invalid.append(p["parking_id"])
        static_features.append(feature(p["parking_id"],p,geom))
    ids=[f["id"] for f in static_features]
    if len(ids)!=len(set(ids)):raise ValueError("Duplicate parking static ID")
    cols={"AVAILABLECAR":"available_car_spaces","AVAILABLEMOTOR":"available_motorcycle_spaces",
          "AVAILABLEBUS":"available_bus_spaces","AVAILABLEHANDICAP":"available_accessible_spaces",
          "AVAILABLEPREGNANCY":"available_pregnancy_spaces","AVAILABLEHEAVYMOTOR":"available_heavy_motorcycle_spaces"}
    live_rows=[];negative=Counter()
    for row in live.findall("PARK"):
        p={"parking_id":row.findtext("ID"),"source_data_time":live_time,"availability_status":{},"source_sentinels":{}}
        for src,out in cols.items():
            n=numeric(row.findtext(src))
            if n is not None and n<0:
                negative[str(n)]+=1;p["source_sentinels"][out]=n
                p[out]=None;p["availability_status"][out]="no_such_space_type" if n==-9 else "source_negative_status_unresolved"
            else:
                p[out]=n;p["availability_status"][out]="reported" if n is not None else "missing"
        live_rows.append(p)
    live_ids=[r["parking_id"] for r in live_rows]
    if len(live_ids)!=len(set(live_ids)):raise ValueError("Duplicate parking live ID")
    orphan=sorted(set(live_ids)-set(ids))
    static_meta=metadata(dl,["parking_2.xml"],"one parking facility",{k:"spaces" for k in fields.values() if k.endswith("_spaces")},
                         ["TOTALBUS 是大客車停車位；不表示准許市區公車調度、待班或過夜。",
                          "即時資料與靜態資料分開輸出；靜態提供車位不保證即時剩餘位有回報。",
                          "設施 code 未自行轉成布林；停車費應合併 rate_type_code、week_type_raw、時段與 charge_unit 解讀。"],
                         static_time,source_crs="EPSG:3826 TWD97 / TM2 zone 121 inferred from TW97X/TW97Y field names and Taipei easting/northing bounds",
                         field_mapping={v:k for k,v in fields.items()},entrance_coordinate_note="Nested Xcod is latitude and Ycod is longitude, determined from explicit value ranges; source values retained.",
                         quality={"invalid_main_coordinate_ids":invalid,"unique_parking_ids":True})
    live_meta=metadata(dl,["parking_0.xml"],"one parking facility at one snapshot",{v:"spaces" for v in cols.values()},
                       ["單次剩餘位快照，不是歷史供給或可用場站容量。-9 為無此類車格，輸出 null 並保留狀態。",
                        "充電槍狀態不是大客車泊位資料，本整理未輸出；原 XML 完整保留。"],live_time,
                       field_mapping={v:k for k,v in cols.items()},quality={"orphan_parking_ids":orphan,"negative_sentinels":dict(negative)})
    candidates=[f for f in static_features if (f["properties"]["total_bus_spaces"] or 0)>0]
    lookup={r["parking_id"]:r for r in live_rows}
    bus_features=[]
    for f in candidates:
        p=dict(f["properties"]);r=lookup.get(f["id"])
        p.update({"available_bus_spaces":r["available_bus_spaces"] if r else None,
                  "available_bus_status":r["availability_status"]["available_bus_spaces"] if r else "facility_absent_from_snapshot",
                  "availability_source_time":live_time if r else None,
                  "availability_source_sentinel":r["source_sentinels"].get("available_bus_spaces") if r else None})
        bus_features.append(feature(f["id"],p,f["geometry"]))
    candidate_meta={**static_meta,"sources":static_meta["sources"]+live_meta["sources"],
                    "selection":"total_bus_spaces > 0; exact parking_id left join to live snapshot; source page explicitly defines shared ID",
                    "availability_source_data_time":live_time,"record_count":len(bus_features),"all_static_record_count":len(static_features),
                    "quality":{**static_meta["quality"],
                               "bus_availability_status_counts":dict(Counter(f["properties"]["available_bus_status"] for f in bus_features)),
                               "static_bus_spaces_but_live_no_such_type_ids":[f["id"] for f in bus_features if f["properties"]["available_bus_status"]=="no_such_space_type"]},
                    "limitations":static_meta["limitations"]+["部分靜態TOTALBUS>0的場站在即時檔回報-9，存在來源間矛盾；保留null及原始狀態，不把它解讀為0個剩餘位或刪除靜態容量。"]}
    return {"parking_lots.geojson":geojson("taipei_parking_lots",static_meta,static_features),
            "parking_availability.json":dataset("taipei_parking_availability",live_meta,live_rows),
            "bus_parking_lots.geojson":geojson("taipei_bus_parking_lots",candidate_meta,bus_features)}

def transform_coords(coords,transform):
    if isinstance(coords[0],(float,int)):
        lon,lat=transform.transform(coords[0],coords[1])
        if point(lon,lat) is None:raise ValueError("Curb parking geometry outside Taiwan")
        # Rounding small parking polygons can turn valid rings into self-crossings.
        return [lon,lat]
    return [transform_coords(c,transform) for c in coords]

def parse_curb_parking(dl,include_all=False):
    # Keep the complete source reference but normally emit only the requested
    # large-vehicle/bus-car shared spaces. The full layer is an explicit opt-in.
    extracted=RAW/"curb_parking_shapefile"
    extracted.mkdir(exist_ok=True)
    with zipfile.ZipFile(RAW/"curb_parking_1.zip") as z:
        for info in z.infolist():
            if Path(info.filename).name!=info.filename:
                raise ValueError("Unexpected archive path")
            target=extracted/info.filename
            if not target.exists() or target.stat().st_size!=info.file_size:
                target.write_bytes(z.read(info))
    shp=next(extracted.glob("*.shp"));prj=shp.with_suffix(".prj")
    crs=CRS.from_wkt(prj.read_text())
    transform=Transformer.from_crs(crs,4326,always_xy=True)
    reader=shapefile.Reader(str(shp),encoding=shp.with_suffix(".cpg").read_text().strip())
    mappings={"pktype":"parking_type_code","pktype1":"parking_type_label_source","pkid":"parking_space_id",
              "pkdoc":"document_id","pknote":"note","pkpern":"responsible_staff","pkdate":"source_installation_date_raw",
              "pkwork":"work_description","pkgoup":"group_code","zone":"zone_code","pkrate":"rate_description",
              "pktime":"time_description","pkdel":"deletion_date_raw","pkaply":"application_description",
              "pkredw":"redraw_date_raw","pkaltr":"alteration_description","pkadrs":"address",
              "pkzone":"parking_zone_code","pknos":"space_numbers_description","pkkind":"parking_orientation_code",
              "pkroad":"road_section_name","pknum":"space_count_raw","pkchk":"inspection_description",
              "editby":"edited_by","keyid":"feature_key_id","workc_keyi":"work_key_id",
              "workc_in_u":"work_user_id","workc_in_t":"work_date","edittime":"edit_date",
              "rdcode":"road_code","mainnet_ke":"road_network_key_id","width":"width_m","len":"length_m",
              "workunit":"work_unit","park11keyi":"parking_reference_key_id","areakeyid":"area_key_id",
              "motoctol":"motorcycle_conversion_code","ctoldate":"conversion_date_raw","linereco":"line_reconstruction_description",
              "hardreco":"hardware_reconstruction_description","pkterm":"parking_term_description","varcode":"variant_code",
              "roadname":"road_name","area_name":"district","village_na":"village","bike_slot_":"bike_slot_code",
              "bike_slo_1":"bike_slot_description"}
    code_labels={"01":"小型車","02":"機車","03":"身心障礙專用（汽）","04":"身心障礙專用（機）","05":"警車",
                 "06":"大型車","07":"貨車裝卸專用","08":"計程車","09":"限時停車","10":"時段性禁停汽車",
                 "11":"機慢車停放區","12":"公用汽車","13":"公用機車","14":"警用機車","15":"汽車停車彎",
                 "16":"機慢車停放區（警車）","17":"消防車","18":"機慢車停放區（身心障礙專用）",
                 "19":"時段禁停機車","20":"限時停車機車","21":"垃圾車專用（時段管制）","22":"汽機車彈性共用",
                 "23":"大客車與小型車共用格位"}
    meta=metadata(dl,["curb_parking_1.zip"],"one source parking-space polygon",{"width_m":"m","length_m":"m","space_count":"spaces"},
                  ["06 為大型車，並未限定全部為大客車；23 為大客車與小型車共用，不能視為大客車專用泊位。",
                   "停車格位不代表已允許公車待班或駕駛休憩。來源可能含刪除/異動註記，未自行剔除。",
                   "網站資源更新時間不是圖層實際更新時間；source_data_time 留 null；zip member名日期僅作檔名證據。",
                   "來源存在網頁代碼表之外的代碼；保留原碼而不自行解讀。streetview系列保留來源數值，語意/單位未核實。"],
                  source_crs=crs.to_string(),source_prj_archive_member=prj.name,source_prj_wkt=prj.read_text(),
                  source_archive_members=[p.name for p in extracted.iterdir()],field_mapping={v:k for k,v in mappings.items()},
                  code_tables={"parking_type_code":code_labels},selection="All source records; no Taipei clipping needed because this source is Taipei curb parking.")
    path=OUT/"curb_parking_spaces.geojson";temp=path.with_suffix(".geojson.part")
    OUT.mkdir(exist_ok=True)
    counts=Counter();keys=set();duplicates=[];candidates=[];invalid=[];unknown_counts=[];all_count=0;written=0
    with temp.open("w",encoding="utf-8") if include_all else nullcontext(None) as f:
        if f:
            header=geojson("taipei_curb_parking_spaces",{**meta,"quality_summary_file":"data/urban/curb_parking_type_counts.json"},[])
            f.write(json.dumps({k:v for k,v in header.items() if k!="features"},ensure_ascii=False,allow_nan=False)[:-1]+',"features":[\n')
        for idx,record in enumerate(reader.iterRecords(),1):
            raw=record.as_dict()
            code=text_or_none(raw["pktype"])
            counts[code]+=1;all_count=idx
            key=str(raw["keyid"]) if raw["keyid"] is not None else None
            if key in keys:duplicates.append(key)
            keys.add(key)
            if not include_all and code not in {"06","23"}:
                continue
            p={}
            for k,v in raw.items():
                out=mappings.get(k,k)
                if isinstance(v,date):v=v.isoformat()
                elif isinstance(v,str):v=text_or_none(v)
                if (out.endswith("_id") or out.endswith("_code")) and v is not None:v=str(v)
                p[out]=v
            code=p["parking_type_code"]
            p["parking_type_label"]=code_labels.get(code)
            p["source_record_number"]=idx
            raw_count=p["space_count_raw"]
            p["space_count"]=int(raw_count) if raw_count and re.fullmatch(r"\d+",raw_count) else None
            if raw_count and p["space_count"] is None:unknown_counts.append(idx)
            p["bus_candidate_category"]="large_vehicle_unspecified" if code=="06" else "bus_car_shared" if code=="23" else None
            geom=reader.shape(idx-1).__geo_interface__
            geom["coordinates"]=transform_coords(geom["coordinates"],transform)
            if not shape(geom).is_valid:invalid.append(idx)
            feat=feature(f"curb-{idx}",p,geom)
            if f:
                if written:f.write(',\n')
                f.write(json.dumps(feat,ensure_ascii=False,allow_nan=False,separators=(',',':')))
                written+=1
            if code in {"06","23"}:candidates.append(feat)
        if f:f.write('\n]}\n')
    if include_all:temp.replace(path)
    qc={"source_record_count":len(reader),"geometry_validation_scope":"all source rows" if include_all else "selected large-vehicle/shared candidate rows",
        "output_candidate_count":len(candidates),"duplicate_feature_key_ids":duplicates,
        "invalid_output_geometry_source_rows":invalid,"non_numeric_space_count_source_rows":unknown_counts,
        "unmapped_parking_type_codes":{str(k):v for k,v in counts.items() if k is not None and k not in code_labels},
        "missing_parking_type_count":counts[None]}
    if all_count!=len(reader):raise ValueError("Curb shape count mismatch")
    candidate_meta={**meta,"selection":"parking_type_code in ['06','23']", "quality":qc,
                    "all_source_record_count":all_count,"candidate_record_count":len(candidates)}
    countrows=[{"parking_type_code":k,"parking_type_label":code_labels.get(k),"source_polygon_count":v} for k,v in sorted(counts.items(),key=lambda x:x[0] or '')]
    outputs={"curb_large_vehicle_spaces.geojson":geojson("taipei_curb_large_vehicle_spaces",candidate_meta,candidates),
             "curb_parking_type_counts.json":dataset("taipei_curb_parking_type_counts",{**meta,"grain":"one parking type code","quality":qc},countrows)}
    points=[]
    for idx,r in enumerate(csv_rows("curb_parking_0.csv"),1):
        props={"district":r["行政區"],"address":r["地址"],"space_count":numeric(r["格位數"]),
               "nearby_attraction_or_shopping_area":r["周邊景點或商圈"],"latitude":numeric(r["場站緯度"]),"longitude":numeric(r["場站經度"]),"source_row":idx+1}
        points.append(feature(f"commercial-stop-{idx}",props,point(props["longitude"],props["latitude"])))
    cm=metadata(dl,["curb_parking_0.csv"],"one shared commercial short-stop location",{"space_count":"spaces"},
                ["本附屬清單是營業用共用臨停區，未證實所有格位均可供大型公車使用。"],
                source_crs="EPSG:4326 inferred from explicit longitude/latitude columns and Taiwan bounds")
    outputs["commercial_short_stops.geojson"]=geojson("taipei_commercial_short_stops",cm,points)
    reader.close()
    # Expanded DBF/SHP intermediates total ~538 MB and can be rebuilt from ZIP.
    for p in extracted.iterdir():
        if p.is_file() and p.name in [i.filename for i in z.infolist()]:p.unlink()
    extracted.rmdir()
    reference={"dataset_id":"taipei_curb_parking_spaces","status":"source_reference_only" if not include_all else "normalized",
               "source_record_count":all_count,"output_record_count":written,"quality":qc,
               "source":dl.entries["curb_parking_1.zip"],"rebuild_command":"uv run scripts/update_urban.py --offline --include-all-curb"}
    if include_all:reference["file"]="data/urban/curb_parking_spaces.geojson"
    return outputs,reference

def parse_travel_times(dl):
    root=ET.parse(RAW/"tmc_live.xml").getroot()
    ns={"t":"http://traffic.transportdata.tw/standard/traffic/schema/"}
    updated=root.findtext("t:UpdateTime",namespaces=ns)
    lives=root.find("t:ETagPairLives",ns)
    if lives is None:raise ValueError("Unexpected travel-time XML")
    if len(lives):
        raise ValueError("ETagPairLive feed gained observations; add and verify field/unit parser before promotion")
    meta=metadata(dl,["tmc_travel_times_0.csv","tmc_live.xml"],"ETag paired-road vehicle travel-time observation (none returned)",{},
                  ["指定 data.gov.tw/135753 descriptor 指向 ETagPairLive.xml；不是經核對的 TMC 路段碼資料。",
                   "XML observations 為空；保留空集合不代表0秒、無壅塞或無車流。",
                   "來源 body UpdateTime 為2024-12-18，HTTP Last-Modified與下載日期不代表實際觀測日期。",
                   "道路旅行時間不能當成包含停站的公車旅行時間。"],updated,
                  acquisition_status="source_empty_stale",source_reported_update_interval_seconds=numeric(root.findtext("t:UpdateInterval",namespaces=ns)),
                  authority_code=root.findtext("t:AuthorityCode",namespaces=ns),observation_count=0)
    meta["sources"][0]["source_data_time"]=None
    return {"road_travel_times.json":dataset("taipei_road_travel_times",meta,[])}

def build(dl,include_all=False):
    all_outputs={}
    for fn in [parse_bus_lanes,parse_bus_restrictions,parse_toilets,parse_parking,parse_travel_times]:
        out=fn(dl)
        for name,obj in out.items():
            write_json(OUT/name,obj)
        all_outputs.update(out)
    curb,streamed=parse_curb_parking(dl,include_all)
    for name,obj in curb.items():write_json(OUT/name,obj)
    all_outputs.update(curb)
    inventory=[]
    for name,obj in all_outputs.items():
        items=obj.get("records",obj.get("features"))
        entry={"dataset_id":obj["dataset_id"],"status":obj["metadata"].get("acquisition_status","normalized"),
               "output_files":[str((OUT/name).relative_to(ROOT))],"record_count":len(items),
               "period":obj["metadata"].get("source_data_time"),"limitations":obj["metadata"]["limitations"],
               "bytes":(OUT/name).stat().st_size}
        inventory.append(entry)
    if include_all:
        inventory.append({"dataset_id":streamed["dataset_id"],"status":"normalized",
                          "output_files":[streamed["file"]],"record_count":streamed["source_record_count"],"period":None,
                          "limitations":["圖層實際更新時間未知；來源代碼問題詳見quality。"],
                          "quality":streamed["quality"],"bytes":(ROOT/streamed["file"]).stat().st_size})
    write_json(RAW/"delivery.json",{"schema_version":1,"prepared_at":now(),"datasets":inventory,"source_references":[] if include_all else [streamed]})
    print(json.dumps([{k:v for k,v in x.items() if k in ["dataset_id","status","record_count","bytes"]} for x in inventory],ensure_ascii=False,indent=2))

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--offline",action="store_true")
    parser.add_argument("--include-all-curb",action="store_true",help="Also emit the very large full curb-parking layer; default emits bus/large-vehicle candidates and full-source reference only.")
    args=parser.parse_args()
    dl=Downloads(args.offline)
    acquire(dl)
    build(dl,args.include_all_curb)

if __name__=="__main__":
    main()
