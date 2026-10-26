-- One row per transaction; consistent_within guarantees any_value() is safe.
select
    client_id,
    transaction_id,
    any_value(transaction_datetime) as transaction_at,
    any_value(store_id) as store_id,
    any_value(purchase_sum) as purchase_sum,
    any_value(regular_points_received) as regular_points_received,
    any_value(express_points_received) as express_points_received,
    abs(any_value(regular_points_spent)) as regular_points_spent,
    abs(any_value(express_points_spent)) as express_points_spent,
    count(*) as n_lines,
    sum(product_quantity) as n_units
from {{ ref('stg_purchase_lines') }}
group by client_id, transaction_id
