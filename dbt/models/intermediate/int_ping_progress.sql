/*
  Place every ping on a continuous "progress" scale along its trip, where stop k of the
  trip is progress = k. That turns a 60-second sample of positions into something we can
  interpolate: a bus seen at progress 4.3 and then 7.8 passed stops 5, 6 and 7 in between.

    STOPPED_AT k             -> k
    IN_TRANSIT_TO / INCOMING -> k - (distance to stop k / length of segment k-1 -> k), clamped

  Pings are also grouped into trip *instances*: the same trip_id runs once per service day,
  and a vehicle that reappears on a trip after a long gap is treated as a new run.
*/
with stop_geo as (
    select
        st.trip_id,
        st.stop_sequence,
        st.stop_index,
        st.n_stops,
        st.sched_arrival_sec,
        s.stop_lat,
        s.stop_lon,
        lag(s.stop_lat) over (partition by st.trip_id order by st.stop_index) as prev_stop_lat,
        lag(s.stop_lon) over (partition by st.trip_id order by st.stop_index) as prev_stop_lon
    from {{ ref('stg_gtfs_stop_times') }} st
    join {{ ref('stg_gtfs_stops') }} s using (stop_id)
),

located as (
    select
        p.*,
        g.stop_index,
        g.n_stops,
        g.sched_arrival_sec,
        {{ haversine_m('p.latitude', 'p.longitude', 'g.stop_lat', 'g.stop_lon') }} as dist_to_stop_m,
        {{ haversine_m('g.prev_stop_lat', 'g.prev_stop_lon', 'g.stop_lat', 'g.stop_lon') }} as segment_len_m
    from {{ ref('stg_vehicle_pings') }} p
    join stop_geo g
      on g.trip_id = p.trip_id
     and g.stop_sequence = p.current_stop_sequence
),

sessionized as (
    select
        *,
        case
            when date_diff('second', lag(ping_utc) over w, ping_utc) <= 5400 then 0
            else 1
        end as starts_instance
    from located
    window w as (partition by trip_id, vehicle_id order by ping_utc)
),

instances as (
    select
        *,
        sum(starts_instance) over (
            partition by trip_id, vehicle_id order by ping_utc rows unbounded preceding
        ) as instance_no
    from sessionized
),

with_service_date as (
    select
        *,
        /*
          Service date: local ping time minus the stop's scheduled seconds-after-midnight lands
          near midnight of the service date (± the delay). Adding 12h and truncating picks the
          right day for after-midnight trips (scheduled 25:10:00) without calendar lookups.
        */
        min_by(
            cast({{ to_local('ping_utc') }} - to_seconds(sched_arrival_sec) + INTERVAL 12 HOUR as DATE),
            ping_utc
        ) over (partition by trip_id, vehicle_id, instance_no) as service_date
    from instances
),

progressed as (
    select
        trip_id || '|' || service_date || '|' || vehicle_id || '|' || instance_no as instance_id,
        trip_id,
        vehicle_id,
        route_id,
        direction_id,
        service_date,
        ping_utc,
        current_status,
        stop_index,
        n_stops,
        dist_to_stop_m,
        case
            when current_status = 'STOPPED_AT' then stop_index::DOUBLE
            when segment_len_m is null or segment_len_m < 1 then stop_index - 0.5
            else stop_index - least(greatest(dist_to_stop_m / segment_len_m, 0.01), 0.99)
        end as progress
    from with_service_date
)

select
    *,
    -- GPS jitter or a stale stop assignment can move a bus "backwards"; such pings are
    -- kept for auditing but excluded from interpolation.
    progress >= coalesce(
        max(progress) over (
            partition by instance_id order by ping_utc rows between unbounded preceding and 1 preceding
        ),
        0
    ) as is_monotonic
from progressed
