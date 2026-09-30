"""Poll MBTA vehicle positions and append them to hourly JSONL files.

The collector deliberately does *not* write to DuckDB: DuckDB allows a single
writer, so keeping the long-running process append-only on plain files means
ingest, dbt, and the dashboard never fight over a lock. Files are partitioned
by UTC date/hour: data/landing/vehicles/dt=2026-09-30/19.jsonl
"""
from __future__ import annotations

import json
import logging
import signal
import time
from datetime import datetime, timezone

import requests

from . import config

log = logging.getLogger("collector")

FIELDS = (
    "label", "latitude", "longitude", "bearing", "speed", "current_status",
    "current_stop_sequence", "direction_id", "updated_at", "occupancy_status", "revenue",
)


def flatten(vehicle: dict, polled_at: str) -> dict:
    attrs = vehicle.get("attributes", {})
    rels = vehicle.get("relationships", {})

    def rel_id(name: str):
        data = (rels.get(name) or {}).get("data")
        return data.get("id") if data else None

    row = {"vehicle_id": vehicle["id"], "polled_at": polled_at}
    row.update({f: attrs.get(f) for f in FIELDS})
    row.update(route_id=rel_id("route"), trip_id=rel_id("trip"), stop_id=rel_id("stop"))
    return row


class Collector:
    def __init__(self, route_types: str = config.ROUTE_TYPES, poll_seconds: int = config.POLL_SECONDS):
        self.route_types = route_types
        self.poll_seconds = poll_seconds
        self.session = requests.Session()
        self.session.headers["Accept-Encoding"] = "gzip"
        if config.MBTA_API_KEY:
            self.session.headers["x-api-key"] = config.MBTA_API_KEY
        self.last_modified: str | None = None
        self.stopping = False

    def fetch(self) -> list[dict] | None:
        """Return the vehicle list, or None if unchanged since the last poll (HTTP 304)."""
        headers = {"If-Modified-Since": self.last_modified} if self.last_modified else {}
        resp = self.session.get(
            f"{config.MBTA_API_BASE}/vehicles",
            params={"filter[route_type]": self.route_types},
            headers=headers,
            timeout=30,
        )
        if resp.status_code == 304:
            return None
        if resp.status_code == 429:
            reset = int(resp.headers.get("x-ratelimit-reset", time.time() + 60))
            raise RateLimited(max(reset - time.time(), 5))
        resp.raise_for_status()
        self.last_modified = resp.headers.get("Last-Modified")
        return resp.json()["data"]

    def write(self, vehicles: list[dict], now: datetime) -> int:
        polled_at = now.isoformat(timespec="seconds")
        out_dir = config.LANDING_DIR / f"dt={now:%Y-%m-%d}"
        out_dir.mkdir(parents=True, exist_ok=True)
        lines = [json.dumps(flatten(v, polled_at), separators=(",", ":")) for v in vehicles]
        with open(out_dir / f"{now:%H}.jsonl", "a") as fh:
            fh.write("\n".join(lines) + "\n")
        return len(lines)

    def poll_once(self) -> None:
        now = datetime.now(timezone.utc)
        vehicles = self.fetch()
        if vehicles is None:
            log.info("not modified")
            return
        n = self.write(vehicles, now)
        log.info("wrote %d vehicles", n)

    def run(self, duration_seconds: float | None = None) -> None:
        signal.signal(signal.SIGTERM, lambda *_: setattr(self, "stopping", True))
        deadline = time.monotonic() + duration_seconds if duration_seconds else None
        backoff = 5.0
        while not self.stopping and (deadline is None or time.monotonic() < deadline):
            started = time.monotonic()
            try:
                self.poll_once()
                backoff = 5.0
                sleep_for = self.poll_seconds - (time.monotonic() - started)
            except RateLimited as exc:
                log.warning("rate limited, sleeping %.0fs", exc.wait)
                sleep_for = exc.wait
            except (requests.RequestException, ValueError, KeyError) as exc:
                # Network blips are expected over weeks of collection: log, back off, keep going.
                log.warning("poll failed (%s), retrying in %.0fs", exc, backoff)
                sleep_for = backoff
                backoff = min(backoff * 2, 300)
            self._sleep(max(sleep_for, 0))

    def _sleep(self, seconds: float) -> None:
        end = time.monotonic() + seconds
        while not self.stopping and time.monotonic() < end:
            time.sleep(min(1.0, end - time.monotonic()))


class RateLimited(Exception):
    def __init__(self, wait: float):
        self.wait = wait
