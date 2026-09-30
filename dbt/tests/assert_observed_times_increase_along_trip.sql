-- Interpolated arrivals must never go backwards in time along a single trip instance.
select instance_id, stop_index
from (
    select
        instance_id,
        stop_index,
        observed_utc < lag(observed_utc) over (partition by instance_id order by stop_index) as went_back
    from {{ ref('int_stop_events') }}
)
where went_back
