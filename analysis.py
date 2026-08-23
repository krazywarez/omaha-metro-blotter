"""Queries behind the dashboard: agency activity and distance to the nearest ALPR.

Reads incidents_current, the newest known version of each record. The originals
stay in incidents and every superseded version in incident_amendments, so a
disposition an agency changed after the fact is still recoverable."""

import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd

DB = Path(__file__).parent / "raw_data" / "metro.db"

# Sarpy and Council Bluffs publish officer-initiated stops; ingest.py flags them
# as is_stop. OPD publishes NIBRS offences only and contributes no stops.
POLICE_AGENCIES = ("Omaha PD", "Council Bluffs PD", "Bellevue PD", "Papillion PD",
                   "La Vista PD", "Sarpy County SO")

EARTH_M = 6371000.0


def connect():
    return sqlite3.connect(DB)


def load_incidents(conn, agencies=None, start=None, end=None, categories=None):
    where, params = ["lat IS NOT NULL", "lon IS NOT NULL"], []
    if agencies:
        where.append(f"agency IN ({','.join('?' * len(agencies))})")
        params += list(agencies)
    if categories:
        where.append(f"category IN ({','.join('?' * len(categories))})")
        params += list(categories)
    if start:
        where.append("occurred_at >= ?")
        params.append(f"{start}T00:00:00")
    if end:
        where.append("occurred_at <= ?")
        params.append(f"{end}T23:59:59")
    df = pd.read_sql_query(
        f"""SELECT source, agency, case_id, occurred_at, category, call_type,
                   disposition, offense_desc, is_stop, address, lat, lon, amended
            FROM incidents_current WHERE {' AND '.join(where)}""",
        conn, params=params)
    df["occurred_at"] = pd.to_datetime(df["occurred_at"])
    return df


def load_cameras(conn):
    return pd.read_sql_query(
        "SELECT osm_id, lat, lon, manufacturer, operator, direction, first_seen"
        " FROM alpr_cameras", conn)


def nearest_camera_m(df, cameras):
    """Great-circle distance in metres from each incident to the closest camera.

    164 cameras against ~250k incidents, so the full distance matrix is computed
    in chunks rather than all at once."""
    if df.empty or cameras.empty:
        return np.full(len(df), np.nan)

    lat1 = np.radians(df["lat"].to_numpy(dtype=float))
    lon1 = np.radians(df["lon"].to_numpy(dtype=float))
    lat2 = np.radians(cameras["lat"].to_numpy(dtype=float))
    lon2 = np.radians(cameras["lon"].to_numpy(dtype=float))

    out = np.empty(len(df))
    cos2 = np.cos(lat2)
    for i in range(0, len(df), 20000):
        s = slice(i, i + 20000)
        dlat = lat2[None, :] - lat1[s, None]
        dlon = lon2[None, :] - lon1[s, None]
        a = (np.sin(dlat / 2) ** 2
             + np.cos(lat1[s, None]) * cos2[None, :] * np.sin(dlon / 2) ** 2)
        out[s] = (2 * EARTH_M * np.arcsin(np.sqrt(a))).min(axis=1)
    return out


def daily_counts(df):
    if df.empty:
        return pd.DataFrame(columns=["date", "agency", "incidents"])
    g = (df.assign(date=df["occurred_at"].dt.floor("D"))
           .groupby(["date", "agency"], as_index=False)
           .size().rename(columns={"size": "incidents"}))
    return g


def stop_outcomes(df):
    """Citation and arrest rate on vehicle stops, per agency.

    The two CAD systems use different disposition vocabularies -- Sarpy writes
    WRITTEN WARNING / CITATION, Council Bluffs writes "3 - Citation" and folds
    warnings into "7 - Handled by Officer" -- and both allow several outcomes per
    stop. Substring matching on citation and arrest is the only comparison the
    two vocabularies actually support, and a department that records warnings
    less thoroughly will show a higher citation rate for that reason alone."""
    stops = df[(df["is_stop"] == 1) & df["disposition"].notna()]
    if stops.empty:
        return pd.DataFrame(columns=["agency", "outcome", "rate", "stops"])
    d = stops["disposition"].str.upper()
    stops = stops.assign(cited=d.str.contains("CITATION"),
                         arrested=d.str.contains("ARREST"))
    g = stops.groupby("agency").agg(stops=("cited", "size"),
                                    Cited=("cited", "mean"),
                                    Arrested=("arrested", "mean")).reset_index()
    return (g.melt(id_vars=["agency", "stops"], value_vars=["Cited", "Arrested"],
                   var_name="outcome", value_name="rate")
             .sort_values("rate", ascending=False))


def camera_proximity(df, cameras, bin_m=200, max_m=2000):
    """Share of stops vs other incidents falling in each distance band.

    Both series are normalised, so a gap between them means stops cluster
    differently around cameras than the rest of the call volume does. It is not
    evidence of causation: cameras and stops both concentrate on arterials."""
    if df.empty or cameras.empty:
        return pd.DataFrame(columns=["distance_m", "kind", "share"])
    d = df.assign(dist=nearest_camera_m(df, cameras))
    d = d[d["dist"] <= max_m]
    if d.empty:
        return pd.DataFrame(columns=["distance_m", "kind", "share"])
    d["kind"] = np.where(d["is_stop"] == 1, "Vehicle stops", "All other incidents")
    d["distance_m"] = (d["dist"] // bin_m * bin_m).astype(int)
    g = (d.groupby(["kind", "distance_m"], as_index=False)
           .size().rename(columns={"size": "n"}))
    g["share"] = g["n"] / g.groupby("kind")["n"].transform("sum")
    return g


def amendment_history(conn, source, source_key):
    """Every version of one record, oldest first."""
    return pd.read_sql_query(
        """SELECT 'original' AS version, occurred_at, category, call_type,
                  disposition, is_stop, address, first_seen AS seen_at
             FROM incidents WHERE source = ? AND source_key = ?
           UNION ALL
           SELECT 'amended', occurred_at, category, call_type,
                  disposition, is_stop, address, seen_at
             FROM incident_amendments WHERE source = ? AND source_key = ?
           ORDER BY seen_at""",
        conn, params=[source, source_key, source, source_key])


def changed_stop_outcomes(conn):
    """Stops whose disposition the agency changed after first publishing it."""
    return pd.read_sql_query(
        """SELECT o.agency, o.case_id, o.occurred_at,
                  o.disposition AS first_published,
                  a.disposition AS later_published, a.seen_at
             FROM incident_amendments a
             JOIN incidents o
               ON o.source = a.source AND o.source_key = a.source_key
            WHERE o.is_stop = 1
              AND IFNULL(a.disposition, '') <> IFNULL(o.disposition, '')
            ORDER BY a.seen_at DESC""", conn)


def agency_options(conn):
    rows = conn.execute(
        "SELECT agency, COUNT(*) FROM incidents_current GROUP BY agency"
        " ORDER BY 2 DESC").fetchall()
    return [a for a, _ in rows]


def category_options(conn):
    rows = conn.execute(
        "SELECT category, COUNT(*) FROM incidents_current"
        " WHERE category IS NOT NULL GROUP BY category ORDER BY 2 DESC").fetchall()
    return [c for c, _ in rows]


def date_bounds(conn):
    lo, hi = conn.execute(
        "SELECT MIN(occurred_at), MAX(occurred_at) FROM incidents_current").fetchone()
    return lo[:10], hi[:10]
