{# Marts read exactly the producer runs that `ute marts` passes as vars (<stage>_run), the same
   ids recorded in PRODUCERS.json and in the marts run's parent_run_ids. #}
{% macro release_run(stage) -%}
    '{{ var(stage ~ "_run") }}'
{%- endmacro %}

{% macro from_run(table, stage) -%}
    select * exclude (run_id) from {{ source('ml', table) }} where run_id = {{ release_run(stage) }}
{%- endmacro %}
