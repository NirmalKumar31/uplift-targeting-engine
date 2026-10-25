-- Loyalty-points behaviour. Express points come from promotions, so these columns are the
-- closest proxy for how a client has responded to past offers.
select
    client_id,
    sum(regular_points_received) as regular_points_received,
    sum(express_points_received) as express_points_received,
    sum(regular_points_spent) as regular_points_spent,
    sum(express_points_spent) as express_points_spent,
    avg((regular_points_spent > 0)::int) as share_txn_regular_spent,
    avg((express_points_received > 0)::int) as share_txn_express_received,
    avg((express_points_spent > 0)::int) as share_txn_express_spent,
    sum(regular_points_spent + express_points_spent)
        / nullif(sum(regular_points_received + express_points_received), 0) as points_burn_ratio,
    {{ days_between('max(transaction_at) filter (where express_points_spent > 0)', as_of()) }}
        as days_since_express_spent
from {{ ref('stg_transactions') }}
group by client_id
