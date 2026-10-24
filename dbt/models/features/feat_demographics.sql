{% set ref_date = "timestamp '" ~ var('reference_date') ~ "'" %}
select
    client_id,
    age,
    gender,
    date_diff('day', first_issue_date, {{ ref_date }}) as card_tenure_days,
    first_redeem_date is not null as has_redeemed,
    date_diff('day', first_issue_date, first_redeem_date) as days_issue_to_redeem,
    date_diff('day', first_redeem_date, {{ ref_date }}) as days_since_first_redeem
from {{ ref('stg_clients') }}
