#!/usr/bin/env bash
# Build the study-interface image in ACR from a MINIMAL staging context.
#
# Why: `az acr build` from the repo root packs the entire tree (~46 GiB with
# tsv_generation/ and the agent's indexes/) regardless of .dockerignore, and
# the registry times out downloading the context. The interface image only
# needs study/ + requirements-interface.txt (~160 MB), so we stage exactly
# that, mirroring the repo layout the Dockerfile expects.
#
# Usage: bash study/interface/build_image.sh [tag]   (default tag: latest)
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
TAG="${1:-latest}"
ACR="${ACR:-azacrspzdzrbtv3v4o}"
CTX="$(mktemp -d "${TMPDIR:-/tmp}/derad-iface-ctx.XXXXXX")"
trap 'rm -rf "$CTX"' EXIT

echo "staging context in $CTX …"
cp "$ROOT/requirements-interface.txt" "$CTX/"
rsync -a --exclude='__pycache__' --exclude='.pytest_cache' \
      --exclude='paper' --exclude='data_analysis' \
      "$ROOT/study/" "$CTX/study/"
du -sh "$CTX"

az acr build --registry "$ACR" \
  --image "derad-study-interface:$TAG" \
  --file study/interface/Dockerfile "$CTX"
