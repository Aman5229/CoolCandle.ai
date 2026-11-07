FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:0.13 /uv /bin/uv
WORKDIR /app
COPY agent/pyproject.toml agent/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY agent/ ./
RUN uv sync --frozen --no-dev
ENV PATH=/app/.venv/bin:$PATH
# The buddy's model loop only: no database, no broker. Compose publishes no port for it.
CMD ["uvicorn", "coolcandle_agent.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8100"]
