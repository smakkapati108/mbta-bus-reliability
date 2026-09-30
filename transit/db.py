from __future__ import annotations

import duckdb

from . import config


def connect(read_only: bool = False) -> duckdb.DuckDBPyConnection:
    config.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(config.DB_PATH), read_only=read_only)
    # Store and compare everything in UTC; convert to local time only for reporting dimensions.
    con.execute("SET TimeZone = 'UTC'")
    return con
