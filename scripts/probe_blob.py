"""Inspect Taipei City bus blob files (tcgbusfs) — prints top-level structure, record count, keys and one sample."""
import gzip, json, sys, pathlib
sys.stdout.reconfigure(encoding="utf-8")

for p in sorted(pathlib.Path(sys.argv[1]).glob("*.gz")):
    raw = gzip.decompress(p.read_bytes())
    for enc in ("utf-8-sig", "utf-8", "big5"):
        try:
            txt = raw.decode(enc); break
        except UnicodeDecodeError:
            continue
    try:
        data = json.loads(txt)
    except json.JSONDecodeError:
        print(f"== {p.name}  not JSON (e.g. XML feed), skipped")
        continue
    if isinstance(data, dict):
        top = {k: (type(v).__name__) for k, v in data.items()}
        recs = data.get("BusInfo") or next((v for v in data.values() if isinstance(v, list)), [])
    else:
        top, recs = "list", data
    print(f"== {p.name}  top={top}  n={len(recs)}")
    if recs:
        print("   keys:", list(recs[0].keys()))
        print("   sample:", json.dumps(recs[0], ensure_ascii=False)[:500])
    if isinstance(data, dict) and "EssentialInfo" in data:
        print("   essential:", json.dumps(data["EssentialInfo"], ensure_ascii=False)[:300])
