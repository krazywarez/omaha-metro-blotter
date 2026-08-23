"""Precompute the public site's figures into a self-contained site/index.html.

The dashboard in app.py needs a live Python process and refilters 300k rows on
every interaction. This does the aggregation once, at ingest time, and emits one
static file: no server, no dependencies, no per-visit cost."""

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

import analysis

ROOT = Path(__file__).parent
OUT = ROOT / "site"
# Grid cells for the map. ~0.004 deg is roughly 300m of latitude here, fine
# enough to show which corridors stops sit on without shipping 40k points.
CELL = 0.004


def summary(conn):
    rows = conn.execute(
        """SELECT agency, COUNT(*), SUM(is_stop),
                  MIN(occurred_at), MAX(occurred_at)
             FROM incidents_current GROUP BY agency ORDER BY COUNT(*) DESC"""
    ).fetchall()
    return [{"agency": a, "incidents": n, "stops": s or 0,
             "first": lo[:10], "last": hi[:10]} for a, n, s, lo, hi in rows]


def totals(conn):
    q = lambda sql: conn.execute(sql).fetchone()[0]
    return {
        "incidents": q("SELECT COUNT(*) FROM incidents_current"),
        "stops": q("SELECT COUNT(*) FROM incidents_current WHERE is_stop=1"),
        "cameras": q("SELECT COUNT(*) FROM alpr_cameras"),
        "searches": q("SELECT COUNT(*) FROM alpr_searches"),
        "amendments": q("SELECT COUNT(*) FROM incident_amendments"),
        "raw": q("SELECT COUNT(*) FROM raw_records"),
        "built": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
    }


def outcomes(conn):
    df = analysis.stop_outcomes(analysis.load_incidents(conn))
    if df.empty:
        return []
    wide = df.pivot(index="agency", columns="outcome", values="rate")
    counts = df.groupby("agency")["stops"].first()
    return [{"agency": a, "stops": int(counts[a]),
             "cited": round(float(wide.loc[a, "Cited"]), 4),
             "arrested": round(float(wide.loc[a, "Arrested"]), 4)}
            for a in wide.sort_values("Cited", ascending=False).index]


def proximity(conn):
    df = analysis.load_incidents(conn)
    cams = analysis.load_cameras(conn)
    prox = analysis.camera_proximity(df, cams)
    if prox.empty:
        return []
    wide = prox.pivot(index="distance_m", columns="kind", values="share")
    return [{"m": int(m),
             "stops": round(float(r.get("Vehicle stops", 0)), 5),
             "other": round(float(r.get("All other incidents", 0)), 5)}
            for m, r in wide.iterrows()]


def weekly(conn):
    df = analysis.load_incidents(conn)
    if df.empty:
        return {}
    w = (df.assign(week=df["occurred_at"].dt.to_period("W").dt.start_time)
           .groupby(["agency", "week"]).size().reset_index(name="n"))
    out = {}
    for agency, g in w.groupby("agency"):
        g = g.sort_values("week")
        # drop the trailing partial week so the last point is not a false dip
        g = g.iloc[:-1] if len(g) > 1 else g
        out[agency] = {"weeks": [d.strftime("%Y-%m-%d") for d in g["week"]],
                       "counts": [int(x) for x in g["n"]]}
    return out


def map_layers(conn):
    df = analysis.load_incidents(conn)
    stops = df[df["is_stop"] == 1].dropna(subset=["lat", "lon"])
    lat = (np.floor(stops["lat"] / CELL) * CELL).round(4)
    lon = (np.floor(stops["lon"] / CELL) * CELL).round(4)
    grid = (stops.assign(clat=lat, clon=lon)
                 .groupby(["clat", "clon"]).size().reset_index(name="n"))
    grid = grid[grid["n"] >= 2]          # single stops are noise at this zoom
    cams = analysis.load_cameras(conn)
    return {
        "cell": CELL,
        "cells": [[round(r.clat, 4), round(r.clon, 4), int(r.n)]
                  for r in grid.itertuples()],
        "cameras": [[round(r.lat, 5), round(r.lon, 5)] for r in cams.itertuples()],
    }


def searches(conn):
    audit = analysis.search_audit(conn)
    if audit.empty:
        return None
    row = audit.iloc[0]
    counts = [r[0] for r in conn.execute(
        "SELECT network_count FROM alpr_searches WHERE network_count IS NOT NULL")]
    reasons = conn.execute(
        """SELECT LOWER(reason), COUNT(*) FROM alpr_searches
            WHERE reason IS NOT NULL GROUP BY 1 ORDER BY 2 DESC LIMIT 8""").fetchall()
    edges = [0, 1, 10, 100, 500, 1000, 2500, 10000]
    hist = []
    for lo, hi in zip(edges, edges[1:]):
        hist.append({"lo": lo, "hi": hi,
                     "n": sum(1 for c in counts if lo < c <= hi)})
    return {
        "agency": row["agency"], "searches": int(row["searches"]),
        "with_reason": int(row["with_reason"]),
        "reason_rate": round(float(row["reason_rate"]), 4),
        "median_networks": int(np.median(counts)) if counts else 0,
        "max_networks": int(max(counts)) if counts else 0,
        "first": row["earliest"][:10], "last": row["latest"][:10],
        "histogram": hist,
        "reasons": [{"reason": r, "n": n} for r, n in reasons],
    }


def build():
    conn = sqlite3.connect(analysis.DB)
    data = {
        "totals": totals(conn),
        "agencies": summary(conn),
        "outcomes": outcomes(conn),
        "proximity": proximity(conn),
        "weekly": weekly(conn),
        "map": map_layers(conn),
        "searches": searches(conn),
    }
    conn.close()

    OUT.mkdir(exist_ok=True)
    template = (ROOT / "site_template.html").read_text()
    payload = json.dumps(data, separators=(",", ":"))
    (OUT / "index.html").write_text(template.replace("/*DATA*/null", payload))
    return data, len(payload)


if __name__ == "__main__":
    data, size = build()
    print(f"  site/index.html written, {size/1024:.0f} KiB of data")
    for k, v in data["totals"].items():
        print(f"    {k}: {v}")
