-- All purchase features read from here. Future rows already fail the source test.
select *
from {{ source('raw', 'purchases') }}
where transaction_datetime < {{ as_of() }}
