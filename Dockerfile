# syntax=docker/dockerfile:1.6
# ---------------------------------------------------------------------------
# Builder stage — install third-party deps and our package as a wheel into a
# self-contained venv. Non-editable so /opt/venv has no stale references to
# the build-time source path.
# ---------------------------------------------------------------------------
FROM python:3.11-slim AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /build

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:${PATH}"

# Layer 1: third-party deps — cached across source edits.
COPY requirements.txt ./
RUN pip install -r requirements.txt

# Layer 2: our package, built into a wheel and installed (no editable .pth
# files referencing /build). package-data in pyproject.toml ships templates
# and static into the wheel so the runtime stage doesn't need the source tree.
COPY pyproject.toml README.md ./
COPY agent ./agent
RUN pip install --no-deps .

# ---------------------------------------------------------------------------
# Runtime stage — slim base, non-root user, baked-in notes index.
# ---------------------------------------------------------------------------
FROM python:3.11-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:${PATH}" \
    PORT=8000 \
    DERAD_AGENT_INDEX_ROOT=/app/indexes

RUN apt-get update \
 && apt-get install -y --no-install-recommends curl \
 && rm -rf /var/lib/apt/lists/*

RUN useradd --create-home --shell /usr/sbin/nologin --uid 10001 app

WORKDIR /app

# Bring across the installed venv (carries agent + templates + static).
COPY --from=builder --chown=app:app /opt/venv /opt/venv

# Bake the notes index into the image. ~1.34 GB apparent size, ~1.8 GB final
# image; trades image size for zero external mounts and a one-step cold start.
COPY --chown=app:app indexes/notes_index /app/indexes/notes_index

USER app

EXPOSE 8000

# App Service has its own health check at /healthz; this Docker-level probe
# is for local dev / Container Apps.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl --fail --silent http://localhost:${PORT}/healthz || exit 1

# Single worker on purpose — the 1.2 GB embedding matrix is per-process.
# gthread releases the GIL during LLM / HTTP waits, which is where the time
# goes. --graceful-timeout 20 gives non-daemon pipeline threads room to drain
# on SIGTERM so we don't lose mentions we've already claimed in the dedup
# store.
CMD ["gunicorn", \
     "--workers", "1", \
     "--worker-class", "gthread", \
     "--threads", "8", \
     "--timeout", "300", \
     "--graceful-timeout", "60", \
     "--bind", "0.0.0.0:8000", \
     "--access-logfile", "-", \
     "--error-logfile", "-", \
     "agent.app.app:app"]
