select client_id, treatment_flg::int as treatment, target::int as target
from {{ source('raw', 'uplift_train') }}
