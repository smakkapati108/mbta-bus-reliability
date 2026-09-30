"""Shared loaders for the analysis scripts: they read the Parquet marts, never the live DB."""
from __future__ import annotations

import duckdb
import pandas as pd

from transit import config


def mart(name: str) -> str:
    return f"read_parquet('{config.MARTS_DIR / name}.parquet')"


def query(sql: str) -> pd.DataFrame:
    return duckdb.sql(sql).df()
