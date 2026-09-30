/*
  Observed vs scheduled headway between consecutive observed buses on the same route,
  direction and timepoint. On frequent routes riders experience headways, not schedules:
  a bus 8 minutes late is invisible if the one behind it is also 8 minutes late.

  If a trip in between was not observed, both headways cover the same gap, so the ratio
  stays meaningful. Very long scheduled gaps (service breaks) are excluded.
*/
with tp as (
    select *
    from {{ ref('fct_stop_events') }}
    where is_timepoint
),

paired as (
    select
        route_id,
        direction_id,
        stop_id,
        stop_name,
        service_date,
        day_type,
        time_period,
        hour_local,
        trip_id,
        scheduled_utc,
        observed_utc,
        date_diff('second', lag(scheduled_utc) over w, scheduled_utc) / 60.0 as scheduled_headway_min,
        date_diff('second', lag(observed_utc) over w, observed_utc) / 60.0 as observed_headway_min
    from tp
    window w as (partition by route_id, direction_id, stop_id, service_date order by scheduled_utc)
)

select
    *,
    observed_headway_min / scheduled_headway_min as headway_ratio,
    observed_headway_min < 0.5 * scheduled_headway_min as is_bunched,
    observed_headway_min > 1.5 * scheduled_headway_min as is_gapped
from paired
where scheduled_headway_min > 0
  and scheduled_headway_min <= 60
