# Dashboard by default; the API needs ./releases mounted (see docker-compose.yml).
FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 curl \
    && rm -rf /var/lib/apt/lists/*
COPY --from=ghcr.io/astral-sh/uv:0.13.5 /uv /bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
RUN uv sync --locked --no-dev --extra api --extra dashboard
COPY configs ./configs
COPY dashboard ./dashboard
COPY .streamlit ./.streamlit
COPY data/marts ./data/marts
ENV PATH="/app/.venv/bin:$PATH" UTE_ROOT=/app
EXPOSE 8000 8501
HEALTHCHECK CMD curl -fs http://localhost:8501/_stcore/health || curl -fs http://localhost:8000/health || exit 1
CMD ["streamlit", "run", "dashboard/app.py", "--server.address=0.0.0.0"]
