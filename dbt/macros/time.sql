{# All stored timestamps are naive UTC. These helpers keep time-zone logic in one place. #}

{% macro to_local(ts) -%}
    (({{ ts }}) AT TIME ZONE 'UTC') AT TIME ZONE '{{ var("local_tz") }}'
{%- endmacro %}

{# GTFS times are "HH:MM:SS" and may exceed 24:00 for trips that run past midnight. #}
{% macro gtfs_seconds(col) -%}
    (split_part({{ col }}, ':', 1)::INTEGER * 3600
     + split_part({{ col }}, ':', 2)::INTEGER * 60
     + split_part({{ col }}, ':', 3)::INTEGER)
{%- endmacro %}

{#
  GTFS defines stop times relative to "noon minus 12 hours" on the service date, not midnight.
  On DST-change days those differ by an hour, so anchor at local noon and subtract 12 hours.
  Returns naive UTC.
#}
{% macro gtfs_to_utc(service_date, seconds) -%}
    ((({{ service_date }})::TIMESTAMP + INTERVAL 12 HOUR) AT TIME ZONE '{{ var("local_tz") }}'
      - INTERVAL 12 HOUR
      + to_seconds({{ seconds }})) AT TIME ZONE 'UTC'
{%- endmacro %}

{% macro epoch_s(ts) -%} (epoch_us({{ ts }}) / 1e6) {%- endmacro %}
{% macro from_epoch_s(x) -%} make_timestamp(CAST(({{ x }}) * 1e6 AS BIGINT)) {%- endmacro %}

{% macro haversine_m(lat1, lon1, lat2, lon2) -%}
    (2 * 6371000 * asin(sqrt(
        power(sin(radians({{ lat2 }} - {{ lat1 }}) / 2), 2)
        + cos(radians({{ lat1 }})) * cos(radians({{ lat2 }})) * power(sin(radians({{ lon2 }} - {{ lon1 }}) / 2), 2)
    )))
{%- endmacro %}

{% macro time_period(hour_col) -%}
    CASE
        WHEN {{ hour_col }} BETWEEN 6 AND 8 THEN '1 AM peak (6-9)'
        WHEN {{ hour_col }} BETWEEN 9 AND 14 THEN '2 Midday (9-15)'
        WHEN {{ hour_col }} BETWEEN 15 AND 18 THEN '3 PM peak (15-19)'
        WHEN {{ hour_col }} BETWEEN 19 AND 22 THEN '4 Evening (19-23)'
        ELSE '5 Night (23-6)'
    END
{%- endmacro %}
