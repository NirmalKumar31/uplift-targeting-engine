-- Campaign economics by uplift segment and demographic slice.
select
    segment,
    age_band,
    gender,
    count(*) as n_clients,
    avg(expected_uplift) as avg_expected_uplift,
    avg(p_buy_without_sms) as avg_p_buy_without_sms,
    sum(expected_uplift) as expected_incremental_purchases,
    sum(expected_profit) as expected_profit_if_texted,
    avg((action = 'send')::int) as share_sent,
    avg(spend_total) as avg_spend_total
from {{ ref('dim_client') }}
group by all
