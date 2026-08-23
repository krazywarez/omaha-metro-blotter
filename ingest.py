"""Pull incident feeds and ALPR camera locations into raw_data/metro.db.

Sources:
  opd     Omaha Police incident data (DCGIS ArcGIS view), 2022-01-01 onward, daily.
  sarpy   Sarpy County PublicCrimeMap CAD calls, rolling 12-month window. Covers
          Bellevue, Papillion, La Vista, Gretna, Springfield and the Sheriff.
          Records age out of the feed, so run this often enough to keep the archive.
  cbpd    Council Bluffs PD public CFS feed, rolling 12-month window, refreshed
          every 10 minutes. Same ageing-out caveat as sarpy.
  alpr    ALPR cameras from OpenStreetMap via Overpass (the data behind DeFlock).
  opd_csv One-time backfill of raw_data/Incidents_*.csv (2015-2023). Statute text
          only, no NIBRS category.

All three ArcGIS services return UTC epochs. Their WHERE literals do not agree:
OPD and Council Bluffs use UTC, Sarpy uses America/Chicago, so each source
carries its own literal timezone and page size.
"""

import argparse
import csv
import json
import sqlite3
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

import certifi
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

# Python does not use the macOS keychain, and Council Bluffs' host is not in the
# bundled trust store on every platform.
SSL_CTX = ssl.create_default_context(cafile=certifi.where())

ROOT = Path(__file__).parent
DB = ROOT / "raw_data" / "metro.db"
LOCAL = ZoneInfo("America/Chicago")

OPD = {
    "url": "https://services1.arcgis.com/tIBLyYZX96jUntYm/arcgis/rest/services"
           "/Omaha_Police_Incident_Data_(View)/FeatureServer/0",
    "date_field": "dteMidpoint",
    "literal_tz": timezone.utc,
    "oid": "OBJECTID",
    "page": 2000,
}

SARPY = {
    "url": "https://geodata.sarpy.gov/arcgis/rest/services/PublicSafety"
           "/PublicCrimeMap/FeatureServer/1",
    "date_field": "IncidentDate",
    "literal_tz": LOCAL,
    "oid": "ObjectID",
    "page": 2000,
}

CBPD = {
    "url": "https://gispublic.councilbluffs-ia.gov/publicserver/rest/services"
           "/Hosted/Public_Facing_CFS_view/FeatureServer/0",
    "date_field": "cfs_datetime",
    "literal_tz": timezone.utc,
    "oid": "objectid",
    "page": 1000,
}

# Council Bluffs files officer-initiated stops under one incident_code.
CBPD_STOP_CODE = "TRAFFIC : TRAFFIC STOP"

# Sarpy IncidentId prefixes. Fire/EMS agencies share the feed with the police
# agencies; they are kept so the archive stays complete and filtered in the app.
SARPY_AGENCIES = {
    "LBP": "Bellevue PD",
    "LPP": "Papillion PD",
    "LLP": "La Vista PD",
    "LSO": "Sarpy County SO",
    "LGP": "Gretna PD",
    "LSP": "Springfield PD",
    "BVF": "Bellevue Fire",
    "PAF": "Papillion Fire",
    "GRF": "Gretna Fire",
    "SPF": "Springfield Fire",
    "LVF": "La Vista Fire",
}

# The feed's other vehicle category, "Traffic", is crashes, parking and DUI
# calls -- reactive, not officer-initiated.
SARPY_STOP_CATEGORY = "Proactive Policing - Vehicle Stop"

OVERPASS = "https://overpass-api.de/api/interpreter"
# Douglas and Sarpy counties in Nebraska plus Council Bluffs across the river.
BBOX = (40.95, -96.35, 41.45, -95.65)
OVERPASS_QUERY = f"""
[out:json][timeout:120];
(
  node["man_made"="surveillance"]["surveillance:type"="ALPR"]{BBOX};
  node["man_made"="surveillance"]["surveillance:zone"="traffic"]["brand"~"Flock",i]{BBOX};
);
out body;
"""


def get(url, params, retries=4):
    body = urllib.parse.urlencode(params).encode()
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, data=body,
                                         headers={"User-Agent": "omaha-incidents/1.0"})
            with urllib.request.urlopen(req, timeout=120, context=SSL_CTX) as r:
                payload = json.load(r)
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
            if attempt == retries - 1:
                raise
            time.sleep(2 ** attempt)
            continue
        if "error" in payload:
            raise RuntimeError(f"{url}: {payload['error']}")
        return payload
    raise AssertionError("unreachable")


def query_all(service, since):
    """Page through an ArcGIS layer, yielding feature attribute dicts."""
    where = "1=1"
    if since is not None:
        stamp = since.astimezone(service["literal_tz"]).strftime("%Y-%m-%d %H:%M:%S")
        where = f"{service['date_field']} >= TIMESTAMP '{stamp}'"
    offset = 0
    while True:
        page = get(service["url"] + "/query", {
            "where": where,
            "outFields": "*",
            "returnGeometry": "true",
            "outSR": "4326",
            "orderByFields": f"{service['oid']} ASC",
            "resultOffset": offset,
            "resultRecordCount": service["page"],
            "f": "json",
        })
        feats = page.get("features", [])
        if not feats:
            return
        for f in feats:
            yield f
        offset += len(feats)
        print(f"    {offset} rows", end="\r", file=sys.stderr, flush=True)
        if not page.get("exceededTransferLimit") and len(feats) < service["page"]:
            return


def local_iso(epoch_ms):
    if epoch_ms is None:
        return None
    dt = datetime.fromtimestamp(epoch_ms / 1000, timezone.utc)
    return dt.astimezone(LOCAL).strftime("%Y-%m-%dT%H:%M:%S")


def upsert(conn, rows):
    conn.executemany(
        """INSERT INTO incidents
           (source, source_key, agency, case_id, occurred_at, category,
            call_type, disposition, offense_desc, is_stop, address, lat, lon)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(source, source_key) DO UPDATE SET
             agency=excluded.agency, case_id=excluded.case_id,
             occurred_at=excluded.occurred_at, category=excluded.category,
             call_type=excluded.call_type, disposition=excluded.disposition,
             offense_desc=excluded.offense_desc, is_stop=excluded.is_stop,
             address=excluded.address, lat=excluded.lat, lon=excluded.lon""",
        rows)
    return len(rows)


def ingest_opd(conn, since):
    rows = []
    for f in query_all(OPD, since):
        a = f["attributes"]
        occurred = local_iso(a["dteMidpoint"])
        if occurred is None:
            continue
        rows.append(("opd", str(a["PK"]), "Omaha PD", a.get("RB"), occurred,
                     a.get("NIBRSCategory"), None, None, None, 0,
                     a.get("AddressBlock"), a.get("LatBlock"), a.get("LonBlock")))
    return upsert(conn, rows)


def ingest_sarpy(conn, since):
    rows, unmapped = [], set()
    for f in query_all(SARPY, since):
        a = f["attributes"]
        occurred = local_iso(a["IncidentDate"])
        if occurred is None:
            continue
        iid = a["IncidentId"]
        prefix = iid[:3]
        agency = SARPY_AGENCIES.get(prefix)
        if agency is None:
            unmapped.add(prefix)
            agency = f"Unmapped {prefix}"
        g = f.get("geometry") or {}
        rows.append(("sarpy", iid, agency, iid, occurred, a.get("Category"),
                     a.get("CadTypeDesc"), a.get("CadDisposition"),
                     a.get("StatuteDesc"),
                     int(a.get("Category") == SARPY_STOP_CATEGORY),
                     a.get("BlkAddress"), g.get("y"), g.get("x")))
    n = upsert(conn, rows)
    if unmapped:
        print(f"  unmapped IncidentId prefixes: {sorted(unmapped)}")
    return n


def ingest_cbpd(conn, since):
    rows = []
    for f in query_all(CBPD, since):
        a = f["attributes"]
        occurred = local_iso(a["cfs_datetime"])
        if occurred is None:
            continue
        g = f.get("geometry") or {}
        code = a.get("incident_code")
        # Council Bluffs withholds the street address; the point is still exact.
        rows.append(("cbpd", a["cfs_number"], "Council Bluffs PD",
                     a.get("case_number") or a.get("cfs_number"), occurred,
                     a.get("incident_category"), code, a.get("disp_code"), None,
                     int(code == CBPD_STOP_CODE),
                     None, g.get("y"), g.get("x")))
    return upsert(conn, rows)


def ingest_opd_csv(conn, _since):
    """Backfill the 2015-2023 CSV archive. Statute text goes to offense_desc;
    category stays NULL because it is not a NIBRS category."""
    rows = []
    for path in sorted((ROOT / "raw_data").glob("Incidents_*.csv")):
        with path.open(newline="") as fh:
            if fh.readline().startswith("version https://git-lfs"):
                print(f"  {path.name}: git-lfs pointer, run 'git lfs pull'")
                continue
            fh.seek(0)
            for i, r in enumerate(csv.reader(fh)):
                if len(r) < 8 or r[0] == "RB Number":
                    continue
                rb, date, tm, desc, loc, district, lat, lon = r[:8]
                try:
                    when = datetime.strptime(f"{date} {tm}", "%m/%d/%Y %H:%M")
                except ValueError:
                    continue
                rows.append(("opd_csv", f"{path.stem}:{i}", "Omaha PD", rb,
                             when.strftime("%Y-%m-%dT%H:%M:%S"), None, None, None,
                             desc, 0, loc,
                             float(lat) if lat else None,
                             float(lon) if lon else None))
    return upsert(conn, rows)


def ingest_alpr(conn, _since):
    payload = get(OVERPASS, {"data": OVERPASS_QUERY})
    now = datetime.now(LOCAL).strftime("%Y-%m-%dT%H:%M:%S")
    rows = []
    for e in payload["elements"]:
        t = e.get("tags", {})
        rows.append((e["id"], e["lat"], e["lon"],
                     t.get("manufacturer") or t.get("brand"),
                     t.get("operator"),
                     t.get("direction") or t.get("camera:direction"),
                     now, now, json.dumps(t, sort_keys=True)))
    conn.executemany(
        """INSERT INTO alpr_cameras
           (osm_id, lat, lon, manufacturer, operator, direction,
            first_seen, last_seen, tags)
           VALUES (?,?,?,?,?,?,?,?,?)
           ON CONFLICT(osm_id) DO UPDATE SET
             lat=excluded.lat, lon=excluded.lon,
             manufacturer=excluded.manufacturer, operator=excluded.operator,
             direction=excluded.direction, last_seen=excluded.last_seen,
             tags=excluded.tags""",
        rows)
    return len(rows)


def migrate(conn):
    """Add columns introduced after a database was first built. Runs before
    schema.sql so its indexes can reference the new columns."""
    have = {r[1] for r in conn.execute("PRAGMA table_info(incidents)")}
    if have and "is_stop" not in have:
        conn.execute("ALTER TABLE incidents ADD COLUMN is_stop INTEGER NOT NULL"
                     " DEFAULT 0")
        conn.execute("UPDATE incidents SET is_stop = 1 WHERE category = ?",
                     (SARPY_STOP_CATEGORY,))
        conn.commit()


SOURCES = {
    "opd": ingest_opd,
    "sarpy": ingest_sarpy,
    "cbpd": ingest_cbpd,
    "alpr": ingest_alpr,
    "opd_csv": ingest_opd_csv,
}


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("sources", nargs="*", choices=list(SOURCES),
                   help="default: opd sarpy cbpd alpr")
    p.add_argument("--since-days", type=int, default=30,
                   help="only pull incidents this recent (default 30); "
                        "ignored by alpr and opd_csv")
    p.add_argument("--full", action="store_true",
                   help="pull the complete feed instead of --since-days")
    args = p.parse_args()
    sources = args.sources or ["opd", "sarpy", "cbpd", "alpr"]

    since = None if args.full else datetime.now(timezone.utc) - timedelta(days=args.since_days)

    DB.parent.mkdir(exist_ok=True)
    conn = sqlite3.connect(DB)
    migrate(conn)
    conn.executescript((ROOT / "schema.sql").read_text())
    for name in sources:
        start = time.monotonic()
        print(f"  {name}: pulling...")
        n = SOURCES[name](conn, since)
        conn.commit()
        print(f"  {name}: {n} rows in {time.monotonic() - start:.1f}s")
    conn.close()


if __name__ == "__main__":
    main()
