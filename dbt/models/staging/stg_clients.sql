{% set ref_date = "timestamp '" ~ var('reference_date') ~ "'" %}
-- One row per loyalty-card holder.
-- * Implausible ages become NULL (the raw file has ages like -7 and 1901).
-- * first_redeem_date runs to Nov 2019, months after the SMS. A redemption after the reference
--   date is post-treatment information (the SMS itself can trigger it), so it is dropped:
--   as of the reference date that client had not redeemed yet.
select
    client_id,
    first_issue_date,
    case when first_redeem_date < {{ ref_date }} then first_redeem_date end as first_redeem_date,
    case when age between 14 and 100 then age end as age,
    case when gender in ('F', 'M') then gender else 'U' end as gender
from {{ source('raw', 'clients') }}
