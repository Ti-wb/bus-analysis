"""Poll the live BusSeatEvent feed (onboard count per vehicle at stop arrival/departure) and append de-duplicated events.

Usage: uv run --no-project python -I scripts/collect_seat_events.py raw_data/research/seat_events --interval 20 --duration 1200
Output: OUT_DIR/seat_events_YYYYMMDD.csv (one row per unique vehicle × stop × CarOnStop × DataTime)
"""
import argparse, csv, gzip, json, pathlib, sys, time, urllib.request

URL = "https://tcgbusfs.blob.core.windows.net/blobbus/BusSeatEvent.gz"
FIELDS = ["DataTime", "BusID", "ProviderID", "StationID", "RouteID", "GoBack", "StopID", "CarOnStop",
          "RemainingNum", "Level", "DutyStatus", "BusStatus", "CarType"]

ap = argparse.ArgumentParser()
ap.add_argument("out_dir")
ap.add_argument("--interval", type=int, default=30)
ap.add_argument("--duration", type=int, default=1200, help="seconds; 0 = run forever")
args = ap.parse_args()
out = pathlib.Path(args.out_dir); out.mkdir(parents=True, exist_ok=True)

seen, polls, t0 = set(), 0, time.time()
while True:
    try:
        raw = urllib.request.urlopen(URL, timeout=30).read()
        recs = json.loads(gzip.decompress(raw).decode("utf-8-sig"))["BusInfo"]
        path = out / f"seat_events_{time.strftime('%Y%m%d')}.csv"
        new = [r for r in recs if (k := (r["BusID"], r["StopID"], r["CarOnStop"], r["DataTime"])) not in seen and not seen.add(k)]
        exists = path.exists()
        with open(path, "a", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=FIELDS, extrasaction="ignore")
            if not exists:
                w.writeheader()
            w.writerows(new)
        polls += 1
        print(f"{time.strftime('%H:%M:%S')} poll={polls} feed={len(recs)} new={len(new)} total={len(seen)}", flush=True)
    except Exception as e:  # keep polling through transient errors
        print(f"{time.strftime('%H:%M:%S')} error {e}", file=sys.stderr, flush=True)
    if args.duration and time.time() - t0 >= args.duration:
        break
    time.sleep(args.interval)
