-- One row per trip_id, taken from the newest feed version that contains it.
with ranked as (
    select t.*, f.loaded_at
    from {{ source('gtfs', 'trips') }} t
    join {{ source('gtfs', 'feeds') }} f using (feed_version)
    qualify row_number() over (partition by t.trip_id order by f.loaded_at desc) = 1
)
select
    trip_id,
    feed_version,
    route_id,
    service_id,
    direction_id::INTEGER as direction_id,
    trip_headsign,
    route_pattern_id,
    block_id
from ranked
