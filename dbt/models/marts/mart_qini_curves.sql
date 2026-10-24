-- Qini curves with the random-targeting line for each model/split, normalised to the population.
select
    model,
    split,
    fraction,
    incremental_buyers,
    fraction * last_value(incremental_buyers) over (
        partition by model, split order by fraction
        rows between unbounded preceding and unbounded following
    ) as random_line
from {{ source('ml', 'qini_curves') }}
