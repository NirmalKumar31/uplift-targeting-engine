{# Row-grain guard: every listed column takes exactly one value (nulls included) within each group.
   purchases.csv repeats transaction-level fields on every line item; staging collapses them with
   any_value(), which is only safe when this test passes. #}
{% test consistent_within(model, group_by, columns) %}
select {{ group_by | join(', ') }}
from {{ model }}
group by {{ group_by | join(', ') }}
having
{%- for c in columns %}
    count(distinct {{ c }}) + max(case when {{ c }} is null then 1 else 0 end) > 1
    {%- if not loop.last %} or{% endif %}
{%- endfor %}
{% endtest %}
