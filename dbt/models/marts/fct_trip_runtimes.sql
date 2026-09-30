/*
  Observed vs scheduled running time for trip instances seen (nearly) end to end.
  This answers "how much does the posted schedule understate real travel time?".
*/
with bounds as (
    select
        instance_id,
        any_value(trip_id) as trip_id,
        any_value(route_id) as route_id,
        any_value(direction_id) as direction_id,
        any_value(service_date) as service_date,
        any_value(n_stops) as n_stops,
        min(stop_index) as first_stop_index,
        max(stop_index) as last_stop_index,
        count(*) as n_events,
        arg_min(observed_utc, stop_index) as first_observed_utc,
        arg_max(observed_utc, stop_index) as last_observed_utc,
        arg_min(scheduled_utc, stop_index) as first_scheduled_utc,
        arg_max(scheduled_utc, stop_index) as last_scheduled_utc,
        arg_min(delay_sec, stop_index) as start_delay_sec,
        arg_max(delay_sec, stop_index) as end_delay_sec
    from {{ ref('fct_stop_events') }}
    group by instance_id
)

select
    *,
    date_diff('second', first_observed_utc, last_observed_utc) / 60.0 as observed_runtime_min,
    date_diff('second', first_scheduled_utc, last_scheduled_utc) / 60.0 as scheduled_runtime_min,
    (date_diff('second', first_observed_utc, last_observed_utc)
        - date_diff('second', first_scheduled_utc, last_scheduled_utc)) / 60.0 as runtime_excess_min,
    {{ to_local('first_scheduled_utc') }} as scheduled_start_local,
    hour({{ to_local('first_scheduled_utc') }}) as start_hour_local,
    case when dayofweek(service_date) in (0, 6) then 'Weekend' else 'Weekday' end as day_class,
    {{ time_period('hour(' ~ to_local('first_scheduled_utc') ~ ')') }} as time_period
from bounds
-- "End to end": starts within the first two stops and reaches the last two.
where first_stop_index <= 2
  and last_stop_index >= n_stops - 1
  and date_diff('second', first_scheduled_utc, last_scheduled_utc) > 0
