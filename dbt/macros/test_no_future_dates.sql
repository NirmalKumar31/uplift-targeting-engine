{# Leakage guard: no feature input may be dated on or after the reference date (the SMS). #}
{% test no_future_dates(model, columns) %}
select *
from {{ model }}
where {% for c in columns %}{{ c }} >= timestamp '{{ var("reference_date") }}'{% if not loop.last %} or {% endif %}{% endfor %}
{% endtest %}
