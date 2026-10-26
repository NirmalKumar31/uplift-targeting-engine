-- Observed uplift by slice. Decile uses out-of-fold scores.
with exp as (
    select * from {{ ref('dim_client') }} where in_experiment
),
slices as (
    select 'Overall' as dimension, 'All clients' as level, * from exp
    union all select 'Age band', age_band, * from exp
    union all select 'Gender', gender, * from exp
    union all select 'Card tenure', tenure_band, * from exp
    union all select 'Visit frequency', frequency_band, * from exp
    union all select 'Predicted decile (OOF)', 'Decile ' || lpad(oof_decile::varchar, 2, '0'), * from exp
)
select
    dimension,
    level,
    count(*) as n_clients,
    count(*) filter (where treatment = 1) as n_treated,
    count(*) filter (where treatment = 0) as n_control,
    avg(target) filter (where treatment = 1) as rate_sms,
    avg(target) filter (where treatment = 0) as rate_no_sms,
    avg(target) filter (where treatment = 1) - avg(target) filter (where treatment = 0) as observed_uplift,
    sqrt(
        var_samp(target) filter (where treatment = 1) / count(*) filter (where treatment = 1)
        + var_samp(target) filter (where treatment = 0) / count(*) filter (where treatment = 0)
    ) as std_error
from slices
group by all
