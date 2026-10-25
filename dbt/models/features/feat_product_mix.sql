-- What a client buys, from pre-cutoff line items joined to the product catalogue.
-- level_1 has three top-level departments; spend shares use trn_sum_from_iss (line value).
with lines as (
    select p.client_id, p.product_id, p.product_quantity, p.trn_sum_from_iss as line_sum,
           pr.level_1, pr.level_3, pr.is_own_trademark, pr.is_alcohol
    from {{ ref('stg_purchase_lines') }} p
    left join {{ source('raw', 'products') }} pr using (product_id)
)
select
    client_id,
    count(distinct product_id) as n_distinct_products,
    count(distinct level_3) as n_distinct_categories,
    sum(line_sum * is_own_trademark) / nullif(sum(line_sum), 0) as share_own_brand,
    sum(line_sum * is_alcohol) / nullif(sum(line_sum), 0) as share_alcohol,
    sum(line_sum) filter (where level_1 = 'e344ab2e71') / nullif(sum(line_sum), 0) as share_dept_a,
    sum(line_sum) filter (where level_1 = 'c3d3a8e8c6') / nullif(sum(line_sum), 0) as share_dept_b,
    sum(line_sum) filter (where level_1 = 'ec62ce61e3') / nullif(sum(line_sum), 0) as share_dept_c,
    sum(line_sum) / nullif(sum(product_quantity), 0) as avg_unit_price
from lines
group by client_id
