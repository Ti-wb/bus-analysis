"""Quick descriptive stats on Taipei bus blob snapshot: routes, stops, and stop-location route stacking (proxy for overlap)."""
import gzip, json, sys, pathlib, collections
sys.stdout.reconfigure(encoding="utf-8")
d = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "raw_data/taipei_feeds")
load = lambda n: json.loads(gzip.decompress((d / f"{n}.gz").read_bytes()).decode("utf-8-sig"))["BusInfo"]
routes, stops, locs = load("GetRoute"), load("GetStop"), load("GetStopLocation")
print("routes:", len(routes), " distinct nameZh:", len({r["nameZh"] for r in routes}), " providers:", len({r["providerId"] for r in routes}))
print("routeType counts:", collections.Counter(r["routeType"] for r in routes).most_common())
print("genus counts:", collections.Counter(r.get("genus") for r in routes).most_common(10))
print("stop records:", len(stops), " stopLocations:", len(locs))
# routes per stopLocation (both directions, by route name)
per_loc = collections.defaultdict(set)
for s in stops:
    per_loc[s["stopLocationId"]].add(s["routeId"])
name = {l["id"]: (l["name"], l["address"]) for l in locs}
top = sorted(per_loc.items(), key=lambda kv: -len(kv[1]))[:20]
print("\nTop 20 stopLocations by number of routes serving:")
for lid, rs in top:
    print(f"  {len(rs):3d}  {lid}  {name.get(lid, ('?','?'))[0]}  {name.get(lid, ('?','?'))[1]}")
hist = collections.Counter(min(len(v), 30) // 5 * 5 for v in per_loc.values())
print("\nhistogram routes-per-location (bucket of 5):", sorted(hist.items()))
# headway fields sample
hw = [r for r in routes if r.get("peakHeadway")]
print("\nroutes with peakHeadway filled:", len(hw), "sample:", [(r["nameZh"], r["peakHeadway"], r["offPeakHeadway"]) for r in hw[:8]])
lat = [float(s["latitude"]) for s in stops]; lon = [float(s["longitude"]) for s in stops]
print("stop bbox lon", min(lon), max(lon), "lat", min(lat), max(lat))
