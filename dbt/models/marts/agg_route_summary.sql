-- One row per route: the headline reliability numbers used in the dashboard and README.
with adherence as (
    select
        route_id,
        count(*) as n_timepoint_events,
        count(distinct instance_id) as n_trips,
        count(distinct service_date) as n_service_days,
        median(delay_sec) / 60.0 as median_delay_min,
        quantile_cont(delay_sec, 0.9) / 60.0 as p90_delay_min,
        avg(is_on_time::INTEGER) as pct_on_time,
        avg(is_late::INTEGER) as pct_late,
        avg(is_late::INTEGER) filter (where time_period like '%PM peak%' and day_type = 'Weekday') as pct_late_pm_peak,
        avg(is_late::INTEGER) filter (where time_period like '%AM peak%' and day_type = 'Weekday') as pct_late_am_peak
    from {{ ref('fct_stop_events') }}
    where is_timepoint
    group by route_id
),

runtimes as (
    select
        route_id,
        count(*) as n_full_trips,
        median(observed_runtime_min / scheduled_runtime_min) as median_runtime_ratio,
        median(runtime_excess_min) as median_runtime_excess_min
    from {{ ref('fct_trip_runtimes') }}
    group by route_id
),

headways as (
    select
        route_id,
        avg(is_bunched::INTEGER) as pct_bunched,
        avg(is_gapped::INTEGER) as pct_gapped
    from {{ ref('fct_headways') }}
    group by route_id
)

select
    r.route_id,
    r.route_name,
    r.route_long_name,
    a.* exclude (route_id),
    rt.* exclude (route_id),
    h.* exclude (route_id)
from adherence a
join {{ ref('dim_routes') }} r using (route_id)
left join runtimes rt using (route_id)
left join headways h using (route_id)
