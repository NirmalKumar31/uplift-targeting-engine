-- Scored population by predicted-uplift decile (1 = most persuadable).
select
    uplift_decile,
    count(*) as n_clients,
    avg(expected_uplift) as avg_expected_uplift,
    min(uplift_score) as min_score,
    max(uplift_score) as max_score,
    avg(p_buy_without_sms) as avg_p_buy_without_sms,
    sum(expected_uplift) as expected_incremental_purchases,
    sum(expected_profit) as expected_profit_if_texted,
    avg((action = 'send')::int) as share_sent,
    avg(age) as avg_age,
    avg(n_txn) as avg_visits,
    avg(basket_avg) as avg_basket
from {{ ref('dim_client') }}
group by uplift_decile
order by uplift_decile
