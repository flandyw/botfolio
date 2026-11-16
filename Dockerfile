FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.5.11 /uv /uvx /usr/local/bin/

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    TTBOT_DB_PATH=/app/data/ttbot.sqlite \
    TTBOT_CONFIG=/app/config/default.yaml \
    PATH="/app/.venv/bin:$PATH" \
    TZ=UTC

WORKDIR /app
RUN useradd --create-home --uid 1000 ttbot

# dependencies first (cached layer), then the project itself
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project
COPY src ./src
COPY config ./config
COPY dashboard ./dashboard
RUN uv sync --frozen --no-dev && mkdir -p /app/data && chown -R ttbot:ttbot /app

USER ttbot
VOLUME ["/app/data"]
CMD ["ttbot", "forward"]
