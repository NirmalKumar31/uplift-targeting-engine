{# No timestamp on or after the as_of cutoff. #}
{% test no_future_dates(model, column_name=none, columns=none) %}
{% set cols = columns if columns else [column_name] %}
select *
from {{ model }}
where {% for c in cols %}{{ c }} >= {{ as_of() }}{% if not loop.last %} or {% endif %}{% endfor %}
{% endtest %}
