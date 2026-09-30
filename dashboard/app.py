"""Streamlit dashboard over the Parquet marts. Run: streamlit run dashboard/app.py"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import altair as alt
import duckdb
import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from transit import config  # noqa: E402

# Palette: sequential blue for magnitude, blue<->red diverging around a gray zero for
# early/late, fixed categorical slots (blue, orange) for observed vs scheduled.
BLUE_RAMP = ["#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95", "#0d366b"]
EARLY, NEUTRAL, LATE = "#2a78d6", "#d9d8d4", "#e34948"
OBSERVED, SCHEDULED = "#2a78d6", "#eb6834"

st.set_page_config(page_title="MBTA Bus Reliability", layout="wide")


@st.cache_data(ttl=300)
def q(sql: str) -> pd.DataFrame:
    return duckdb.sql(sql).df()


def mart(name: str) -> str:
    return f"read_parquet('{config.MARTS_DIR / name}.parquet')"


if not (config.MARTS_DIR / "agg_route_summary.parquet").exists():
    st.error("No marts yet. Run `python -m transit refresh` after collecting some data.")
    st.stop()

coverage = q(f"""
    select count(*) as events, count(distinct instance_id) as trips, count(distinct route_id) as routes,
           min(service_date) as d0, max(service_date) as d1, max(observed_utc) as last_obs
    from {mart('fct_stop_events')}
""").iloc[0]

st.title("MBTA bus reliability")
st.caption(
    f"{coverage.events:,} stop visits · {coverage.trips:,} trips · {coverage.routes} routes · "
    f"{str(coverage.d0)[:10]} → {str(coverage.d1)[:10]}. On time = 1 min early to 6 min late at a timepoint."
)

tab_overview, tab_route, tab_model = st.tabs(["Route ranking", "Route detail", "Delay model"])

# ---------------------------------------------------------------- overview
with tab_overview:
    min_trips = st.slider("Minimum observed trips per route", 1, 500, 30)
    summary = q(f"select * from {mart('agg_route_summary')}")
    summary = summary[summary.n_trips >= min_trips].copy()

    if summary.empty:
        st.info("No routes meet the minimum yet — lower the slider or collect more data.")
    else:
        weighted_late = (summary.pct_late * summary.n_timepoint_events).sum() / summary.n_timepoint_events.sum()
        worst = summary.sort_values("pct_late", ascending=False).iloc[0]
        c1, c2, c3 = st.columns(3)
        c1.metric("Timepoint visits more than 6 min late", f"{weighted_late:.0%}")
        c2.metric("Least reliable route", f"Route {worst.route_name}", f"{worst.pct_late:.0%} late", delta_color="off")
        if summary.median_runtime_ratio.notna().any():
            c3.metric("Median actual ÷ scheduled runtime", f"{summary.median_runtime_ratio.median():.2f}×")

        top = summary.sort_values("pct_late", ascending=False).head(25)
        chart = (
            alt.Chart(top)
            .mark_bar(cornerRadiusEnd=4, color=LATE, size=14)
            .encode(
                x=alt.X("pct_late:Q", title="Share of timepoint visits > 6 min late", axis=alt.Axis(format="%")),
                y=alt.Y("route_name:N", sort="-x", title="Route"),
                tooltip=[
                    alt.Tooltip("route_name", title="Route"),
                    alt.Tooltip("route_long_name", title="Name"),
                    alt.Tooltip("pct_late", title="% late", format=".0%"),
                    alt.Tooltip("pct_on_time", title="% on time", format=".0%"),
                    alt.Tooltip("median_delay_min", title="Median delay (min)", format=".1f"),
                    alt.Tooltip("p90_delay_min", title="p90 delay (min)", format=".1f"),
                    alt.Tooltip("n_trips", title="Trips"),
                ],
            )
            .properties(height=alt.Step(20), title="25 least reliable routes")
        )
        st.altair_chart(chart, width="stretch")

        with st.expander("All routes (table)"):
            st.dataframe(
                summary.sort_values("pct_late", ascending=False)[[
                    "route_name", "route_long_name", "n_trips", "pct_on_time", "pct_late", "pct_late_pm_peak",
                    "median_delay_min", "p90_delay_min", "median_runtime_ratio", "pct_bunched",
                ]],
                hide_index=True,
                column_config={
                    c: st.column_config.NumberColumn(format="percent")
                    for c in ["pct_on_time", "pct_late", "pct_late_pm_peak", "pct_bunched"]
                },
            )

# ---------------------------------------------------------------- route detail
with tab_route:
    routes = q(f"""
        select route_id, route_name, route_long_name, n_trips
        from {mart('agg_route_summary')} order by n_trips desc
    """)
    labels = {r.route_id: f"{r.route_name} — {r.route_long_name} ({r.n_trips} trips)" for r in routes.itertuples()}
    route = st.selectbox("Route", list(labels), format_func=labels.get)
    day_type = st.segmented_control("Day type", ["Weekday", "Saturday", "Sunday"], default="Weekday")

    hourly = q(f"select * from {mart('agg_route_hourly')} where route_id = '{route}'")
    hourly = hourly[hourly.day_type == day_type]
    left, right = st.columns(2)

    with left:
        st.subheader("Late share by hour")
        if hourly.empty:
            st.info("No observations for this day type yet.")
        else:
            bars = (
                alt.Chart(hourly)
                .mark_bar(cornerRadiusEnd=4, size=14)
                .encode(
                    x=alt.X("hour_local:O", title="Scheduled hour"),
                    y=alt.Y("pct_late:Q", title="% > 6 min late", axis=alt.Axis(format="%")),
                    color=alt.Color("pct_late:Q", scale=alt.Scale(range=BLUE_RAMP[2:], domain=[0, 1]), legend=None),
                    tooltip=[
                        alt.Tooltip("hour_local", title="Hour"),
                        alt.Tooltip("pct_late", title="% late", format=".0%"),
                        alt.Tooltip("pct_early", title="% early", format=".0%"),
                        alt.Tooltip("median_delay_min", title="Median delay (min)", format=".1f"),
                        alt.Tooltip("n_trips", title="Trips"),
                    ],
                )
            )
            st.altair_chart(bars, width="stretch")

    events = q(f"""
        select delay_sec / 60.0 as delay_min, time_period, hour_local
        from {mart('fct_stop_events')}
        where route_id = '{route}' and day_type = '{day_type}' and is_timepoint
    """)
    with right:
        st.subheader("Delay distribution by period")
        if events.empty:
            st.info("No observations for this day type yet.")
        else:
            events["time_period"] = events.time_period.str[2:]
            box = (
                alt.Chart(events)
                .mark_boxplot(extent="min-max", size=18, color=OBSERVED, median={"color": "white"})
                .encode(
                    y=alt.Y("time_period:N", title=None, sort=None),
                    x=alt.X("delay_min:Q", title="Delay at timepoints (min)"),
                )
            )
            rule = alt.Chart(pd.DataFrame({"x": [-1, 6]})).mark_rule(strokeDash=[4, 3], color="#8a8985").encode(x="x:Q")
            st.altair_chart(box + rule, width="stretch")
            st.caption("Dashed lines mark the on-time window (−1 to +6 min).")

    st.subheader("Actual vs scheduled running time")
    rt = q(f"""
        select scheduled_start_local, scheduled_runtime_min, observed_runtime_min, runtime_excess_min, start_hour_local
        from {mart('fct_trip_runtimes')} where route_id = '{route}'
    """)
    if rt.empty:
        st.info("No end-to-end trips observed on this route yet.")
    else:
        long = rt.melt(id_vars=["start_hour_local"], value_vars=["scheduled_runtime_min", "observed_runtime_min"],
                       var_name="kind", value_name="minutes")
        long["kind"] = long.kind.map({"scheduled_runtime_min": "Scheduled", "observed_runtime_min": "Actual"})
        agg = long.groupby(["start_hour_local", "kind"], as_index=False).minutes.median()
        lines = (
            alt.Chart(agg)
            .mark_line(point=alt.OverlayMarkDef(size=64, filled=True), strokeWidth=2)
            .encode(
                x=alt.X("start_hour_local:O", title="Scheduled start hour"),
                y=alt.Y("minutes:Q", title="Median trip time (min)", scale=alt.Scale(zero=False)),
                color=alt.Color("kind:N", scale=alt.Scale(domain=["Actual", "Scheduled"], range=[OBSERVED, SCHEDULED]),
                                legend=alt.Legend(title=None, orient="top")),
                tooltip=[alt.Tooltip("start_hour_local", title="Hour"), "kind",
                         alt.Tooltip("minutes", title="Median minutes", format=".1f")],
            )
        )
        st.altair_chart(lines, width="stretch")
        st.caption(f"{len(rt)} end-to-end trips · median extra time {rt.runtime_excess_min.median():+.1f} min vs schedule.")

    hw = q(f"""
        select time_period, avg(is_bunched::INT) as bunched, avg(is_gapped::INT) as gapped, count(*) as n
        from {mart('fct_headways')} where route_id = '{route}' and day_type = '{day_type}'
        group by 1 order by 1
    """)
    if not hw.empty:
        st.subheader("Bunching and gaps")
        hw["time_period"] = hw.time_period.str[2:]
        st.dataframe(
            hw.rename(columns={"time_period": "Period", "bunched": "Bunched (<50% of scheduled headway)",
                               "gapped": "Gapped (>150%)", "n": "Headways"}),
            hide_index=True,
            column_config={c: st.column_config.NumberColumn(format="percent")
                           for c in ["Bunched (<50% of scheduled headway)", "Gapped (>150%)"]},
        )

# ---------------------------------------------------------------- model
with tab_model:
    path = config.MARTS_DIR / "model_metrics.json"
    if not path.exists():
        st.info("Run `python -m transit train` once you have a few days of data.")
    else:
        m = json.loads(path.read_text())
        st.markdown("Predicts delay at the **next timepoint** from what is known when a bus leaves the current one. "
                    f"Split {m['split']}: {m['n_train']:,} train / {m['n_test']:,} test pairs.")
        c1, c2, c3 = st.columns(3)
        c1.metric("Model MAE", f"{m['mae_model_min']:.2f} min")
        c2.metric("Persistence baseline MAE", f"{m['mae_persistence_baseline_min']:.2f} min",
                  f"{-m['improvement_vs_persistence']:.0%}", delta_color="inverse")
        c3.metric("Predictions within 2 min", f"{m['share_within_2min']:.0%}")
        imp = pd.DataFrame(list(m["feature_importance_mae_increase_min"].items()), columns=["feature", "mae_increase"])
        st.altair_chart(
            alt.Chart(imp).mark_bar(cornerRadiusEnd=4, color=OBSERVED, size=14).encode(
                x=alt.X("mae_increase:Q", title="MAE increase when shuffled (min)"),
                y=alt.Y("feature:N", sort="-x", title=None),
                tooltip=["feature", alt.Tooltip("mae_increase", format=".3f")],
            ).properties(title="Permutation importance"),
            width="stretch",
        )
