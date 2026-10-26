{# Marts read the exact producer run ids passed by `ute marts`. #}
{% macro release_run(stage) -%}
    '{{ var(stage ~ "_run") }}'
{%- endmacro %}

{% macro from_run(table, stage) -%}
    select * exclude (run_id) from {{ source('ml', table) }} where run_id = {{ release_run(stage) }}
{%- endmacro %}
