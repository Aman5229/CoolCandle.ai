FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:0.13 /uv /bin/uv
WORKDIR /app
COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY backend/ ./
RUN uv sync --frozen --no-dev
ENV PATH=/app/.venv/bin:$PATH
# One worker, always: two instances would end each other's broker session.
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn coolcandle.main:create_app --factory --host 0.0.0.0 --port 8000 --workers 1"]
