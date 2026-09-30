with ranked as (
    select s.*
    from {{ source('gtfs', 'stops') }} s
    join {{ source('gtfs', 'feeds') }} f using (feed_version)
    qualify row_number() over (partition by s.stop_id order by f.loaded_at desc) = 1
)
select
    stop_id,
    stop_name,
    stop_lat::DOUBLE as stop_lat,
    stop_lon::DOUBLE as stop_lon,
    municipality
from ranked
