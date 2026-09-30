"""Run the dbt project, then export marts to Parquet for the dashboard.

The dashboard reads Parquet rather than the DuckDB file so it never contends for the
database lock with ingest/dbt runs happening on a schedule.
"""
from __future__ import annotations

import json
import logging
import os

from . import config
from .db import connect

log = logging.getLogger("transform")

DBT_DIR = config.PROJECT_ROOT / "dbt"
EXPORT_TABLES = (
    "fct_stop_events", "fct_trip_runtimes", "fct_headways",
    "agg_route_hourly", "agg_route_summary", "dim_routes",
)


def run_dbt(*args: str) -> None:
    from dbt.cli.main import dbtRunner

    os.environ.setdefault("TRANSIT_DB_PATH", str(config.DB_PATH))
    result = dbtRunner().invoke(
        [*args, "--project-dir", str(DBT_DIR), "--profiles-dir", str(DBT_DIR)]
    )
    if not result.success:
        raise SystemExit(f"dbt {' '.join(args)} failed")


def export_marts() -> None:
    config.MARTS_DIR.mkdir(parents=True, exist_ok=True)
    # Not read_only: dbt may still hold this file open in-process, and DuckDB refuses
    # a second handle to the same file with a different access mode.
    con = connect()
    for t in EXPORT_TABLES:
        tmp = config.MARTS_DIR / f".{t}.parquet"
        con.execute(f"COPY marts.{t} TO '{tmp}' (FORMAT parquet)")
        tmp.replace(config.MARTS_DIR / f"{t}.parquet")  # atomic swap so readers never see a partial file
    stats = con.execute("""
        select
            (select count(*) from raw.vehicle_pings) as raw_pings,
            (select sum(bytes) from raw.ingested_files) as raw_bytes_ingested,
            (select min(polled_at)::VARCHAR from raw.vehicle_pings) as first_poll_utc,
            (select max(polled_at)::VARCHAR from raw.vehicle_pings) as last_poll_utc,
            (select count(*) from staging.stg_vehicle_pings) as clean_pings,
            (select count(*) from marts.fct_stop_events) as stop_events,
            (select count(distinct instance_id) from marts.fct_stop_events) as trips,
            (select count(distinct route_id) from marts.fct_stop_events) as routes,
            (select count(*) from gtfs.feeds) as gtfs_versions
    """).df().iloc[0].to_dict()
    (config.MARTS_DIR / "pipeline_stats.json").write_text(json.dumps(stats, indent=2, default=str))
    con.close()
    log.info("exported %d marts to %s", len(EXPORT_TABLES), config.MARTS_DIR)


def run() -> None:
    run_dbt("build")
    export_marts()
