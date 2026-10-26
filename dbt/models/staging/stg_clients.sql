-- Ages outside 14-100 -> NULL. Redemptions after as_of or before card issue -> NULL
-- (post-SMS or impossible). Counts are in stg_data_quality.
select
    client_id,
    first_issue_date,
    case
        when first_redeem_date >= {{ as_of() }} then null
        when first_redeem_date < first_issue_date then null
        else first_redeem_date
    end as first_redeem_date,
    first_redeem_date < first_issue_date as redeem_chronology_invalid,
    case when age between 14 and 100 then age end as age,
    age is not null and age not between 14 and 100 as age_invalid,
    case when gender in ('F', 'M') then gender else 'U' end as gender
from {{ source('raw', 'clients') }}
