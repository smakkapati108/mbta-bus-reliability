select route_id, route_name, route_long_name, route_desc, route_sort_order
from {{ ref('stg_gtfs_routes') }}
