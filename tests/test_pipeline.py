"""End-to-end test: synthetic GTFS + pings -> ingest -> dbt build -> check interpolated arrivals.

Uses 2026-03-08, the day US clocks spring forward, so a naive "midnight + seconds" schedule
conversion would be off by an hour, plus an after-midnight trip (GTFS time 24:30:00).
"""
from __future__ import annotations

import csv
import io
import json
import zipfile
from datetime import datetime, timedelta, timezone

import pytest

from transit import config, gtfs, ingest, transform
from transit.db import connect

BASE_LAT, LON, STEP = 42.30, -71.10, 0.0045  # stops ~500 m apart along a meridian


def stop_lat(i: int) -> float:
    return BASE_LAT + (i - 1) * STEP


def write_gtfs(path):
    def table(rows):
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerows(rows)
        return buf.getvalue()

    stop_times = [["trip_id", "arrival_time", "departure_time", "stop_id", "stop_sequence", "timepoint", "checkpoint_id"]]
    for trip, start_h, start_m in (("T1", 8, 0), ("T2", 24, 30)):
        for i in range(1, 6):
            m = start_m + 2 * (i - 1)
            t = f"{start_h:02d}:{m:02d}:00"
            stop_times.append([trip, t, t, f"S{i}", str(i * 10 - 9 if i > 1 else 1), "0", "cp" if i in (1, 3, 5) else ""])
    files = {
        "feed_info.txt": table([["feed_publisher_name", "feed_version", "feed_start_date"], ["test", "v1", "20260301"]]),
        "routes.txt": table([["route_id", "route_short_name", "route_long_name", "route_desc", "route_type", "route_sort_order"],
                             ["R", "R", "Test Route", "Local Bus", "3", "1"]]),
        "trips.txt": table([["route_id", "service_id", "trip_id", "trip_headsign", "direction_id", "route_pattern_id", "block_id"],
                            ["R", "svc", "T1", "North", "0", "R-0", "b"], ["R", "svc", "T2", "North", "0", "R-0", "b"]]),
        "stop_times.txt": table(stop_times),
        "stops.txt": table([["stop_id", "stop_name", "stop_lat", "stop_lon", "municipality"]]
                           + [[f"S{i}", f"Stop {i}", str(stop_lat(i)), str(LON), "Boston"] for i in range(1, 6)]),
        "calendar.txt": table([["service_id", "monday"], ["svc", "1"]]),
        "calendar_dates.txt": table([["service_id", "date", "exception_type"]]),
        "checkpoints.txt": table([["checkpoint_id", "checkpoint_name"], ["cp", "Checkpoint"]]),
    }
    with zipfile.ZipFile(path, "w") as zf:
        for name, content in files.items():
            zf.writestr(name, content)


def ping(trip, ts, status, seq, lat, vehicle="v1"):
    return {"vehicle_id": vehicle, "polled_at": ts.isoformat(), "updated_at": ts.isoformat(),
            "latitude": lat, "longitude": LON, "current_status": status, "current_stop_sequence": seq,
            "direction_id": 0, "revenue": "REVENUE", "route_id": "R", "trip_id": trip, "stop_id": None,
            "label": vehicle, "bearing": None, "speed": None, "occupancy_status": None}


SEQ = {1: 1, 2: 11, 3: 21, 4: 31, 5: 41}


def trip_pings(trip, t0):
    at = lambda s: t0 + timedelta(seconds=s)  # noqa: E731
    mid = lambda a, b, f: stop_lat(a) + (stop_lat(b) - stop_lat(a)) * f  # noqa: E731
    return [
        ping(trip, at(0), "STOPPED_AT", SEQ[1], stop_lat(1)),
        ping(trip, at(60), "STOPPED_AT", SEQ[1], stop_lat(1)),              # still at origin -> departs ~t0+60
        ping(trip, at(120), "IN_TRANSIT_TO", SEQ[3], mid(2, 3, 0.5)),       # progress 2.5
        ping(trip, at(180), "STOPPED_AT", SEQ[4], stop_lat(4)),             # progress 4
        ping(trip, at(210), "IN_TRANSIT_TO", SEQ[3], mid(2, 3, 0.9)),       # GPS glitch backwards: ignored
        ping(trip, at(240), "IN_TRANSIT_TO", SEQ[5], mid(4, 5, 0.75)),      # progress 4.75
        ping(trip, at(300), "STOPPED_AT", SEQ[5], stop_lat(5)),             # progress 5
    ]


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("pipeline")
    mp = pytest.MonkeyPatch()
    mp.setattr(config, "DB_PATH", tmp / "t.duckdb")
    mp.setattr(config, "LANDING_DIR", tmp / "landing")
    mp.setattr(config, "ARCHIVE_DIR", tmp / "archive")
    mp.setattr(config, "MARTS_DIR", tmp / "marts")
    mp.setenv("TRANSIT_DB_PATH", str(tmp / "t.duckdb"))

    write_gtfs(tmp / "feed.zip")
    assert gtfs.load(tmp / "feed.zip")
    assert not gtfs.load(tmp / "feed.zip")  # same feed_version is skipped

    # T1: 08:00 EDT on 2026-03-08 (DST day) == 12:00 UTC. T2: 24:30 on 2026-03-10 == 00:30 EDT 03-11 == 04:30 UTC.
    rows = trip_pings("T1", datetime(2026, 3, 8, 12, 0, tzinfo=timezone.utc))
    rows += trip_pings("T2", datetime(2026, 3, 11, 4, 30, tzinfo=timezone.utc))
    day = config.LANDING_DIR / "dt=2026-03-08"
    day.mkdir(parents=True)
    lines = [json.dumps(r) for r in rows]
    (day / "12.jsonl").write_text("\n".join(lines + lines[:3]) + "\n")  # includes duplicate polls

    assert ingest.run() == len(rows)
    assert ingest.run() == 0  # idempotent
    transform.run()  # dbt build (models + data tests) and Parquet export
    # dbt keeps the file open in-process, so reuse the same (read-write) configuration.
    yield connect()
    mp.undo()


def events(con, trip):
    return {
        r[0]: (r[1], r[2], r[3])
        for r in con.execute(
            """select stop_index, epoch(observed_utc)::BIGINT, epoch(scheduled_utc)::BIGINT, service_date
               from marts.fct_stop_events where trip_id = ? order by stop_index""", [trip]
        ).fetchall()
    }


def test_interpolated_arrivals_and_dst_schedule(built):
    t0 = int(datetime(2026, 3, 8, 12, 0, tzinfo=timezone.utc).timestamp())
    ev = events(built, "T1")
    assert sorted(ev) == [1, 2, 3, 4, 5]
    expected_observed = {1: 60, 2: 100, 3: 140, 4: 180, 5: 300}
    for k, offset in expected_observed.items():
        assert abs(ev[k][0] - (t0 + offset)) <= 1, f"stop {k}"
        assert ev[k][1] == t0 + 120 * (k - 1), f"stop {k} schedule (DST handling)"
    assert str(ev[1][2]) == "2026-03-08"


def test_after_midnight_trip_gets_previous_service_date(built):
    ev = events(built, "T2")
    assert str(ev[1][2]) == "2026-03-10"
    t0 = int(datetime(2026, 3, 11, 4, 30, tzinfo=timezone.utc).timestamp())
    assert ev[1][1] == t0  # 24:30 on 03-10 is 04:30 UTC on 03-11


def test_runtime_and_marts_exported(built):
    rt = built.execute("select observed_runtime_min, scheduled_runtime_min from marts.fct_trip_runtimes where trip_id = 'T1'").fetchone()
    assert rt == (4.0, 8.0)
    assert (config.MARTS_DIR / "fct_stop_events.parquet").exists()
