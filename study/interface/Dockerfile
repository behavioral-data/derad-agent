# syntax=docker/dockerfile:1.6
# Mock-X study interface image (see docs/interface_azure_deployment.md §3).
# Build from the REPO ROOT so study/ is in context:
#   az acr build -r azacrspzdzrbtv3v4o -t derad-study-interface:latest \
#       -f study/interface/Dockerfile .
FROM python:3.11-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PORT=8000
WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends curl \
 && rm -rf /var/lib/apt/lists/*

COPY requirements-interface.txt .
RUN pip install --no-cache-dir -r requirements-interface.txt

# study/ is a package (study/__init__.py, study/interface/__init__.py). Copy the
# interface code + data (posts/notes/media_index/replies CSVs + media/, ~160 MB,
# and the committed participant-profile pool study/data/profiles/).
COPY study/ ./study/

# Build the read-only study.db at image-build time (creates posts, interventions,
# and the access-code table). Requires the committed CSVs + media to be present.
RUN python -m study.interface.build_db \
      --selected study/data/posts.csv \
      --notes    study/data/notes.csv \
      --media    study/data/media_index.csv \
      --replies  study/data/replies.csv \
      --db       study/data/study.db

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl --fail --silent http://localhost:${PORT}/healthz || exit 1

# Prod uses the Azure Tables store (shared across workers), so >1 worker is safe.
# gunicorn app-factory syntax calls create_app().
CMD ["gunicorn", "study.interface.server:create_app()", \
     "--workers", "2", "--threads", "4", "--timeout", "60", \
     "--bind", "0.0.0.0:8000", "--access-logfile", "-", "--error-logfile", "-"]
