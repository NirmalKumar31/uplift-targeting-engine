.PHONY: setup data pipeline train dashboard api test lint docker clean

setup:            ## install everything (python 3.12 + uv)
	uv sync --all-extras

data:             ## download + checksum + land raw files, build the feature store
	uv run ute ingest
	uv run ute transform

pipeline:         ## full run: ingest -> dbt features -> train -> test eval -> deploy -> explain -> dbt marts
	uv run ute run

train:            ## re-run modelling only (features already built)
	uv run ute train && uv run ute evaluate && uv run ute deploy && uv run ute explain && uv run ute marts

dashboard:        ## BI dashboard on http://localhost:8501
	uv run streamlit run dashboard/app.py

api:              ## scoring API on http://localhost:8000/docs
	uv run uvicorn uplift_engine.api:app --port 8000

test:
	uv run pytest --cov --cov-report=term-missing

lint:
	uv run ruff format --check . && uv run ruff check .

docker:           ## dashboard + API containers
	docker compose up --build

clean:
	rm -rf artifacts dbt/target dbt/logs data/warehouse.duckdb
