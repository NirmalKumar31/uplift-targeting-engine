{% test disjoint(model, column_name, other, other_column) %}
select {{ column_name }} from {{ model }}
intersect
select {{ other_column }} from {{ other }}
{% endtest %}
