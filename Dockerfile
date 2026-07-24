FROM python:3.12-slim

# uv ставим из официального образа, версия запинена для воспроизводимости.
COPY --from=ghcr.io/astral-sh/uv:0.11.24 /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Зависимости ставим отдельным слоём, чтобы правки кода не пересобирали их каждый раз.
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --no-dev

COPY app ./app

# Непривилегированный пользователь и каталог данных под него.
RUN useradd --create-home app \
    && mkdir -p /app/data \
    && chown -R app:app /app
USER app

ENV PATH="/app/.venv/bin:$PATH"
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
