-- One row per observed stop visit, with schedule adherence and reporting dimensions.
with events as (
    select
        e.*,
        t.route_id,
        t.direction_id,
        t.route_pattern_id,
        t.trip_headsign,
        date_diff('second', e.scheduled_utc, e.observed_utc) as delay_sec,
        {{ to_local('e.scheduled_utc') }} as scheduled_local
    from {{ ref('int_stop_events') }} e
    join {{ ref('stg_gtfs_trips') }} t using (trip_id)
)

select
    instance_id,
    trip_id,
    vehicle_id,
    route_id,
    direction_id,
    route_pattern_id,
    trip_headsign,
    service_date,
    stop_id,
    s.stop_name,
    stop_index,
    n_stops,
    stop_index::DOUBLE / n_stops as trip_fraction,
    is_timepoint,
    event_type,
    scheduled_utc,
    observed_utc,
    scheduled_local,
    delay_sec,
    interp_window_sec,
    hour(scheduled_local) as hour_local,
    dayofweek(service_date) as dow,       -- 0 = Sunday
    dayname(service_date) as day_name,
    case
        when dayofweek(service_date) = 0 then 'Sunday'
        when dayofweek(service_date) = 6 then 'Saturday'
        else 'Weekday'
    end as day_type,
    {{ time_period('hour(scheduled_local)') }} as time_period,
    delay_sec < {{ var('early_threshold_sec') }} as is_early,
    delay_sec > {{ var('late_threshold_sec') }} as is_late,
    delay_sec between {{ var('early_threshold_sec') }} and {{ var('late_threshold_sec') }} as is_on_time
from events
left join {{ ref('stg_gtfs_stops') }} s using (stop_id)
-- Anything off by more than 3 hours is a mis-assigned trip, not a late bus.
where abs(delay_sec) <= 3 * 3600
