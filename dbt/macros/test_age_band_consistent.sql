{% test age_band_consistent(model) %}
select client_id, age, age_band from {{ model }}
where not (
       (age_band = 'Unknown' and age is null)
    or (age_band = '14-17' and age between 14 and 17)
    or (age_band = '18-24' and age between 18 and 24)
    or (age_band = '25-34' and age between 25 and 34)
    or (age_band = '35-44' and age between 35 and 44)
    or (age_band = '45-54' and age between 45 and 54)
    or (age_band = '55-64' and age between 55 and 64)
    or (age_band = '65+' and age >= 65)
)
{% endtest %}
