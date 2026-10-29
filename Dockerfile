# MBPE live system image (paper trading only). CPU inference; FinBERT/MiniLM weights download on first use
# into the mounted cache volume. Build: docker compose build
FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 git && rm -rf /var/lib/apt/lists/*
COPY --from=ghcr.io/astral-sh/uv:0.11.32 /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY . .
RUN uv sync --frozen --no-dev
ENV HF_HOME=/cache/huggingface PYTHONUNBUFFERED=1
