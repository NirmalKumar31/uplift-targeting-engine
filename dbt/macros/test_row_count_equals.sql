{% test row_count_equals(model, n) %}
select count(*) as n_rows from {{ model }} having count(*) <> {{ n }}
{% endtest %}
