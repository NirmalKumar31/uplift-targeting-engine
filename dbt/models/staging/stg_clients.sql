-- One row per loyalty-card holder. Cleaning rules (counted in stg_data_quality):
-- * age outside 14-100 is implausible (raw values include -7 and 1901) -> NULL (unknown).
-- * first_redeem_date at/after the as_of cutoff is post-treatment information (dates run to
--   Nov 2019 and the SMS itself can trigger a redemption) -> NULL: not redeemed as of the cutoff.
-- * first_redeem_date before first_issue_date is impossible -> NULL and flagged; the client's
--   redemption date is unknown, not invented.
-- * first_issue_date at/after the cutoff fails the build (source test).
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
