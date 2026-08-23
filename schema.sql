-- Incidents as first observed. Rows here are never updated: when a feed serves a
-- changed version of a record it goes to incident_amendments instead, so the
-- original survives a reclassification, a reopened case or a withdrawn record.
-- occurred_at is local time (America/Chicago); the services return UTC epochs
-- and ingest.py converts on the way in.
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
    digest       TEXT NOT NULL,  -- hash of the payload, for change detection
    first_seen   TEXT,           -- when ingest first saw it; NULL if pre-dating
    PRIMARY KEY (source, source_key)
);

CREATE INDEX IF NOT EXISTS incidents_occurred ON incidents (occurred_at);
CREATE INDEX IF NOT EXISTS incidents_agency   ON incidents (agency, occurred_at);
CREATE INDEX IF NOT EXISTS incidents_category ON incidents (category);
CREATE INDEX IF NOT EXISTS incidents_stop     ON incidents (is_stop, occurred_at);

-- Every distinct later version of a record, one row per version. Keyed on the
-- payload digest, so a version is stored once no matter how many runs serve it.
-- A record that reverts to a payload already on file is therefore not recorded
-- again: this holds the set of distinct states observed, not a strict timeline.
CREATE TABLE IF NOT EXISTS incident_amendments (
    source       TEXT NOT NULL,
    source_key   TEXT NOT NULL,
    agency       TEXT NOT NULL,
    case_id      TEXT,
    occurred_at  TEXT NOT NULL,
    category     TEXT,
    call_type    TEXT,
    disposition  TEXT,
    offense_desc TEXT,
    is_stop      INTEGER NOT NULL DEFAULT 0,
    address      TEXT,
    lat          REAL,
    lon          REAL,
    digest       TEXT NOT NULL,
    seen_at      TEXT NOT NULL,  -- when this version was first observed
    PRIMARY KEY (source, source_key, digest)
);

CREATE INDEX IF NOT EXISTS amendments_key  ON incident_amendments (source, source_key);
CREATE INDEX IF NOT EXISTS amendments_seen ON incident_amendments (seen_at);

-- The newest known version of each record: its latest amendment, or the
-- original where a record has never been amended.
CREATE VIEW IF NOT EXISTS incidents_current AS
SELECT source, source_key, agency, case_id, occurred_at, category, call_type,
       disposition, offense_desc, is_stop, address, lat, lon, observed_at,
       amended
FROM (
    SELECT *, ROW_NUMBER() OVER (PARTITION BY source, source_key
                                 ORDER BY amended DESC, observed_at DESC) AS rn
    FROM (
        SELECT source, source_key, agency, case_id, occurred_at, category,
               call_type, disposition, offense_desc, is_stop, address, lat, lon,
               first_seen AS observed_at, 0 AS amended
        FROM incidents
        UNION ALL
        SELECT source, source_key, agency, case_id, occurred_at, category,
               call_type, disposition, offense_desc, is_stop, address, lat, lon,
               seen_at AS observed_at, 1 AS amended
        FROM incident_amendments
    )
)
WHERE rn = 1;

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
