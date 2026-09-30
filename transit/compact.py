"""Compact finished days of landing JSONL into one Parquet file per day (~10x smaller).

Only days strictly before today (UTC) are touched, since the collector may still be
appending to today's files. Landing files are deleted only after the Parquet write
succeeds and row counts match.
"""
from __future__ import annotations

import logging
import shutil
from datetime import datetime, timezone

import duckdb

from . import config
from .ingest import JSON_COLUMNS

log = logging.getLogger("compact")


def run(include_today: bool = False, suffix: str = "") -> None:
    today = f"dt={datetime.now(timezone.utc):%Y-%m-%d}"
    con = duckdb.connect()
    for day_dir in sorted(config.LANDING_DIR.glob("dt=*")):
        if day_dir.name >= today and not include_today:
            continue
        files = sorted(day_dir.glob("*.jsonl"))
        if not files:
            continue
        out_dir = config.ARCHIVE_DIR / day_dir.name
        out_dir.mkdir(parents=True, exist_ok=True)
        out = out_dir / f"part{suffix}.parquet"
        if out.exists():
            raise SystemExit(f"{out} already exists; pass a different --suffix")
        src = f"read_json({[str(f) for f in files]}, format = 'newline_delimited', columns = {JSON_COLUMNS}, ignore_errors = true)"
        n_in = con.execute(f"SELECT count(*) FROM {src}").fetchone()[0]
        con.execute(f"COPY (SELECT * FROM {src}) TO '{out}' (FORMAT parquet, COMPRESSION zstd)")
        n_out = con.execute(f"SELECT count(*) FROM read_parquet('{out}')").fetchone()[0]
        if n_in != n_out:
            out.unlink()
            raise SystemExit(f"row count mismatch for {day_dir.name}: {n_in} vs {n_out}")
        shutil.rmtree(day_dir)
        log.info("compacted %s: %d rows -> %s", day_dir.name, n_out, out)
