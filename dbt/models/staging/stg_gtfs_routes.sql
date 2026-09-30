with ranked as (
    select r.*
    from {{ source('gtfs', 'routes') }} r
    join {{ source('gtfs', 'feeds') }} f using (feed_version)
    qualify row_number() over (partition by r.route_id order by f.loaded_at desc) = 1
)
select
    route_id,
    coalesce(nullif(route_short_name, ''), route_id) as route_name,
    route_long_name,
    route_desc,
    route_type::INTEGER as route_type,
    route_sort_order::INTEGER as route_sort_order
from ranked
