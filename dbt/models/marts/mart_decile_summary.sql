select
    uplift_decile,
    count(*) as n_clients,
    avg(calibrated_effect) as avg_calibrated_effect,
    avg(p_buy_without_sms) as avg_p_buy_without_sms,
    avg(economically_eligible::int) as share_eligible,
    count(*) filter (where action = 'send') as n_send,
    count(*) filter (where action = 'holdout') as n_holdout,
    sum(calibrated_effect) filter (where action = 'send') as projected_additional_buyers_sent,
    sum(expected_value_if_texted) filter (where action = 'send') as projected_value_sent,
    avg(age) as avg_age,
    avg(n_txn) as avg_visits,
    avg(basket_avg) as avg_basket
from {{ ref('dim_client') }}
group by uplift_decile
order by uplift_decile
