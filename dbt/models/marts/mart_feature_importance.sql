select * from {{ source('ml', 'feature_importance') }}
