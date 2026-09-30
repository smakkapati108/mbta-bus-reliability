"""Predict a bus's delay at its next timepoint from what is known when it leaves the current one.

This is the rider-facing question ("my bus is 4 min late now; how late at my stop?"), so
every feature must be available at prediction time: the current delay, where on the trip
the bus is, time of day, and how late *other* buses on the route have been in the last
30 minutes. The split is by time (train on earlier days, test on later ones) because a
random split leaks same-day conditions into training.

Baselines: "schedule" (predict 0 delay) and "persistence" (delay stays what it is now).
The model learns the *change* in delay between timepoints and adds it to the current delay,
so with no signal it degrades to persistence instead of below it.
"""
from __future__ import annotations

import json

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.inspection import permutation_importance
from sklearn.metrics import mean_absolute_error

from transit import config

from .data import mart, query

CATEGORICAL = ["route_id", "direction_id", "day_type"]
NUMERIC = ["current_delay_min", "hour", "dow", "trip_fraction", "target_trip_fraction",
           "sched_min_to_target", "stops_to_target", "route_recent_delay_min", "route_recent_n"]
FEATURES = CATEGORICAL + NUMERIC


def build_dataset() -> pd.DataFrame:
    return query(f"""
        with tp as (
            select * from {mart('fct_stop_events')} where is_timepoint
        ),
        recent as (
            -- Average delay of any bus on this route observed in the 30 minutes before this event.
            -- RANGE framing excludes the current row's own time, so there is no target leakage.
            select *,
                   avg(delay_sec) over w / 60.0 as route_recent_delay_min,
                   count(*) over w as route_recent_n
            from tp
            window w as (partition by route_id order by observed_utc
                         range between interval 30 minutes preceding and interval 1 microsecond preceding)
        ),
        paired as (
            select
                route_id, direction_id::VARCHAR as direction_id, day_type, service_date,
                observed_utc,
                delay_sec / 60.0 as current_delay_min,
                hour_local as hour, dow, trip_fraction,
                route_recent_delay_min, route_recent_n,
                lead(delay_sec) over w / 60.0 as target_delay_min,
                lead(trip_fraction) over w as target_trip_fraction,
                date_diff('second', scheduled_utc, lead(scheduled_utc) over w) / 60.0 as sched_min_to_target,
                lead(stop_index) over w - stop_index as stops_to_target
            from recent
            window w as (partition by instance_id order by stop_index)
        )
        select * from paired where target_delay_min is not null order by observed_utc
    """)


def time_split(df: pd.DataFrame, test_frac: float = 0.2) -> tuple[pd.DataFrame, pd.DataFrame, str]:
    days = sorted(df.service_date.unique())
    if len(days) >= 5:
        cutoff = days[int(len(days) * (1 - test_frac))]
        return df[df.service_date < cutoff], df[df.service_date >= cutoff], f"by service date (test from {cutoff})"
    cutoff = df.observed_utc.quantile(1 - test_frac)
    return df[df.observed_utc < cutoff], df[df.observed_utc >= cutoff], "by time within the collected window (fewer than 5 days of data)"


def main() -> None:
    df = build_dataset()
    if len(df) < 200:
        print(f"Only {len(df)} timepoint pairs; collect more data before training.")
        return
    for c in CATEGORICAL:
        df[c] = df[c].astype("category")
    train, test, split_desc = time_split(df)

    model = HistGradientBoostingRegressor(
        loss="absolute_error", max_iter=400, learning_rate=0.05, max_leaf_nodes=31,
        categorical_features="from_dtype", early_stopping=True, validation_fraction=0.15, random_state=0,
    )
    model.fit(train[FEATURES], train.target_delay_min - train.current_delay_min)
    pred = test.current_delay_min + model.predict(test[FEATURES])

    y = test.target_delay_min
    metrics = {
        "n_train": len(train),
        "n_test": len(test),
        "split": split_desc,
        "mae_schedule_baseline_min": mean_absolute_error(y, np.zeros(len(y))),
        "mae_persistence_baseline_min": mean_absolute_error(y, test.current_delay_min),
        "mae_model_min": mean_absolute_error(y, pred),
        "share_within_2min": float((np.abs(y - pred) <= 2).mean()),
    }
    metrics["improvement_vs_persistence"] = 1 - metrics["mae_model_min"] / metrics["mae_persistence_baseline_min"]

    sample = test.sample(min(len(test), 5000), random_state=0)
    imp = permutation_importance(model, sample[FEATURES], sample.target_delay_min - sample.current_delay_min,
                                 scoring="neg_mean_absolute_error", n_repeats=5, random_state=0)
    # Importance is measured on the delay-change target, so current_delay_min only gets
    # credit for how it shifts the change, not for the persistence part it anchors.
    metrics["feature_importance_mae_increase_min"] = dict(
        sorted(zip(FEATURES, imp.importances_mean.round(3).tolist()), key=lambda kv: -kv[1])
    )

    config.MARTS_DIR.mkdir(parents=True, exist_ok=True)
    (config.MARTS_DIR / "model_metrics.json").write_text(json.dumps(metrics, indent=2, default=float))
    joblib.dump(model, config.DATA_DIR / "delay_model.joblib")
    print(json.dumps(metrics, indent=2, default=float))


if __name__ == "__main__":
    main()
