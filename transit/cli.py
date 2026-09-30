"""Command-line entry point: python -m transit <command>."""
from __future__ import annotations

import argparse
import logging


def main() -> None:
    parser = argparse.ArgumentParser(prog="transit")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("collect", help="poll vehicle positions forever (or for --duration seconds)")
    p.add_argument("--duration", type=float)
    sub.add_parser("gtfs", help="download the static schedule and load it if it is a new version")
    sub.add_parser("ingest", help="load new collector output into DuckDB")
    sub.add_parser("transform", help="dbt build + export marts to Parquet")
    sub.add_parser("refresh", help="ingest + transform (what the scheduler runs)")
    p = sub.add_parser("compact", help="convert finished days of JSONL into Parquet")
    p.add_argument("--include-today", action="store_true")
    p.add_argument("--suffix", default="", help="filename suffix, e.g. a CI run id, to avoid collisions")
    sub.add_parser("analyze", help="statistical tests + findings report")
    sub.add_parser("train", help="train and evaluate the delay-prediction model")

    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")

    if args.cmd == "collect":
        from .collector import Collector
        Collector().run(duration_seconds=args.duration)
    elif args.cmd == "gtfs":
        from . import gtfs
        gtfs.refresh()
    elif args.cmd == "ingest":
        from . import ingest
        ingest.run()
    elif args.cmd == "transform":
        from . import transform
        transform.run()
    elif args.cmd == "refresh":
        from . import ingest, transform
        ingest.run()
        transform.run()
    elif args.cmd == "compact":
        from . import compact
        compact.run(include_today=args.include_today, suffix=args.suffix)
    elif args.cmd == "analyze":
        from analysis import stats
        stats.main()
    elif args.cmd == "train":
        from analysis import model
        model.main()
