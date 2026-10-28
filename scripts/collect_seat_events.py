"""Poll BusSeatEvent and append unique vehicle × stop × CarOnStop × DataTime events.

Usage: uv run scripts/collect_seat_events.py raw_data/research/seat_events --interval 20 --duration 1200
Output: OUT_DIR/seat_events_YYYYMMDD.csv; raw snapshots, not a complete trip history.
"""
import argparse
import csv
import gzip
import json
from pathlib import Path
import sys
import time
from urllib.request import urlopen
import zlib

URL = "https://tcgbusfs.blob.core.windows.net/blobbus/BusSeatEvent.gz"
FIELDS = ["DataTime", "BusID", "ProviderID", "StationID", "RouteID", "GoBack", "StopID", "CarOnStop",
          "RemainingNum", "Level", "DutyStatus", "BusStatus", "CarType"]
KEY_FIELDS = ("BusID", "StopID", "CarOnStop", "DataTime")


def append_events(path, records, seen):
    """Only mark an event as seen after its CSV batch has been written successfully."""
    pending = {}
    for record in records:
        key = tuple(record[field] for field in KEY_FIELDS)
        if key not in seen:
            pending.setdefault(key, record)
    if not pending:
        return 0
    with path.open("a+", encoding="utf-8", newline="") as stream:
        stream.seek(0, 2)
        offset = stream.tell()
        try:
            writer = csv.DictWriter(stream, fieldnames=FIELDS, extrasaction="ignore")
            if offset == 0:
                writer.writeheader()
            writer.writerows(pending.values())
            stream.flush()
        except OSError:
            # A failed batch must not leave half-written rows before the next poll retries it.
            stream.seek(offset)
            stream.truncate()
            raise
    seen.update(pending)
    return len(pending)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("out_dir", type=Path)
    parser.add_argument("--interval", type=int, default=30)
    parser.add_argument("--duration", type=int, default=1200, help="seconds; 0 = run forever")
    args = parser.parse_args(argv)
    if args.interval <= 0 or args.duration < 0:
        parser.error("interval must be positive and duration must be nonnegative")
    args.out_dir.mkdir(parents=True, exist_ok=True)
    seen, polls = set(), 0
    started = time.monotonic()
    while True:
        try:
            with urlopen(URL, timeout=30) as response:
                raw = response.read()
            records = json.loads(gzip.decompress(raw).decode("utf-8-sig"))["BusInfo"]
            path = args.out_dir / f"seat_events_{time.strftime('%Y%m%d')}.csv"
            added = append_events(path, records, seen)
            polls += 1
            print(f"{time.strftime('%H:%M:%S')} poll={polls} feed={len(records)} new={added} total={len(seen)}", flush=True)
        except (OSError, EOFError, zlib.error, ValueError, KeyError, TypeError) as error:
            print(f"{time.strftime('%H:%M:%S')} error {error}", file=sys.stderr, flush=True)
        remaining = args.duration - (time.monotonic() - started) if args.duration else None
        if remaining is not None and remaining <= 0:
            break
        time.sleep(min(args.interval, remaining) if remaining is not None else args.interval)


if __name__ == "__main__":
    main()
