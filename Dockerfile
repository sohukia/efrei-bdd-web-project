FROM python:3.13-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    PYTHONUNBUFFERED=1

# Install dependencies first so this layer is cached across code changes
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY download_sources.py elt.py app.py ./
COPY sql/ sql/

ENV PATH="/app/.venv/bin:$PATH" \
    DATA_DIR=/data

EXPOSE 8501

# Service etl ; le service app surcharge avec `streamlit run app.py`
CMD ["python", "elt.py"]
