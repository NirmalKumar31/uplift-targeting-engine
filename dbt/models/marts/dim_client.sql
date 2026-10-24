-- Customer 360: every card holder with descriptive bands, model scores, segment and campaign action.
select
    f.client_id,
    f.gender,
    f.age,
    case
        when f.age is null then 'Unknown'
        when f.age < 25 then '18-24'
        when f.age < 35 then '25-34'
        when f.age < 45 then '35-44'
        when f.age < 55 then '45-54'
        when f.age < 65 then '55-64'
        else '65+'
    end as age_band,
    case
        when f.card_tenure_days < 180 then '< 6 months'
        when f.card_tenure_days < 365 then '6-12 months'
        when f.card_tenure_days < 540 then '12-18 months'
        else '18+ months'
    end as tenure_band,
    case
        when f.n_txn < 5 then '1-4 visits'
        when f.n_txn < 15 then '5-14 visits'
        when f.n_txn < 30 then '15-29 visits'
        else '30+ visits'
    end as frequency_band,
    f.n_txn,
    f.recency_days,
    f.spend_total,
    f.basket_avg,
    f.express_points_spent,
    s.uplift_score,
    s.expected_uplift,
    s.p_buy_without_sms,
    s.p_buy_with_sms,
    s.rank as uplift_rank,
    s.decile as uplift_decile,
    s.oof_decile,
    s.expected_profit,
    s.segment,
    s.action,
    s.in_experiment,
    e.treatment,
    e.target
from {{ ref('client_features') }} f
join {{ source('ml', 'scores') }} s using (client_id)
left join {{ ref('stg_experiment') }} e using (client_id)
