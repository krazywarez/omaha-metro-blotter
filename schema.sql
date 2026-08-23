-- Incidents from every ingested feed. occurred_at is local time (America/Chicago);
-- the upstream services return UTC epochs and ingest.py converts on the way in.
CREATE TABLE IF NOT EXISTS incidents (
    source       TEXT NOT NULL,  -- opd | sarpy | cbpd | opd_csv
    source_key   TEXT NOT NULL,  -- PK (opd) | IncidentId (sarpy) | cfs_number (cbpd)
    agency       TEXT NOT NULL,
    case_id      TEXT,
    occurred_at  TEXT NOT NULL,  -- ISO 8601, no offset
    category     TEXT,           -- each feed's own taxonomy, not comparable
    call_type    TEXT,           -- CadTypeDesc (sarpy) | incident_code (cbpd)
    disposition  TEXT,           -- CAD disposition, sarpy and cbpd
    offense_desc TEXT,           -- StatuteDesc (sarpy) | statute text (opd_csv)
    is_stop      INTEGER NOT NULL DEFAULT 0,  -- officer-initiated vehicle stop
    address      TEXT,
    lat          REAL,
    lon          REAL,
    PRIMARY KEY (source, source_key)
);

CREATE INDEX IF NOT EXISTS incidents_occurred ON incidents (occurred_at);
CREATE INDEX IF NOT EXISTS incidents_agency   ON incidents (agency, occurred_at);
CREATE INDEX IF NOT EXISTS incidents_category ON incidents (category);
CREATE INDEX IF NOT EXISTS incidents_stop     ON incidents (is_stop, occurred_at);

-- ALPR cameras from OpenStreetMap (ODbL). first_seen/last_seen track when a node
-- entered and was last present in the Overpass result, so cameras that appear or
-- are removed are visible over time.
CREATE TABLE IF NOT EXISTS alpr_cameras (
    osm_id       INTEGER PRIMARY KEY,
    lat          REAL NOT NULL,
    lon          REAL NOT NULL,
    manufacturer TEXT,
    operator     TEXT,
    direction    TEXT,
    first_seen   TEXT NOT NULL,
    last_seen    TEXT NOT NULL,
    tags         TEXT NOT NULL  -- full OSM tag dict as JSON
);
