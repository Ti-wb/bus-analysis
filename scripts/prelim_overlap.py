"""Preliminary bus-MRT proximity / overlap indicators from real open data (no ridership).

Inputs : raw_data/taipei_feeds/{GetStop,GetStopLocation,GetRoute}.gz, raw_data/research/mrt_exits.csv (cp950)
Outputs: raw_data/research/derived/stop_location_mrt.csv, raw_data/research/derived/route_mrt_overlap.csv
Caveat : MRT exits carry no line attribute, so "both stops near MRT" is an UPPER BOUND of the
         Seoul-style station-based redundancy (which requires the SAME line).
"""
import csv, gzip, json, math, pathlib, re, sys, collections

sys.stdout.reconfigure(encoding="utf-8")
ROOT = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else ".")
FEEDS, RESEARCH = ROOT / "raw_data/taipei_feeds", ROOT / "raw_data/research"
OUT = RESEARCH / "derived"
OUT.mkdir(parents=True, exist_ok=True)
RADIUS_M = 500


def load(name):
    return json.loads(gzip.decompress((FEEDS / f"{name}.gz").read_bytes()).decode("utf-8-sig"))["BusInfo"]


def haversine(lon1, lat1, lon2, lat2):
    r = 6371000
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


# MRT exits -> station name ("頂埔站出口1" -> "頂埔")
exits = []
with open(RESEARCH / "mrt_exits.csv", encoding="cp950", newline="") as f:
    for row in csv.DictReader(f):
        m = re.match(r"(.+?)站", row["出入口名稱"])
        exits.append((m.group(1) if m else row["出入口名稱"], float(row["經度"]), float(row["緯度"])))

locs = load("GetStopLocation")
loc_near = {}
with open(OUT / "stop_location_mrt.csv", "w", encoding="utf-8-sig", newline="") as f:
    w = csv.writer(f)
    w.writerow(["stopLocationId", "name", "lon", "lat", "nearest_mrt", "dist_m", f"within_{RADIUS_M}m"])
    for l in locs:
        lon, lat = float(l["lon"]), float(l["lat"])
        st, d = min(((s, haversine(lon, lat, x, y)) for s, x, y in exits), key=lambda t: t[1])
        loc_near[l["id"]] = (st, d)
        w.writerow([l["id"], l["name"], lon, lat, st, round(d), int(d <= RADIUS_M)])

routes = {r["Id"]: r for r in load("GetRoute")}
seqs = collections.defaultdict(list)
for s in load("GetStop"):
    seqs[(s["routeId"], s["goBack"])].append((int(s["seqNo"]), s["stopLocationId"], float(s["longitude"]), float(s["latitude"])))

rows = []
for (rid, gb), stops in seqs.items():
    stops.sort()
    total = near_len = 0.0
    n_near = 0
    for i, (_, lid, lon, lat) in enumerate(stops):
        near = loc_near.get(lid, ("", 1e9))[1] <= RADIUS_M
        n_near += near
        if i:
            _, plid, plon, plat = stops[i - 1]
            seg = haversine(plon, plat, lon, lat)
            total += seg
            if near and loc_near.get(plid, ("", 1e9))[1] <= RADIUS_M:
                near_len += seg
    r = routes.get(rid, {})
    rows.append({
        "routeId": rid, "nameZh": r.get("nameZh", ""), "provider": r.get("providerName", ""), "goBack": gb,
        "n_stops": len(stops), "stops_near_mrt_ratio": round(n_near / len(stops), 3) if stops else 0,
        "len_km": round(total / 1000, 2), "seg_both_near_mrt_ratio": round(near_len / total, 3) if total else 0,
        "peakHeadway": r.get("peakHeadway", ""), "offPeakHeadway": r.get("offPeakHeadway", ""),
    })

rows.sort(key=lambda x: -x["seg_both_near_mrt_ratio"])
with open(OUT / "route_mrt_overlap.csv", "w", encoding="utf-8-sig", newline="") as f:
    w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
    w.writeheader(); w.writerows(rows)

near_locs = sum(1 for v in loc_near.values() if v[1] <= RADIUS_M)
print(f"MRT exits: {len(exits)}  stations: {len({e[0] for e in exits})}")
print(f"stopLocations within {RADIUS_M} m of an MRT exit: {near_locs}/{len(loc_near)} ({near_locs/len(loc_near):.1%})")
dirs = [r for r in rows if r["goBack"] in ("0", "1") and r["len_km"] > 0]
over70 = [r for r in dirs if r["seg_both_near_mrt_ratio"] >= 0.7]
print(f"route-directions (goBack 0/1): {len(dirs)};  >=70% length with both ends near MRT: {len(over70)} ({len(over70)/len(dirs):.1%})")
print("top 15:")
for r in rows[:15]:
    print(f"  {r['nameZh']:<10} gb={r['goBack']} len={r['len_km']:>5}km seg_ratio={r['seg_both_near_mrt_ratio']} stops_ratio={r['stops_near_mrt_ratio']}")
