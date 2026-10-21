"""Summarise curated JSON/GeoJSON datasets (schema_version/metadata/records convention): id, size, period, granularity, fields, limitations."""
import json, sys, pathlib
sys.stdout.reconfigure(encoding="utf-8")
root = pathlib.Path(sys.argv[1])
for p in sorted(root.rglob("*.json")) + sorted(root.rglob("*.geojson")):
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"== {p.relative_to(root)}  PARSE FAIL {e}"); continue
    if not isinstance(d, dict):
        print(f"== {p.relative_to(root)} list n={len(d)}"); continue
    recs = d.get("records") or d.get("features") or []
    md = d.get("metadata", {})
    first = recs[0] if recs else {}
    if "properties" in first: first = first["properties"]
    keep = {k: md[k] for k in md if any(t in k for t in ("title", "name", "period", "granularity", "unit", "limitation", "note", "source_time", "coverage", "scope"))}
    print(f"== {p.relative_to(root)}  [{p.stat().st_size//1024} KB]  id={d.get('dataset_id')}  n={len(recs) if isinstance(recs, list) else type(recs).__name__}")
    print("   meta:", json.dumps(keep, ensure_ascii=False)[:600])
    print("   fields:", list(first.keys())[:30] if isinstance(first, dict) else first)
