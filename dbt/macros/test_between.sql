{% test dbt_utils_between(model, column_name, low, high) %}
select {{ column_name }}
from {{ model }}
where {{ column_name }} < {{ low }} - 1e-9 or {{ column_name }} > {{ high }} + 1e-9
{% endtest %}
