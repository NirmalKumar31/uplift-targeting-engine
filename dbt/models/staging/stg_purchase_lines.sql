-- The single point-in-time purchase input: every purchase feature reads from here.
-- Rows at/after the cutoff already fail the source test; the filter is defence in depth.
select *
from {{ source('raw', 'purchases') }}
where transaction_datetime < {{ as_of() }}
