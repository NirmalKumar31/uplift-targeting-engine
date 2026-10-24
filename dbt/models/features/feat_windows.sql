{% set ref_date = "timestamp '" ~ var('reference_date') ~ "'" %}
-- Short-horizon activity and momentum: a client speeding up or slowing down before the SMS.
with tx as (
    select *, date_diff('day', transaction_at, {{ ref_date }}) as days_ago
    from {{ ref('stg_transactions') }}
)
select
    client_id,
    count(*) filter (where days_ago <= 7) as n_txn_7d,
    count(*) filter (where days_ago <= 14) as n_txn_14d,
    count(*) filter (where days_ago <= 30) as n_txn_30d,
    count(*) filter (where days_ago <= 60) as n_txn_60d,
    coalesce(sum(purchase_sum) filter (where days_ago <= 30), 0) as spend_30d,
    coalesce(sum(purchase_sum) filter (where days_ago <= 60), 0) as spend_60d,
    (count(*) filter (where days_ago <= 30) + 1.0)
        / (count(*) filter (where days_ago > 30 and days_ago <= 60) + 1.0) as txn_trend_30d,
    count(distinct date_trunc('week', transaction_at)) as active_weeks
from tx
group by client_id
