FROM python:3.11-slim AS runtime

ARG INSTALL_AI=false
ARG PYTORCH_VERSION=2.11.0
ARG PYTORCH_VARIANT=cu130
ARG PYTORCH_INDEX_URL=https://download.pytorch.org/whl/cu130

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    MODELSCOPE_CACHE=/models/modelscope \
    HF_HOME=/models/huggingface

RUN apt-get update \
    && apt-get install --no-install-recommends -y ffmpeg libsndfile1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app/services/api

COPY services/api/pyproject.toml ./pyproject.toml
COPY services/api/app ./app
COPY services/api/alembic ./alembic
COPY services/api/alembic.ini ./alembic.ini
COPY services/api/docker-entrypoint.sh ./docker-entrypoint.sh

RUN if [ "$INSTALL_AI" = "true" ]; then \
      python -m pip install --no-cache-dir \
        "torch==${PYTORCH_VERSION}+${PYTORCH_VARIANT}" \
        "torchaudio==${PYTORCH_VERSION}+${PYTORCH_VARIANT}" \
        --index-url "$PYTORCH_INDEX_URL" \
        && python -m pip install --no-cache-dir ".[ai]"; \
    else \
      python -m pip install --no-cache-dir .; \
    fi \
    && chmod +x ./docker-entrypoint.sh \
    && mkdir -p /data /models/modelscope /models/huggingface

EXPOSE 8000

HEALTHCHECK --interval=10s --timeout=3s --start-period=20s --retries=5 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2)"

ENTRYPOINT ["./docker-entrypoint.sh"]
