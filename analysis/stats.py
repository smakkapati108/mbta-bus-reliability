"""Statistical analysis of reliability; writes data/marts/findings.md.

Stop events on the same trip are strongly correlated (a bus late at stop 5 is late at
stop 6), so tests use one observation per *trip*, not per stop event. Treating every
stop as independent would inflate sample sizes and make almost anything "significant".
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from scipy import stats

from transit import config

from .data import mart, query

MIN_TRIPS = 30          # minimum trips before a route is ranked
N_BOOT = 2000
rng = np.random.default_rng(42)


def bootstrap_ci(x: np.ndarray, fn=np.median, n: int = N_BOOT, alpha: float = 0.05) -> tuple[float, float]:
    samples = [fn(rng.choice(x, size=len(x), replace=True)) for _ in range(n)]
    return float(np.quantile(samples, alpha / 2)), float(np.quantile(samples, 1 - alpha / 2))


def bootstrap_diff_ci(a: np.ndarray, b: np.ndarray, fn=np.median) -> tuple[float, float]:
    diffs = [fn(rng.choice(a, len(a))) - fn(rng.choice(b, len(b))) for _ in range(N_BOOT)]
    return float(np.quantile(diffs, 0.025)), float(np.quantile(diffs, 0.975))


def wilson_ci(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (np.nan, np.nan)
    p = k / n
    denom = 1 + z**2 / n
    centre = (p + z**2 / (2 * n)) / denom
    half = z * np.sqrt(p * (1 - p) / n + z**2 / (4 * n**2)) / denom
    return centre - half, centre + half


def benjamini_hochberg(p: pd.Series) -> pd.Series:
    order = p.sort_values().index
    m = len(p)
    ranked = p.loc[order] * m / np.arange(1, m + 1)
    adjusted = np.minimum.accumulate(ranked[::-1])[::-1].clip(upper=1)
    return adjusted.reindex(p.index)


def trip_level() -> pd.DataFrame:
    """One row per trip instance: share of timepoints late and mean timepoint delay."""
    return query(f"""
        select instance_id, route_id, service_date, day_type,
               case when day_type = 'Weekday' then 'Weekday' else 'Weekend' end as day_class,
               any_value(time_period) as time_period,
               avg(delay_sec) / 60.0 as mean_delay_min,
               avg(is_late::INTEGER) as share_late,
               count(*) as n_timepoints
        from {mart('fct_stop_events')}
        where is_timepoint
        group by all
    """)


def section_least_reliable(md: list[str]) -> pd.DataFrame:
    df = query(f"select * from {mart('agg_route_summary')} where n_trips >= {MIN_TRIPS}")
    md.append(f"## Least reliable routes (≥{MIN_TRIPS} observed trips)\n")
    if df.empty:
        md.append("_Not enough data yet._\n")
        return df
    df["late_events"] = (df.pct_late * df.n_timepoint_events).round().astype(int)
    ci = df.apply(lambda r: wilson_ci(r.late_events, r.n_timepoint_events), axis=1)
    df["late_ci"] = ci.map(lambda c: f"{c[0]:.0%}–{c[1]:.0%}")
    top = df.sort_values("pct_late", ascending=False).head(10)
    md.append("| Route | Trips | % late (>6 min) | 95% CI | Median delay | p90 delay | Weekday PM peak % late |")
    md.append("|---|---|---|---|---|---|---|")
    for r in top.itertuples():
        pm = "–" if pd.isna(r.pct_late_pm_peak) else f"{r.pct_late_pm_peak:.0%}"
        md.append(f"| {r.route_name} | {r.n_trips} | {r.pct_late:.0%} | {r.late_ci} | "
                  f"{r.median_delay_min:.1f} min | {r.p90_delay_min:.1f} min | {pm} |")
    md.append("\n_CI is a Wilson interval on timepoint events; events within a trip are correlated, so treat it as optimistic._\n")
    return df


def section_worst_windows(md: list[str]) -> None:
    df = query(f"""
        select route_id, time_period, count(distinct instance_id) as n_trips,
               avg(is_late::INTEGER) as pct_late, median(delay_sec) / 60 as median_delay_min
        from {mart('fct_stop_events')}
        where is_timepoint and day_type = 'Weekday'
        group by all
        having count(distinct instance_id) >= {MIN_TRIPS // 2}
        order by pct_late desc
        limit 10
    """)
    md.append("## Worst route × time-of-day windows (weekdays)\n")
    if df.empty:
        md.append("_Not enough data yet._\n")
        return
    md.append("| Route | Period | Trips | % late | Median delay |")
    md.append("|---|---|---|---|---|")
    for r in df.itertuples():
        md.append(f"| {r.route_id} | {r.time_period[2:]} | {r.n_trips} | {r.pct_late:.0%} | {r.median_delay_min:.1f} min |")
    md.append("")


def section_weekday_weekend(md: list[str], trips: pd.DataFrame) -> None:
    md.append("## Weekday vs weekend delay\n")
    wd = trips.loc[trips.day_class == "Weekday", "mean_delay_min"].to_numpy()
    we = trips.loc[trips.day_class == "Weekend", "mean_delay_min"].to_numpy()
    if len(wd) < 20 or len(we) < 20:
        md.append(f"_Need ≥20 trips of each; have {len(wd)} weekday, {len(we)} weekend._\n")
        return
    u = stats.mannwhitneyu(wd, we, alternative="two-sided")
    lo, hi = bootstrap_diff_ci(wd, we)
    md.append(f"System-wide, the median trip-average delay is **{np.median(wd):.1f} min on weekdays** vs "
              f"**{np.median(we):.1f} min on weekends** (difference 95% CI {lo:+.1f} to {hi:+.1f} min; "
              f"Mann-Whitney U p = {u.pvalue:.2g}, n = {len(wd)} / {len(we)} trips).\n")

    rows = []
    for route, g in trips.groupby("route_id"):
        a = g.loc[g.day_class == "Weekday", "mean_delay_min"]
        b = g.loc[g.day_class == "Weekend", "mean_delay_min"]
        if len(a) >= 15 and len(b) >= 15:
            p = stats.mannwhitneyu(a, b, alternative="two-sided").pvalue
            rows.append((route, len(a), len(b), a.median(), b.median(), p))
    if rows:
        per = pd.DataFrame(rows, columns=["route", "n_wd", "n_we", "med_wd", "med_we", "p"])
        per["q"] = benjamini_hochberg(per.p)
        sig = per[per.q < 0.05].sort_values("q")
        md.append(f"Per route: {len(sig)} of {len(per)} routes differ at a 5% false-discovery rate "
                  "(Benjamini-Hochberg).\n")
        if not sig.empty:
            md.append("| Route | Weekday median | Weekend median | q |")
            md.append("|---|---|---|---|")
            for r in sig.head(10).itertuples():
                md.append(f"| {r.route} | {r.med_wd:.1f} min | {r.med_we:.1f} min | {r.q:.2g} |")
            md.append("")


def section_runtime(md: list[str]) -> None:
    rt = query(f"select * from {mart('fct_trip_runtimes')}")
    md.append("## Does the schedule understate travel time?\n")
    if len(rt) < 20:
        md.append(f"_Only {len(rt)} end-to-end trips observed so far._\n")
        return
    excess = rt.runtime_excess_min.to_numpy()
    w = stats.wilcoxon(excess)
    lo, hi = bootstrap_ci(excess)
    md.append(f"Across {len(rt)} end-to-end trips, buses took a median **{np.median(excess):+.1f} min** versus "
              f"the scheduled running time (95% CI {lo:+.1f} to {hi:+.1f}; Wilcoxon signed-rank p = {w.pvalue:.2g}).\n")

    rows = []
    for route, g in rt.groupby("route_id"):
        if len(g) >= 15:
            ratio = (g.observed_runtime_min / g.scheduled_runtime_min).to_numpy()
            l, h = bootstrap_ci(g.runtime_excess_min.to_numpy())
            rows.append((route, len(g), g.scheduled_runtime_min.median(), g.runtime_excess_min.median(), l, h,
                         np.median(ratio), (g.runtime_excess_min > 0).mean()))
    if rows:
        per = pd.DataFrame(rows, columns=["route", "n", "sched", "excess", "lo", "hi", "ratio", "share_longer"])
        md.append("| Route | Trips | Scheduled runtime | Median extra | 95% CI | Actual ÷ scheduled | Trips longer than scheduled |")
        md.append("|---|---|---|---|---|---|---|")
        for r in per.sort_values("ratio", ascending=False).head(10).itertuples():
            md.append(f"| {r.route} | {r.n} | {r.sched:.0f} min | {r.excess:+.1f} min | {r.lo:+.1f} to {r.hi:+.1f} | "
                      f"{r.ratio:.2f}× | {r.share_longer:.0%} |")
        md.append("")


def section_coverage(md: list[str]) -> None:
    c = query(f"""
        select count(*) as events, count(distinct instance_id) as trips, count(distinct route_id) as routes,
               min(service_date) as first_day, max(service_date) as last_day,
               count(distinct service_date) as days, median(interp_window_sec) as median_window
        from {mart('fct_stop_events')}
    """).iloc[0]
    md.append("## Data coverage\n")
    md.append(f"{c.events:,} stop events from {c.trips:,} trips on {c.routes} routes, "
              f"{str(c.first_day)[:10]} to {str(c.last_day)[:10]} ({c.days} service days). Median interpolation "
              f"window: {c.median_window:.0f}s.\n")


def section_resume_numbers(md: list[str]) -> None:
    import json
    path = config.MARTS_DIR / "pipeline_stats.json"
    if not path.exists():
        return
    s = json.loads(path.read_text())
    first, last = pd.Timestamp(s["first_poll_utc"]), pd.Timestamp(s["last_poll_utc"])
    days = (last - first).total_seconds() / 86400
    md.append("## Pipeline numbers\n")
    md.append(f"- Raw pings ingested (after de-duplication): **{s['raw_pings']:,}** "
              f"({s['raw_bytes_ingested'] / 1e6:,.0f} MB of JSON scanned)")
    md.append(f"- Collection window: {first:%Y-%m-%d %H:%M} to {last:%Y-%m-%d %H:%M} UTC (**{days:.1f} days**, {days / 7:.1f} weeks)")
    md.append(f"- Clean revenue pings: {s['clean_pings']:,}; inferred stop visits: **{s['stop_events']:,}** "
              f"on {s['trips']:,} trips across **{s['routes']} routes**")
    md.append(f"- GTFS schedule versions matched against: {s['gtfs_versions']}\n")


def main() -> None:
    md = ["# MBTA bus reliability: findings\n",
          "_Generated by `python -m transit analyze`. On time = between 1 min early and 6 min late at a timepoint (MBTA standard)._\n"]
    section_coverage(md)
    section_resume_numbers(md)
    section_least_reliable(md)
    section_worst_windows(md)
    section_weekday_weekend(md, trip_level())
    section_runtime(md)
    out = config.MARTS_DIR / "findings.md"
    out.write_text("\n".join(md))
    print("\n".join(md))
    print(f"\nwritten to {out}")


if __name__ == "__main__":
    main()
