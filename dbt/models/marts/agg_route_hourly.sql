-- Schedule adherence at timepoints by route, day type and scheduled hour.
select
    route_id,
    day_type,
    hour_local,
    count(*) as n_events,
    count(distinct instance_id) as n_trips,
    median(delay_sec) / 60.0 as median_delay_min,
    quantile_cont(delay_sec, 0.9) / 60.0 as p90_delay_min,
    avg(is_on_time::INTEGER) as pct_on_time,
    avg(is_late::INTEGER) as pct_late,
    avg(is_early::INTEGER) as pct_early
from {{ ref('fct_stop_events') }}
where is_timepoint
group by all
