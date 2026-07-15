FROM python:3.11-slim AS runtime

ARG INSTALL_AI=false
ARG PYTORCH_VERSION=2.11.0
ARG PYTORCH_VARIANT=cu130
ARG PYTORCH_INDEX_URL=https://download.pytorch.org/whl/cu130

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONSAFEPATH=1 \
    PYTHONPATH=/app/services/api \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    HOME=/home/campusvoice \
    CAMPUSVOICE_DATABASE_URL=sqlite+aiosqlite:////data/campusvoice.db \
    MODELSCOPE_CACHE=/models/modelscope \
    HF_HOME=/models/huggingface

RUN apt-get update \
    && apt-get install --no-install-recommends -y ffmpeg libsndfile1 \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system --gid 10001 campusvoice \
    && useradd --system --uid 10001 --gid campusvoice \
      --home-dir /home/campusvoice --create-home campusvoice

WORKDIR /app/services/api

COPY services/api/pyproject.toml ./pyproject.toml
COPY services/api/requirements/runtime.lock ./requirements/runtime.lock
COPY services/api/app ./app
COPY services/api/alembic ./alembic
COPY services/api/alembic.ini ./alembic.ini
COPY services/api/docker-entrypoint.sh ./docker-entrypoint.sh

RUN python -m pip install --no-cache-dir --require-hashes \
      --requirement ./requirements/runtime.lock \
    && if [ "$INSTALL_AI" = "true" ]; then \
      python -m pip install --no-cache-dir \
        "torch==${PYTORCH_VERSION}+${PYTORCH_VARIANT}" \
        "torchaudio==${PYTORCH_VERSION}+${PYTORCH_VARIANT}" \
        --index-url "$PYTORCH_INDEX_URL" \
        && python -m pip install --no-cache-dir ".[ai]"; \
    fi \
    && chmod +x ./docker-entrypoint.sh \
    && install -d -o campusvoice -g campusvoice \
      /data /models /models/modelscope /models/huggingface \
    && chown -R campusvoice:campusvoice /app /home/campusvoice

EXPOSE 8000

HEALTHCHECK --interval=10s --timeout=3s --start-period=20s --retries=5 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health/ready', timeout=2)"

USER campusvoice

WORKDIR /data

ENTRYPOINT ["/app/services/api/docker-entrypoint.sh"]
