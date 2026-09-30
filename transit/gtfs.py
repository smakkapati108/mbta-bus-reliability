"""Download the static GTFS schedule and load it into DuckDB, versioned by feed_version.

MBTA republishes GTFS every few days and trip_ids from an older rating can still be
running, so every feed version is kept. Staging models pick the newest version that
contains a given trip.
"""
from __future__ import annotations

import csv
import io
import logging
import tempfile
import zipfile
from pathlib import Path

import requests

from . import config
from .db import connect

log = logging.getLogger("gtfs")

TABLES = ("routes", "trips", "stop_times", "stops", "calendar", "calendar_dates", "checkpoints")


def download(url: str = config.GTFS_URL) -> Path:
    config.GTFS_DIR.mkdir(parents=True, exist_ok=True)
    resp = requests.get(url, timeout=300)
    resp.raise_for_status()
    info = _feed_info(resp.content)
    safe = "".join(c if c.isalnum() else "_" for c in info["feed_version"])[:80]
    path = config.GTFS_DIR / f"{info['feed_start_date']}_{safe}.zip"
    path.write_bytes(resp.content)
    return path


def _feed_info(raw: bytes) -> dict:
    with zipfile.ZipFile(io.BytesIO(raw)) as zf:
        return next(csv.DictReader(io.TextIOWrapper(zf.open("feed_info.txt"), "utf-8-sig")))


def load(zip_path: Path, route_types: str = config.ROUTE_TYPES) -> bool:
    """Load one GTFS zip. Returns False if this feed_version is already loaded."""
    feed_version = _feed_info(zip_path.read_bytes())["feed_version"]
    types = [t.strip() for t in route_types.split(",")]
    con = connect()
    con.execute("CREATE SCHEMA IF NOT EXISTS gtfs")
    con.execute(
        "CREATE TABLE IF NOT EXISTS gtfs.feeds (feed_version VARCHAR PRIMARY KEY, loaded_at TIMESTAMP)"
    )
    if con.execute("SELECT 1 FROM gtfs.feeds WHERE feed_version = ?", [feed_version]).fetchone():
        log.info("feed %s already loaded", feed_version)
        return False

    with tempfile.TemporaryDirectory() as tmp, zipfile.ZipFile(zip_path) as zf:
        for t in TABLES:
            zf.extract(f"{t}.txt", tmp)
        con.execute("BEGIN")
        for t in TABLES:
            src = (f"read_csv('{Path(tmp) / (t + '.txt')}', all_varchar = true, header = true, "
                   """delim = ',', quote = '"', escape = '"')""")
            if t == "routes":
                src = f"(SELECT * FROM {src} WHERE route_type IN ({','.join('?' * len(types))}))"
            elif t in ("trips", "stop_times"):
                # Keep only trips on the route types we collect; stop_times is ~150 MB otherwise.
                src = f"""(SELECT s.* FROM {src} s WHERE {'route_id' if t == 'trips' else 'trip_id'} IN (
                    SELECT {'route_id' if t == 'trips' else 'trip_id'} FROM _keep_{'routes' if t == 'trips' else 'trips'}))"""
            query = f"SELECT ? AS feed_version, * FROM {src}"
            params = [feed_version] + (types if t == "routes" else [])
            _append(con, f"gtfs.{t}", query, params)
            if t == "routes":
                con.execute("CREATE OR REPLACE TEMP TABLE _keep_routes AS SELECT route_id FROM gtfs.routes WHERE feed_version = ?", [feed_version])
            if t == "trips":
                con.execute("CREATE OR REPLACE TEMP TABLE _keep_trips AS SELECT trip_id FROM gtfs.trips WHERE feed_version = ?", [feed_version])
        con.execute("INSERT INTO gtfs.feeds VALUES (?, now())", [feed_version])
        con.execute("COMMIT")
    n = con.execute("SELECT count(*) FROM gtfs.stop_times WHERE feed_version = ?", [feed_version]).fetchone()[0]
    log.info("loaded feed %s (%d stop_times rows)", feed_version, n)
    con.close()
    return True


def _append(con, table: str, query: str, params: list) -> None:
    exists = con.execute(
        "SELECT 1 FROM information_schema.tables WHERE table_schema = 'gtfs' AND table_name = ?",
        [table.split(".")[1]],
    ).fetchone()
    if exists:
        # Columns can be added between feed versions, so insert by name.
        con.execute(f"INSERT INTO {table} BY NAME {query}", params)
    else:
        con.execute(f"CREATE TABLE {table} AS {query}", params)


def refresh() -> None:
    load(download())
