with store_counts as (
    select client_id, store_id, count(*) as n
    from {{ ref('stg_transactions') }}
    group by all
),
top_store as (
    select client_id, max(n) as top_store_txn
    from store_counts
    group by client_id
)
select
    t.client_id,
    count(*) as n_txn,
    {{ days_between('max(transaction_at)', as_of()) }} as recency_days,
    {{ days_between('min(transaction_at)', as_of()) }} as first_txn_days_ago,
    {{ days_between('min(transaction_at)', 'max(transaction_at)') }} / greatest(count(*) - 1, 1)
        as mean_gap_days,
    sum(purchase_sum) as spend_total,
    avg(purchase_sum) as basket_avg,
    median(purchase_sum) as basket_median,
    max(purchase_sum) as basket_max,
    stddev_samp(purchase_sum) / nullif(avg(purchase_sum), 0) as basket_cv,
    avg(n_lines) as lines_per_basket,
    avg(n_units) as units_per_basket,
    count(distinct t.store_id) as n_stores,
    any_value(ts.top_store_txn) / count(*) as top_store_share
from {{ ref('stg_transactions') }} t
join top_store ts using (client_id)
group by t.client_id
