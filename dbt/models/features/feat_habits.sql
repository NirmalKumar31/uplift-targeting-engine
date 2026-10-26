select
    client_id,
    avg(hour(transaction_at)) as mean_hour,
    avg((hour(transaction_at) >= 18)::int) as share_evening,
    avg((isodow(transaction_at) >= 6)::int) as share_weekend
from {{ ref('stg_transactions') }}
group by client_id
