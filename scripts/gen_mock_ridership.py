"""Generate MOCK per-stop hourly boarding/alighting data for Taipei buses.

Real stop/route geometry comes from the 公運處 feeds in raw_data/taipei_feeds/; the ridership numbers are SYNTHETIC.
See docs/07_Mock資料規格.md for the model and its assumptions.

Run:  uv run scripts/gen_mock_ridership.py [repo_root] [--seed 42] [--calib PATH|""]
Out:  raw_data/research/mock/mock_stop_hourly.csv           (route x direction x stop x daytype x hour)
      raw_data/research/mock/mock_stoplocation_hourly.csv   (aggregated to physical stop location)
"""
import argparse, csv, gzip, json, math, pathlib, re, sys, collections
import numpy as np

sys.stdout.reconfigure(encoding="utf-8")
ap = argparse.ArgumentParser()
ap.add_argument("root", nargs="?", default=".")
ap.add_argument("--seed", type=int, default=42)
ap.add_argument("--weekday-total", type=float, default=1_141_764,
                help="calibrate the sum of weekday boardings (default: 2024 Taipei avg daily riders)")
ap.add_argument("--calib", default="data/pto_extended/route_annual.json",
                help="route-level annual ridership JSON (records: route_name, passenger_trips, is_total), "
                     "relative to repo root; pass \"\" to disable; matched routes get their real share")
args = ap.parse_args()
ROOT = pathlib.Path(args.root)
FEEDS, RESEARCH = ROOT / "raw_data/taipei_feeds", ROOT / "raw_data/research"
OUT = RESEARCH / "mock"
OUT.mkdir(parents=True, exist_ok=True)
rng = np.random.default_rng(args.seed)

HOURS = list(range(5, 24))
CBD = (121.5170, 25.0478)  # 臺北車站
# Share of a route-direction's daily demand by hour (sums to 1 per day type).
PROFILE = {
    "weekday": np.array([.010, .040, .095, .100, .060, .045, .045, .050, .045, .045, .050, .060, .090, .095, .070, .050, .040, .035, .025]),
    "holiday": np.array([.005, .015, .035, .050, .065, .075, .080, .080, .080, .080, .080, .075, .070, .065, .055, .045, .035, .030, .020]),
}
for k in PROFILE:
    PROFILE[k] = PROFILE[k] / PROFILE[k].sum()
HOLIDAY_FACTOR = 0.65


def load(name):
    return json.loads(gzip.decompress((FEEDS / f"{name}.gz").read_bytes()).decode("utf-8-sig"))["BusInfo"]


def dist_m(lon1, lat1, lon2, lat2):
    kx = 111320 * math.cos(math.radians((lat1 + lat2) / 2))
    return math.hypot((lon2 - lon1) * kx, (lat2 - lat1) * 110540)


def headway_min(s, default):
    """'0715' -> 11.0 ; '12' -> 12 ; '' -> default"""
    s = (s or "").strip()
    if re.fullmatch(r"\d{4}", s):
        return (int(s[:2]) + int(s[2:])) / 2 or default
    if s.isdigit() and int(s) > 0:
        return float(s)
    return default


exits = []
with open(RESEARCH / "mrt_exits.csv", encoding="cp950", newline="") as f:
    for row in csv.DictReader(f):
        exits.append((float(row["經度"]), float(row["緯度"])))

locs = {l["id"]: l for l in load("GetStopLocation")}
routes = {r["Id"]: r for r in load("GetRoute")}  # first record per Id is enough for headways
stops = load("GetStop")

# Routes serving each stop location (a proxy for activity level).
loc_routes = collections.Counter()
seen = set()
for s in stops:
    if (s["stopLocationId"], s["routeId"]) not in seen:
        seen.add((s["stopLocationId"], s["routeId"]))
        loc_routes[s["stopLocationId"]] += 1

# Stop attractiveness weight (time-invariant).
loc_w, loc_mrt = {}, {}
for lid, l in locs.items():
    lon, lat = float(l["lon"]), float(l["lat"])
    d_mrt = min(dist_m(lon, lat, x, y) for x, y in exits)
    d_cbd = dist_m(lon, lat, *CBD)
    w = rng.lognormal(0.0, 0.6)                         # idiosyncratic
    w *= 1 + 2.0 * math.exp(-d_mrt / 300)               # MRT transfer attraction
    w *= 1 + 0.35 * math.log1p(loc_routes[lid])         # busy corridors
    w *= 0.4 + 0.6 * math.exp(-d_cbd / 8000)            # decay away from the CBD
    loc_w[lid], loc_mrt[lid] = w, d_mrt

# Group stops into ordered route-direction sequences.
seqs = collections.defaultdict(list)
for s in stops:
    seqs[(s["routeId"], s["goBack"])].append(s)

# Draw a raw daily size per route-direction, then rescale so weekday boardings sum to --weekday-total.
valid = [(k, v) for k, v in seqs.items() if len(v) >= 2]
raw_daily = {k: rng.lognormal(math.log(1800), 0.8) * (1 + 0.04 * min(len(v), 60)) for k, v in valid}

# Optional: replace random sizes of routes matched by name with the real annual ridership (split evenly by direction).
calibrated = set()
if args.calib:
    annual = {r["route_name"]: r["passenger_trips"] for r in json.loads((ROOT / args.calib).read_text(encoding="utf-8"))["records"]
              if not r.get("is_total") and r.get("passenger_trips")}
    weekday_equiv_days = 250 + 115 * HOLIDAY_FACTOR
    dirs = collections.Counter(rid for rid, _ in raw_daily)
    for (rid, gb) in raw_daily:
        name = routes.get(rid, {}).get("nameZh")
        if name in annual:
            raw_daily[(rid, gb)] = annual[name] / weekday_equiv_days / dirs[rid]
            calibrated.add(rid)
    print(f"calibrated routes: {len(calibrated)}/{len(dirs)} matched by exact name; unmatched keep random size")
# Without calibration: rescale so the network weekday total equals --weekday-total.
# With calibration: real routes keep their real size; unmatched routes are scaled to the calibrated median.
cal = [v for (rid, _), v in raw_daily.items() if rid in calibrated]
rand = [v for (rid, _), v in raw_daily.items() if rid not in calibrated]
if calibrated:
    scale = float(np.median(cal) / np.median(rand)) if rand else 1.0
else:
    scale = args.weekday_total / sum(rand)

rows_stop = []
agg = collections.defaultdict(lambda: np.zeros((2, len(HOURS), 3)))  # [daytype, hour, (board, alight, trips)]
for (rid, gb), seq in valid:
    seq.sort(key=lambda s: int(s["seqNo"]))
    n = len(seq)
    r = routes.get(rid, {})
    w = np.array([loc_w.get(s["stopLocationId"], 0.5) for s in seq])
    pos = np.arange(n) / (n - 1)
    board_share = w * (1.0 - 0.85 * pos)  # few boardings near the end
    board_share[-1] = 0
    board_share /= board_share.sum()
    # P[i, j]: probability a boarder at i alights at j (>i), favours attractive stops 2–10 stops downstream.
    P = np.zeros((n, n))
    for i in range(n - 1):
        k = np.arange(i + 1, n)
        g = w[k] * np.exp(-((k - i - 5) ** 2) / 50.0) + 1e-9
        P[i, k] = g / g.sum()
    alight_share = board_share @ P
    peak_hw = headway_min(r.get("peakHeadway"), 12)
    off_hw = headway_min(r.get("offPeakHeadway"), max(peak_hw * 1.5, 15))
    daily = raw_daily[(rid, gb)] * (1 if rid in calibrated else scale)  # route-direction weekday boardings
    for di, daytype in enumerate(("weekday", "holiday")):
        day_total = daily * (1 if daytype == "weekday" else HOLIDAY_FACTOR)
        for hi, h in enumerate(HOURS):
            peak = daytype == "weekday" and (7 <= h < 9 or 17 <= h < 19)
            trips = max(1, round(60 / (peak_hw if peak else off_hw)))
            lam = day_total * PROFILE[daytype][hi]
            b = rng.poisson(lam * board_share)
            a = rng.poisson(lam * alight_share)
            for i, s in enumerate(seq):
                if b[i] == 0 and a[i] == 0:
                    continue
                rows_stop.append((daytype, h, rid, r.get("nameZh", ""), gb, s["seqNo"], s["stopLocationId"], s["nameZh"],
                                  s["longitude"], s["latitude"], int(b[i]), int(a[i]), trips, int(rid in calibrated), 1))
                agg[s["stopLocationId"]][di, hi] += (b[i], a[i], trips)

cols = ["day_type", "hour", "route_id", "route_name", "go_back", "seq_no", "stop_location_id", "stop_name",
        "lon", "lat", "boardings", "alightings", "trips", "route_total_calibrated", "is_mock"]
with open(OUT / "mock_stop_hourly.csv", "w", encoding="utf-8-sig", newline="") as f:
    w_ = csv.writer(f); w_.writerow(cols); w_.writerows(rows_stop)

with open(OUT / "mock_stoplocation_hourly.csv", "w", encoding="utf-8-sig", newline="") as f:
    w_ = csv.writer(f)
    w_.writerow(["day_type", "hour", "stop_location_id", "stop_name", "lon", "lat", "dist_mrt_m",
                 "boardings", "alightings", "trips", "pax_per_trip", "is_mock"])
    for lid, m in agg.items():
        l = locs.get(lid)
        if not l:
            continue
        for di, daytype in enumerate(("weekday", "holiday")):
            for hi, h in enumerate(HOURS):
                b, a, t = m[di, hi]
                if t:
                    w_.writerow([daytype, h, lid, l["name"], l["lon"], l["lat"], round(loc_mrt[lid]),
                                 int(b), int(a), int(t), round((b + a) / t, 2), 1])

tot_b = sum(r[10] for r in rows_stop if r[0] == "weekday")
print(f"rows: {len(rows_stop):,}  stop-locations: {len(agg):,}  weekday boardings (all route-dirs): {tot_b:,}")
if calibrated:
    print(f"NOTE: all stop figures are MOCK (is_mock=1). {len(calibrated)} routes use real annual totals; stop/hour split is synthetic.")
else:
    print(f"NOTE: all figures are MOCK (is_mock=1). Weekday total calibrated to {args.weekday_total:,.0f} (route split is random).")
