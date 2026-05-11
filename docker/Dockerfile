# syntax=docker/dockerfile:1.7
#
# kripto_bot — ICT scalping signal bot (paper trading)
# Single-stage image, uv-based deps install. Python 3.13 slim base.
#
# Build context = repo root.
# See docker-compose.yml at repo root: `context: .` + `dockerfile: docker/Dockerfile`.
# Ignore rules: docker/Dockerfile.dockerignore (BuildKit picks it up automatically).

FROM python:3.13-slim

# uv binary'sini /bin/'e kopyala
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    PATH="/app/.venv/bin:${PATH}"

WORKDIR /app

# cffi/cryptography gibi native build edenler için
RUN apt-get update \
 && apt-get install -y --no-install-recommends gcc libffi-dev curl \
 && rm -rf /var/lib/apt/lists/*

# Proje dosyaları (src ve config dahil — editable install için src lazım)
COPY pyproject.toml README.md ./
COPY src ./src
COPY config ./config
COPY scripts ./scripts
COPY tests ./tests

# Sanal ortam + bağımlılıklar (uv cache mount)
RUN --mount=type=cache,target=/root/.cache/uv \
    uv venv && uv pip install -e ".[dev]"

# Runtime için yazılabilir dizinler (compose volume mount edecek)
RUN mkdir -p /app/data /app/logs

# Hafif healthcheck — config yüklenebiliyor mu
HEALTHCHECK --interval=60s --timeout=10s --start-period=20s --retries=3 \
    CMD python -m src.main check > /dev/null 2>&1 || exit 1

# Default komut: canlı (paper) bot
CMD ["python", "-m", "src.main", "run"]
