#!/usr/bin/env python3
"""Independent structural audit of generated regional frequency-grid deliveries.

Never replaces unknown schedules with zero; reads generator output and retained
publication envelopes, and checks outputs without recalculating timetable rows.
"""
from collections import Counter
import gzip
import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXPECTED = (100, 150, 250, 500)
FIELDS = ["ix", "iy", "stop_count", "subroute_count", "unscheduled_subroute_count", "weekday", "saturday", "sunday"]
DAY_FIELDS = ("weekday", "saturday", "sunday")
SCOPES = ("Taipei", "NewTaipei", "Keelung", "InterCity")


def load(path):
    with (gzip.open(path, "rt", encoding="utf-8") if path.suffix == ".gz"
          else path.open("r", encoding="utf-8")) as stream:
        return json.load(stream)


def audit(root=ROOT):
    results = []
    seen_sources = None
    for cell_m in EXPECTED:
        path = root / "web/public/data" / f"regional_bus_grid_{cell_m}.json"
        doc = load(path)
        if doc.get("cell_m") != cell_m or doc.get("fields") != FIELDS:
            raise ValueError(f"{path}: grid schema mismatch")
        if len(doc.get("origin", ())) != 2 or len(doc.get("step", ())) != 2:
            raise ValueError(f"{path}: grid geometry missing")
        if any(type(x) not in (float, int) or not math.isfinite(x) or x <= 0 for x in doc["step"]):
            raise ValueError(f"{path}: invalid grid steps")
        meta = doc["metadata"]
        if set(meta["scope_city_codes"]) != {"63000", "65000", "10017"}:
            raise ValueError(f"{path}: north-region scope drift")
        sources = meta.get("sources")
        if not isinstance(sources, list) or len(sources) != 8:
            raise ValueError(f"{path}: expected four authorities × two endpoints")
        observed_sources = set()
        for entry in sources:
            key = (entry["scope"], entry["service"])
            if key in observed_sources or entry["service"] not in {"StopOfRoute", "Schedule"}:
                raise ValueError(f"{path}: duplicate/unrecognized source {key}")
            observed_sources.add(key)
            raw = load(root / entry["path"])
            if raw["metadata"].get("scope_status") != "complete_published_endpoint_by_exhausted_page":
                raise ValueError(f"{path}: incomplete source {key}")
            if len(raw["records"]) != entry["records"]:
                raise ValueError(f"{path}: source record-count drift {key}")
        if {scope for scope in SCOPES for _ in range(2)} != {s[0] for s in observed_sources}:
            raise ValueError(f"{path}: missing scope")
        if seen_sources is None:
            seen_sources = observed_sources
        elif seen_sources != observed_sources:
            raise ValueError(f"{path}: inconsistent source coverage across grid sizes")
        seen_cells = set()
        classes = Counter()
        known_cells = 0
        uncertain_cells = 0
        for cell in doc["cells"]:
            if not isinstance(cell, list) or len(cell) != 8:
                raise ValueError(f"{path}: cell column contract changed")
            ix, iy, stops, routes, unknown = cell[:5]
            if any(type(x) is not int or x < 0 for x in (ix, iy, stops, routes, unknown)):
                raise ValueError(f"{path}: invalid cell count")
            if unknown > routes or (routes > 0 and stops == 0):
                raise ValueError(f"{path}: invalid route/stop accounting")
            if (ix, iy) in seen_cells:
                raise ValueError(f"{path}: duplicate grid location")
            seen_cells.add((ix, iy))
            valid = False
            for value in cell[5:8]:
                if value is None:
                    continue
                valid = True
                if (not isinstance(value, list) or len(value) != 24 or
                    any(type(n) not in (int, float) or not math.isfinite(n) or n < 0 for n in value)):
                    raise ValueError(f"{path}: invalid hourly series")
            if routes == 0:
                status = "no_stop"
                if any(x is not None for x in cell[5:8]):
                    raise ValueError(f"{path}: routes absent but hours populated")
            elif not valid:
                status = "no_schedule"
            elif unknown:
                status = "partial"
            else:
                status = "ok"
            classes[status] += 1
            known_cells += bool(valid)
            uncertain_cells += bool(unknown)
        if dict(classes) != {k: v for k, v in meta["status_counts"].items() if v}:
            raise ValueError(f"{path}: generator status counts disagree with row audit")
        if not seen_cells or known_cells == 0:
            raise ValueError(f"{path}: no published service available")
        # With a much smaller geographic sampling footprint, the fine grids must
        # not pretend to have complete administrative coverage.
        if cell_m in (100, 150) and "350" not in meta["cell"]:
            raise ValueError(f"{path}: fine-grid scope caveat missing")
        results.append({"cell_m": cell_m, "cells": len(seen_cells), "cells_with_published_hours": known_cells,
                        "uncertain_cells": uncertain_cells, "classes": dict(classes)})
    return results


if __name__ == "__main__":
    print(json.dumps({"audit": "passed", "grids": audit()}, ensure_ascii=False, indent=2))
