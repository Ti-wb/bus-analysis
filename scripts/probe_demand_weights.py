#!/usr/bin/env python3
"""Feasibility probe for the transport-need index in docs/12 (not a dataset build).

Checks three things on the current repo data and prints a summary:
1. how many metro station names in data/metro/station_hourly_flows.json have an OSM
   railway_station point with the same normalised name (candidate join, unverified);
2. weekday AM/PM peak direction asymmetry per metro station;
3. a 250 m grid demand-supply gap: statistical-area population (area-weighted) against
   weekday 07–09 planned bus departures within 400 m of the cell centre.

Writes the per-cell and per-station values to raw_data/research/derived/ (gitignored).
"""

import argparse
import json
import math
import re
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

import numpy as np
import shapely
from pyproj import Geod, Transformer
from shapely import STRtree, make_valid
from shapely.geometry import box, shape

from build_web_data import GRID_LAT0, MAP, ROOT, read, subroute_departures

DATA = ROOT / "data"
OUT = ROOT / "raw_data/research/derived"
AM, PM = (7, 8), (17, 18)
STOP_RADIUS_M = 400


def norm_station(name):
    name = re.sub(r"^(BL|BR|G|O|R|Y)(?=[一-鿿])", "", name or "")
    return name.replace("臺", "台").removesuffix("站")


def rank01(values):
    """Percentile rank in [0, 1]; ties share the mean rank."""
    order = np.argsort(values, kind="mergesort")
    ranks = np.empty(len(values))
    ranks[order] = np.arange(len(values))
    for v in np.unique(values):
        idx = values == v
        ranks[idx] = ranks[idx].mean()
    return ranks / max(len(values) - 1, 1)


def spearman(a, b):
    return float(np.corrcoef(rank01(a), rank01(b))[0, 1])


def metro_probe():
    flows = read(DATA / "metro/station_hourly_flows.json")
    meta = flows["metadata"]
    osm = defaultdict(list)
    for feature in read(MAP / "osm/transport.geojson.gz")["features"]:
        props = feature["properties"]
        if props["feature_class"] == "railway_station" and props.get("name"):
            osm[norm_station(props["name"])].append(feature["geometry"]["coordinates"])
    names = {norm_station(n) for n in meta["entry_station_names"] + meta["exit_station_names"]}
    match = Counter("unmatched" if n not in osm else ("single" if len(osm[n]) == 1 else "multiple") for n in names)

    sums = defaultdict(lambda: np.zeros(4))  # am_in, am_out, pm_in, pm_out
    days = set()
    for service_date, hour, entries, exits in flows["records"]:
        if date.fromisoformat(service_date).isoweekday() > 5 or hour not in AM + PM:
            continue
        days.add(service_date)
        col = 0 if hour in AM else 2
        for names_key, values, offset in (("entry_station_names", entries, 0), ("exit_station_names", exits, 1)):
            for station, value in zip(meta[names_key], values):
                if value is not None:
                    sums[norm_station(station)][col + offset] += value
    # Some stations appear only in the entry dictionary; their asymmetry would be a dictionary artefact.
    both = {norm_station(n) for n in meta["entry_station_names"]} & {norm_station(n) for n in meta["exit_station_names"]}
    stations = []
    for name, (am_in, am_out, pm_in, pm_out) in sums.items():
        if name not in both or min(am_in + am_out, pm_in + pm_out) == 0:
            continue
        stations.append({
            "station_name": name,
            "am_net_attraction": (am_out - am_in) / (am_out + am_in),
            "pm_net_attraction": (pm_out - pm_in) / (pm_out + pm_in),
            "am_peak_daily": (am_in + am_out) / len(days),
        })
    am = np.array([s["am_net_attraction"] for s in stations])
    pm = np.array([s["pm_net_attraction"] for s in stations])
    stations.sort(key=lambda s: s["am_net_attraction"])
    return {
        "name_match": dict(match),
        "unmatched_names": sorted(n for n in names if n not in osm),
        "entry_only_names": sorted({norm_station(n) for n in meta["entry_station_names"]} - both),
        "weekday_days": len(days),
        "station_count": len(stations),
        "am_pm_asymmetry_pearson": float(np.corrcoef(am, pm)[0, 1]),
        "top_am_producers": [s["station_name"] for s in stations[:8]],
        "top_am_attractors": [s["station_name"] for s in stations[-8:][::-1]],
        "stations": stations,
    }


def grid_probe(cell_m):
    """Unscheduled subroutes count as zero here; the real build must flag them instead."""
    city = make_valid(shape(next(f for f in read(MAP / "admin/cities.geojson")["features"] if f["properties"]["admin_code"] == "63000")["geometry"]))
    geod = Geod(ellps="GRS80")
    lon_step = cell_m / geod.inv(121.5, GRID_LAT0, 121.51, GRID_LAT0)[2] * 0.01
    lat_step = cell_m / geod.inv(121.5, GRID_LAT0, 121.5, GRID_LAT0 + 0.01)[2] * 0.01
    minx, miny, _, _ = city.bounds
    origin = (math.floor(minx / lon_step) * lon_step, math.floor(miny / lat_step) * lat_step)

    # Statistical-area population, spread over cells by area share (both in degrees; same local scale).
    population = {r["base_area_id"]: r["population"] for r in read(DATA / "population/taipei_statistical_population_base.json")["records"]}
    cell_pop = defaultdict(float)
    for feature in read(DATA / "population/taipei_statistical_area_boundaries.geojson")["features"]:
        value = population.get(feature["properties"]["base_area_id"])
        if not value:
            continue
        geometry = make_valid(shape(feature["geometry"]))
        x0, y0, x1, y1 = geometry.bounds
        for ix in range(int((x0 - origin[0]) // lon_step), int((x1 - origin[0]) // lon_step) + 1):
            for iy in range(int((y0 - origin[1]) // lat_step), int((y1 - origin[1]) // lat_step) + 1):
                cx, cy = origin[0] + ix * lon_step, origin[1] + iy * lat_step
                share = geometry.intersection(box(cx, cy, cx + lon_step, cy + lat_step)).area / geometry.area
                if share > 0:
                    cell_pop[(ix, iy)] += value * share

    # Weekday 07–09 planned departures of distinct subroutes stopping within STOP_RADIUS_M.
    departures = subroute_departures()
    stop_subroutes = defaultdict(set)
    for record in read(DATA / "taipei_feeds/route_stop_sequences.json")["records"]:
        stop_subroutes[record["stop_location_id"]].add(record["subroute_id"])
    stops = [(f["properties"]["stop_location_id"], f["geometry"]["coordinates"]) for f in read(DATA / "taipei_feeds/stop_locations.geojson")["features"]]
    to_tm = Transformer.from_crs(4326, 3826, always_xy=True)
    tree = STRtree(shapely.points(*to_tm.transform(*np.array([c for _, c in stops]).T)))

    keys = sorted(cell_pop)
    centres = np.array([(origin[0] + (ix + 0.5) * lon_step, origin[1] + (iy + 0.5) * lat_step) for ix, iy in keys])
    inside = shapely.contains_xy(city, centres[:, 0], centres[:, 1])
    keys, centres = [k for k, ok in zip(keys, inside) if ok], centres[inside]
    points = shapely.points(*to_tm.transform(centres[:, 0], centres[:, 1]))
    cell_idx, stop_idx = tree.query(points, predicate="dwithin", distance=STOP_RADIUS_M)
    near = defaultdict(set)
    for c, s in zip(cell_idx, stop_idx):
        near[c] |= stop_subroutes[stops[s][0]]
    supply = np.array([sum(sum(departures.get((sub, "weekday"), [0.0] * 24)[h] for h in AM) for sub in near[i]) for i in range(len(keys))])
    scheduled = {sub for sub, _ in departures}
    partial = np.array([bool(near[i] - scheduled) for i in range(len(keys))])
    pop = np.array([cell_pop[k] for k in keys])
    density = pop / (cell_m * cell_m / 1e6)

    demand_score, supply_score = rank01(np.log1p(density)), rank01(np.log1p(supply))
    gap = demand_score - supply_score
    cells = [
        {"ix": ix, "iy": iy, "lon": round(float(x), 5), "lat": round(float(y), 5), "population": round(float(p), 1),
         "am_peak_departures_400m": round(float(s), 1), "demand_score": round(float(d), 3), "supply_score": round(float(q), 3), "gap": round(float(g), 3)}
        for (ix, iy), (x, y), p, s, d, q, g in zip(keys, centres, pop, supply, demand_score, supply_score, gap)
    ]
    populated = pop >= 100
    top = sorted((c for c, ok in zip(cells, populated) if ok), key=lambda c: -c["gap"])[:10]
    return {
        "cell_m": cell_m,
        "cells_with_population": len(keys),
        "population_sum": round(float(pop.sum())),
        "cells_without_stop_within_400m": int((supply == 0).sum()),
        "population_without_stop_within_400m": round(float(pop[supply == 0].sum())),
        "cells_with_unscheduled_subroutes": int(partial.sum()),
        "spearman_density_vs_supply": spearman(density, supply),
        "top_gap_cells_pop_ge_100": top,
        "cells": cells,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--cell-m", type=int, default=250)
    args = parser.parse_args()
    metro, grid = metro_probe(), grid_probe(args.cell_m)
    OUT.mkdir(parents=True, exist_ok=True)
    for name, doc in (("demand_probe_metro.json", metro), (f"demand_probe_grid_{args.cell_m}.json", grid)):
        (OUT / name).write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    summary = {k: v for k, v in metro.items() if k != "stations"} | {k: v for k, v in grid.items() if k != "cells"}
    print(json.dumps(summary, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
