select * from {{ source('meta', 'runs') }} where release_id = '{{ var("release_id") }}'
