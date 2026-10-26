-- One row per client; behavioural features stay NULL for clients without purchases.
select
    d.*,
    r.* exclude (client_id),
    w.* exclude (client_id),
    pt.* exclude (client_id),
    h.* exclude (client_id),
    pm.* exclude (client_id)
from {{ ref('feat_demographics') }} d
left join {{ ref('feat_rfm') }} r using (client_id)
left join {{ ref('feat_windows') }} w using (client_id)
left join {{ ref('feat_points') }} pt using (client_id)
left join {{ ref('feat_habits') }} h using (client_id)
left join {{ ref('feat_product_mix') }} pm using (client_id)
