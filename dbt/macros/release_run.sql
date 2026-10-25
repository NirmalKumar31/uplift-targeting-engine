{# run_id of the latest successful run of `stage` in the release being built (var release_id). #}
{% macro release_run(stage) -%}
    (select run_id from {{ source('meta', 'runs') }}
     where release_id = '{{ var("release_id") }}' and stage = '{{ stage }}'
       and status = 'succeeded'
     order by started_at desc limit 1)
{%- endmacro %}

{% macro from_run(table, stage) -%}
    select * exclude (run_id) from {{ source('ml', table) }} where run_id = {{ release_run(stage) }}
{%- endmacro %}
