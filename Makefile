.PHONY: setup pipeline release-only dashboard api docs test test-lowest browser-check site lint docker clean

setup:            ## install everything (CPython 3.12 + uv 0.13.x)
	uv sync --locked --all-extras --group browser

pipeline:         ## ingest -> transform -> assess -> deploy -> historical -> explain -> marts -> release
	uv run ute run

release-only:     ## rebuild a release from the current feature build (no ingest/transform)
	uv run ute assess --new-release && uv run ute deploy && uv run ute historical \
	  && uv run ute explain && uv run ute marts && uv run ute release

site:             ## static snapshot + regenerated README results from the active release
	uv run python scripts/build_site.py && uv run python scripts/render_readme_results.py

dashboard:        ## BI dashboard on http://localhost:8501
	uv run streamlit run dashboard/app.py

DBT_VARS = '{"as_of": "2019-03-19 00:00:00", "release_id": "docs", "assess_run": "a", "deploy_run": "d", "explain_run": "e", "historical_run": "h", "has_historical": true}'

docs:             ## dbt lineage graph on http://localhost:8081
	uv run dbt docs generate --project-dir dbt --profiles-dir dbt --vars $(DBT_VARS)
	uv run dbt docs serve --project-dir dbt --profiles-dir dbt --port 8081

api:              ## API on http://localhost:8000/docs (needs an active release)
	uv run uvicorn uplift_engine.api:app --port 8000

test:
	uv run pytest --cov --cov-report=term-missing

test-lowest:      ## the suite on the lowest supported dependency versions
	uv run --isolated --resolution lowest-direct --all-extras --group dev pytest

browser-check:    ## real Chrome check of docs/index.html
	uv run --group browser python scripts/browser_check.py --site docs/index.html

lint:
	uv run ruff format --check . && uv run ruff check .

docker:           ## dashboard + API containers
	docker compose up --build

clean:
	rm -rf dbt/target dbt/logs data/warehouse.duckdb
