"""Load collector output into DuckDB (raw.vehicle_pings) incrementally and idempotently.

Sources are hourly JSONL files in data/landing and compacted Parquet in data/archive.
A file is re-read whenever its size changes (the current hour is still being appended),
and rows are de-duplicated on (vehicle_id, updated_at): consecutive polls of a bus that
has not reported a new position return the same record.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path

from . import config
from .db import connect

log = logging.getLogger("ingest")

COLUMNS = """
    vehicle_id VARCHAR, polled_at TIMESTAMP, updated_at TIMESTAMP, label VARCHAR,
    latitude DOUBLE, longitude DOUBLE, bearing INTEGER, speed DOUBLE, current_status VARCHAR,
    current_stop_sequence INTEGER, direction_id INTEGER, occupancy_status VARCHAR, revenue VARCHAR,
    route_id VARCHAR, trip_id VARCHAR, stop_id VARCHAR
"""

# Normalise either source format to the raw table's columns; timestamps become naive UTC.
SELECT_NORMALISED = """
    SELECT vehicle_id,
           CAST(polled_at AS TIMESTAMPTZ) AT TIME ZONE 'UTC' AS polled_at,
           CAST(updated_at AS TIMESTAMPTZ) AT TIME ZONE 'UTC' AS updated_at,
           label, latitude, longitude, bearing, speed, current_status,
           current_stop_sequence, direction_id, occupancy_status, revenue,
           route_id, trip_id, stop_id
    FROM {src}
"""

JSON_COLUMNS = """{vehicle_id: 'VARCHAR', polled_at: 'VARCHAR', label: 'VARCHAR', latitude: 'DOUBLE',
    longitude: 'DOUBLE', bearing: 'INTEGER', speed: 'DOUBLE', current_status: 'VARCHAR',
    current_stop_sequence: 'INTEGER', direction_id: 'INTEGER', updated_at: 'VARCHAR',
    occupancy_status: 'VARCHAR', revenue: 'VARCHAR', route_id: 'VARCHAR', trip_id: 'VARCHAR',
    stop_id: 'VARCHAR'}"""


def _source(path: Path) -> str:
    if path.suffix == ".parquet":
        return f"read_parquet('{path}')"
    # ignore_errors skips a torn final line if the collector is mid-write.
    return f"read_json('{path}', format = 'newline_delimited', columns = {JSON_COLUMNS}, ignore_errors = true)"


def run() -> int:
    con = connect()
    con.execute("CREATE SCHEMA IF NOT EXISTS raw")
    con.execute(f"CREATE TABLE IF NOT EXISTS raw.vehicle_pings ({COLUMNS}, PRIMARY KEY (vehicle_id, updated_at))")
    con.execute("CREATE TABLE IF NOT EXISTS raw.ingested_files (path VARCHAR PRIMARY KEY, bytes BIGINT, ingested_at TIMESTAMP)")
    seen = dict(con.execute("SELECT path, bytes FROM raw.ingested_files").fetchall())

    files = sorted(config.LANDING_DIR.glob("dt=*/*.jsonl")) + sorted(config.ARCHIVE_DIR.glob("dt=*/*.parquet"))
    todo = [f for f in files if seen.get(str(f)) != f.stat().st_size]
    before = con.execute("SELECT count(*) FROM raw.vehicle_pings").fetchone()[0]
    for f in todo:
        size = f.stat().st_size
        con.execute("BEGIN")
        con.execute(f"""
            INSERT INTO raw.vehicle_pings
            SELECT * FROM ({SELECT_NORMALISED.format(src=_source(f))})
            WHERE vehicle_id IS NOT NULL AND updated_at IS NOT NULL
            -- first poll wins when the same (vehicle, updated_at) appears more than once
            QUALIFY row_number() OVER (PARTITION BY vehicle_id, updated_at ORDER BY polled_at) = 1
            ON CONFLICT DO NOTHING
        """)
        con.execute("INSERT OR REPLACE INTO raw.ingested_files VALUES (?, ?, ?)",
                    [str(f), size, datetime.now(timezone.utc).replace(tzinfo=None)])
        con.execute("COMMIT")
    after = con.execute("SELECT count(*) FROM raw.vehicle_pings").fetchone()[0]
    log.info("scanned %d changed files, inserted %d new pings (total %d)", len(todo), after - before, after)
    con.close()
    return after - before
