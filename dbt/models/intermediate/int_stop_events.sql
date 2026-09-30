/*
  Estimated time each trip instance reached each stop.

  Consecutive monotonic pings form segments (p0, t0) -> (p1, t1). Every stop index k with
  p0 < k <= p1 was reached inside that segment, at a time linearly interpolated between
  t0 and t1. For the first stop of a trip we want the departure, which is the last ping
  still stopped at the origin (p0 = 1) before the bus moves.
*/
with pings as (
    select * from {{ ref('int_ping_progress') }} where is_monotonic
),

segments as (
    select
        instance_id,
        trip_id,
        vehicle_id,
        service_date,
        progress as p0,
        ping_utc as t0,
        lead(progress) over w as p1,
        lead(ping_utc) over w as t1
    from pings
    window w as (partition by instance_id order by ping_utc)
),

usable as (
    select *
    from segments
    where p1 > p0
      and date_diff('second', t0, t1) <= {{ var('max_interp_gap_sec') }}
)

select
    s.instance_id,
    s.trip_id,
    s.vehicle_id,
    s.service_date,
    st.stop_id,
    st.stop_index,
    st.n_stops,
    st.is_timepoint,
    st.checkpoint_id,
    case when st.stop_index = 1 then 'departure' else 'arrival' end as event_type,
    case
        when st.stop_index = 1 then s.t0
        else {{ from_epoch_s(
            epoch_s('s.t0') ~ ' + (st.stop_index - s.p0) / (s.p1 - s.p0) * (' ~ epoch_s('s.t1') ~ ' - ' ~ epoch_s('s.t0') ~ ')'
        ) }}
    end as observed_utc,
    {{ gtfs_to_utc(
        's.service_date',
        'case when st.stop_index = 1 then st.sched_departure_sec else st.sched_arrival_sec end'
    ) }} as scheduled_utc,
    -- Width of the interpolation window: a rough error bar on observed_utc.
    date_diff('second', s.t0, s.t1) as interp_window_sec
from usable s
join {{ ref('stg_gtfs_stop_times') }} st
  on st.trip_id = s.trip_id
 and (
        (st.stop_index > 1 and st.stop_index > s.p0 and st.stop_index <= s.p1)
     or (st.stop_index = 1 and s.p0 = 1)
 )
