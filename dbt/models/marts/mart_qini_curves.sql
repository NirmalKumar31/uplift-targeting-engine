with curves as (
    {{ from_run('qini_curves', 'deploy') }}
    {% if var('has_historical', false) %}
    union all
    {{ from_run('qini_curves', 'historical') }}
    {% endif %}
)
select
    *,
    fraction * last_value(incremental_buyers) over (
        partition by model, source order by fraction
        rows between unbounded preceding and unbounded following
    ) as random_line
from curves
