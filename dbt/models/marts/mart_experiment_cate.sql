-- Observed treatment effect in the randomised experiment, sliced by descriptive bands.
-- Raw differences in means, no model: the ground truth the model is trying to rank.
with exp as (
    select * from {{ ref('dim_client') }} where in_experiment
),
slices as (
    select 'Overall' as dimension, 'All clients' as level, * from exp
    union all select 'Age band', age_band, * from exp
    union all select 'Gender', gender, * from exp
    union all select 'Card tenure', tenure_band, * from exp
    union all select 'Visit frequency', frequency_band, * from exp
    -- out-of-fold decile: the production model saw these clients, so its own deciles would flatter it
    union all select 'Predicted decile', 'Decile ' || lpad(oof_decile::varchar, 2, '0'), * from exp
)
select
    dimension,
    level,
    count(*) as n_clients,
    avg(target) filter (where treatment = 1) as rate_sms,
    avg(target) filter (where treatment = 0) as rate_no_sms,
    avg(target) filter (where treatment = 1) - avg(target) filter (where treatment = 0) as observed_uplift,
    sqrt(
        var_samp(target) filter (where treatment = 1) / count(*) filter (where treatment = 1)
        + var_samp(target) filter (where treatment = 0) / count(*) filter (where treatment = 0)
    ) as std_error
from slices
group by all
