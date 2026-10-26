{% macro as_of() -%}
    timestamp '{{ var("as_of") }}'
{%- endmacro %}

{# Elapsed days, not calendar-day boundaries. #}
{% macro days_between(a, b) -%}
    (date_diff('second', {{ a }}, {{ b }}) / 86400.0)
{%- endmacro %}
