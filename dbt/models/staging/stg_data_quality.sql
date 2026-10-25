-- Counts behind every cleaning rule, so no exclusion is silent.
select 'purchase lines at/after as_of (excluded; build fails first)' as check_name,
       count(*) filter (where transaction_datetime >= {{ as_of() }}) as n_rows
from {{ source('raw', 'purchases') }}
union all
select 'redemptions at/after as_of (set to unknown)',
       count(*) filter (where first_redeem_date >= {{ as_of() }})
from {{ source('raw', 'clients') }}
union all
select 'redemption before card issue (set to unknown, flagged)',
       count(*) filter (where first_redeem_date < first_issue_date)
from {{ source('raw', 'clients') }}
union all
select 'age outside 14-100 (set to unknown, flagged)',
       count(*) filter (where age is not null and age not between 14 and 100)
from {{ source('raw', 'clients') }}
union all
select 'age missing', count(*) filter (where age is null)
from {{ source('raw', 'clients') }}
union all
select 'gender missing or not F/M/U (mapped to U)', count(*) filter (where gender is null or gender not in ('F', 'M', 'U'))
from {{ source('raw', 'clients') }}
