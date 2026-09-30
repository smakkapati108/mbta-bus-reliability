"""Central configuration. Everything can be overridden with environment variables."""
from __future__ import annotations

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("TRANSIT_DATA_DIR", PROJECT_ROOT / "data"))

LANDING_DIR = DATA_DIR / "landing" / "vehicles"  # raw JSONL written by the collector
ARCHIVE_DIR = DATA_DIR / "archive" / "vehicles"  # daily compacted Parquet
GTFS_DIR = DATA_DIR / "gtfs"                      # downloaded static feed zips
MARTS_DIR = DATA_DIR / "marts"                    # Parquet exports for the dashboard
DB_PATH = Path(os.environ.get("TRANSIT_DB_PATH", DATA_DIR / "transit.duckdb"))

MBTA_API_BASE = "https://api-v3.mbta.com"
MBTA_API_KEY = os.environ.get("MBTA_API_KEY")  # optional; raises rate limit from 20 to 1000 req/min
GTFS_URL = os.environ.get("GTFS_URL", "https://cdn.mbta.com/MBTA_GTFS.zip")

# GTFS route_type values to collect. 3 = bus. Add 0,1 for light rail / subway.
ROUTE_TYPES = os.environ.get("TRANSIT_ROUTE_TYPES", "3")
POLL_SECONDS = int(os.environ.get("TRANSIT_POLL_SECONDS", "60"))

LOCAL_TZ = "America/New_York"
