-- Windows are [as_of - N days, as_of).
{% set w = {7: "7d", 14: "14d", 30: "30d", 60: "60d"} %}
with tx as (
    select * from {{ ref('stg_transactions') }} where transaction_at < {{ as_of() }}
)
select
    client_id,
    {%- for days, name in w.items() %}
    count(*) filter (where transaction_at >= {{ as_of() }} - interval {{ days }} day) as n_txn_{{ name }},
    {%- endfor %}
    coalesce(sum(purchase_sum) filter (where transaction_at >= {{ as_of() }} - interval 30 day), 0)
        as spend_30d,
    coalesce(sum(purchase_sum) filter (where transaction_at >= {{ as_of() }} - interval 60 day), 0)
        as spend_60d,
    (count(*) filter (where transaction_at >= {{ as_of() }} - interval 30 day) + 1.0)
        / (count(*) filter (where transaction_at >= {{ as_of() }} - interval 60 day
                             and transaction_at < {{ as_of() }} - interval 30 day) + 1.0)
        as txn_trend_30d,
    count(distinct date_trunc('week', transaction_at)) as active_weeks
from tx
group by client_id
