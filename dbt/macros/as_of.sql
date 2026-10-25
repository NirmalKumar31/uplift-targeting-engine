{# The feature cutoff timestamp. Every feature uses only rows strictly before it. #}
{% macro as_of() -%}
    timestamp '{{ var("as_of") }}'
{%- endmacro %}

{# Elapsed days (fractional) from timestamp a to timestamp b. Unlike date_diff('day', ...), which
   counts calendar boundaries, this is true elapsed time. #}
{% macro days_between(a, b) -%}
    (date_diff('second', {{ a }}, {{ b }}) / 86400.0)
{%- endmacro %}
