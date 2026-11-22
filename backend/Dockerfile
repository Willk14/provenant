# Provenant API and worker: one image, two commands.
#   API:     uvicorn provenant.api.main:app --host 0.0.0.0 --port 8000   (the default)
#   Worker:  python -m provenant.worker
#   Migrate: python -m provenant.api.migrate   (as the table owner, before a new version starts)
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Dependencies first, so a code change does not reinstall them.
COPY pyproject.toml ./
RUN mkdir provenant && touch provenant/__init__.py \
    && pip install ".[postgres,s3]" \
    && pip uninstall -y provenant && rm -rf provenant build

# Editable install from the source tree: the Alembic migrations folder ships with the code.
COPY provenant ./provenant
COPY scripts ./scripts
RUN pip install --no-deps -e .

RUN useradd --create-home --uid 10001 provenant \
    && mkdir -p /data/storage /data/cache && chown -R provenant /data
USER provenant

ENV PROVENANT_STORAGE_DIR=/data/storage \
    PROVENANT_CACHE_DIR=/data/cache

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4)"
CMD ["uvicorn", "provenant.api.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
