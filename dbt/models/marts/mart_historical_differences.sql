{{ config(enabled=var('has_historical', false)) }}
{{ from_run('historical_differences', 'historical') }}
