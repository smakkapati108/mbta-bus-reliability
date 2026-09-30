{{ config(materialized="table") }}

-- Scheduled stops for each trip, from the same feed version chosen in stg_gtfs_trips.
-- stop_sequence values are not contiguous (1, 10, 20, ...), so stop_index gives a dense
-- 1..n position that the progress interpolation in int_ping_progress relies on.
with st as (
    select st.*
    from {{ source('gtfs', 'stop_times') }} st
    join {{ ref('stg_gtfs_trips') }} t using (trip_id, feed_version)
)
select
    trip_id,
    stop_id,
    stop_sequence::INTEGER as stop_sequence,
    row_number() over (partition by trip_id order by stop_sequence::INTEGER) as stop_index,
    count(*) over (partition by trip_id) as n_stops,
    {{ gtfs_seconds('arrival_time') }} as sched_arrival_sec,
    {{ gtfs_seconds('departure_time') }} as sched_departure_sec,
    nullif(checkpoint_id, '') is not null as is_timepoint,
    nullif(checkpoint_id, '') as checkpoint_id
from st
