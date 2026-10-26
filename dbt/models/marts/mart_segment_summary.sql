select
    predicted_group,
    age_band,
    gender,
    count(*) as n_clients,
    avg(calibrated_effect) as avg_calibrated_effect,
    avg(p_buy_without_sms) as avg_p_buy_without_sms,
    count(*) filter (where action = 'send') as n_send,
    count(*) filter (where action = 'holdout') as n_holdout,
    sum(calibrated_effect) filter (where action = 'send') as projected_additional_buyers_sent,
    sum(expected_value_if_texted) filter (where action = 'send') as projected_value_sent
from {{ ref('dim_client') }}
group by all
