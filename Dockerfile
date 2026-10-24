FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 && rm -rf /var/lib/apt/lists/*
COPY --from=ghcr.io/astral-sh/uv:0.5 /uv /bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
RUN uv sync --frozen --no-dev --extra api --extra dashboard
COPY configs ./configs
COPY dashboard ./dashboard
COPY data/marts ./data/marts
ENV PATH="/app/.venv/bin:$PATH"
EXPOSE 8000 8501
CMD ["streamlit", "run", "dashboard/app.py", "--server.address=0.0.0.0"]
