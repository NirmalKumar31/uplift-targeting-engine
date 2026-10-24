{# Minimal stand-in for dbt_utils.unique_combination_of_columns, so the project needs no packages. #}
{% test dbt_utils_unique_combination(model, columns) %}
select {{ columns | join(', ') }}
from {{ model }}
group by {{ columns | join(', ') }}
having count(*) > 1
{% endtest %}
