-- The randomised SMS experiment: treatment_flg = received the SMS, target = purchased afterwards.
select client_id, treatment_flg::int as treatment, target::int as target
from {{ source('raw', 'uplift_train') }}
