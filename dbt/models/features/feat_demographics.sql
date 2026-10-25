-- Demographics and card history as of the cutoff. Unknown dates stay NULL (never imputed here).
select
    client_id,
    age,
    gender,
    {{ days_between('first_issue_date', as_of()) }} as card_tenure_days,
    first_redeem_date is not null as has_redeemed,
    {{ days_between('first_issue_date', 'first_redeem_date') }} as days_issue_to_redeem,
    {{ days_between('first_redeem_date', as_of()) }} as days_since_first_redeem,
    coalesce(redeem_chronology_invalid, false) as redeem_date_invalid
from {{ ref('stg_clients') }}
