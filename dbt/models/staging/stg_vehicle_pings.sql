-- Revenue-service position reports that can be tied to a scheduled trip and are fresh.
select
    vehicle_id,
    trip_id,
    route_id,
    direction_id,
    current_status,
    current_stop_sequence,
    stop_id,
    latitude,
    longitude,
    updated_at as ping_utc,
    polled_at as polled_utc,
    date_diff('second', updated_at, polled_at) as staleness_sec
from {{ source('raw', 'vehicle_pings') }}
where revenue = 'REVENUE'
  and trip_id is not null
  and current_stop_sequence is not null
  and latitude is not null
  and date_diff('second', updated_at, polled_at) <= {{ var('max_ping_staleness_sec') }}
