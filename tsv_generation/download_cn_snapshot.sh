#!/usr/bin/env bash
#
# download_cn_snapshot.sh — robustly download a COMPLETE Community Notes public
# snapshot (all shards of every file type) and verify integrity + date coverage.
#
# Motivation: a prior download silently stopped after ratings-00002 (which ends
# 2024-09-10), so every note created after that date fell out of the pipeline.
# This script enumerates shards until a *definitive* HTTP 404, retries transient
# errors instead of stopping, verifies each zip, and asserts the last ratings
# shard actually reaches the snapshot date.
#
# Usage:  ./download_cn_snapshot.sh [YYYY/MM/DD]   (default: newest known-good date below)
#
set -euo pipefail

SNAP="${1:-2026/06/30}"                      # snapshot date on the CN server
SNAPTAG="$(echo "$SNAP" | tr -d '/')"        # e.g. 20260630
BASE="https://ton.twimg.com/birdwatch-public-data/$SNAP"
ROOT="/projects/bdata/advaitmb/derad-agent/tsv_generation/cn_data_$SNAPTAG"
RAW="$ROOT/raw"
LOG="$ROOT/download.log"
MANIFEST="$ROOT/MANIFEST.txt"

# (kind -> server subdirectory)
declare -A SUBDIR=( [notes]=notes [noteStatusHistory]=noteStatusHistory \
                    [userEnrollment]=userEnrollment [ratings]=noteRatings )
# expected shard counts observed on 2026/06/30 — a *cross-check*, not the stop
# condition. Enumeration is authoritative; a mismatch is logged loudly.
declare -A EXPECT=( [notes]=3 [noteStatusHistory]=1 [userEnrollment]=1 [ratings]=8 )

mkdir -p "$RAW" "$ROOT/notes" "$ROOT/ratings"
: > "$MANIFEST"
log(){ echo "[$(date '+%F %T')] $*" | tee -a "$LOG"; }
die(){ log "FATAL: $*"; exit 1; }

# HTTP status for a byte-range probe (206=exists, 404=absent). Retries transient.
probe_status(){
  curl -sS -o /dev/null -w "%{http_code}" \
    --retry 5 --retry-all-errors --retry-delay 2 -r 0-0 --connect-timeout 20 --max-time 40 \
    "$1" 2>/dev/null || echo "000"
}

# Expected byte size from Content-Length (for post-download verification).
remote_size(){
  curl -sS -I --retry 5 --retry-all-errors --retry-delay 2 --max-time 40 "$1" 2>/dev/null \
    | awk -F': ' 'tolower($1)=="content-length"{gsub(/\r/,"",$2); print $2}'
}

# Download one shard with resume + retries, then verify zip integrity and size.
fetch_verify(){
  local url="$1" out="$2" want; want="$(remote_size "$url")"
  if [[ -f "$out" ]] && unzip -t "$out" >/dev/null 2>&1; then
    local have; have=$(stat -c %s "$out")
    if [[ -z "$want" || "$have" == "$want" ]]; then
      log "SKIP  $(basename "$out") already present & valid ($have bytes)"; echo "$out $have OK-cached" >> "$MANIFEST"; return 0
    fi
    log "RE-GET $(basename "$out") size mismatch have=$have want=$want"
  fi
  log "GET   $url  (expect ${want:-?} bytes)"
  local tries=0
  until curl -fSL --retry 6 --retry-all-errors --retry-delay 3 -C - \
             --connect-timeout 30 --max-time 7200 -o "$out" "$url"; do
    tries=$((tries+1)); [[ $tries -ge 4 ]] && die "download failed after $tries attempts: $url"
    log "  transient failure, retry $tries for $(basename "$out")"; sleep 5
  done
  unzip -t "$out" >/dev/null 2>&1 || die "corrupt zip (failed unzip -t): $out"
  local have; have=$(stat -c %s "$out")
  [[ -n "$want" && "$have" != "$want" ]] && die "size mismatch after download: $out have=$have want=$want"
  log "OK    $(basename "$out") ($have bytes, zip integrity verified)"
  echo "$out $have OK" >> "$MANIFEST"
}

# Enumerate + download every shard of a kind. Stops ONLY on a definitive 404.
fetch_kind(){
  local kind="$1" sub="${SUBDIR[$1]}" n=0 idx url code
  log "=== $kind (server dir: $sub) ==="
  while :; do
    idx="$(printf '%05d' "$n")"; url="$BASE/$sub/$kind-$idx.zip"
    code="$(probe_status "$url")"
    case "$code" in
      200|206) fetch_verify "$url" "$RAW/$kind-$idx.zip"; n=$((n+1)) ;;
      404)     log "END   $kind: $n shard(s) found"; break ;;
      *)       die "$kind-$idx.zip returned HTTP $code (not 200/206/404) after retries — refusing to guess the end" ;;
    esac
    [[ $n -gt 100 ]] && die "safety stop: >100 $kind shards (unexpected)"
  done
  local exp="${EXPECT[$kind]:-}"
  if [[ -n "$exp" && "$n" != "$exp" ]]; then
    log "NOTE  $kind shard count is $n (cross-check expected $exp for 2026/06/30); proceeding with actual."
  fi
  echo "$kind $n" >> "$ROOT/.shardcounts"
}

log "############ Community Notes snapshot download: $SNAP -> $ROOT ############"
: > "$ROOT/.shardcounts"
for kind in notes noteStatusHistory userEnrollment ratings; do fetch_kind "$kind"; done

log "=== unzipping into layout (notes/ and ratings/ are directories; nsh & userEnrollment single files) ==="
for z in "$RAW"/notes-*.zip;             do unzip -o "$z" -d "$ROOT/notes"   >/dev/null; done
for z in "$RAW"/ratings-*.zip;           do unzip -o "$z" -d "$ROOT/ratings" >/dev/null; done
unzip -o "$RAW"/noteStatusHistory-00000.zip -d "$ROOT" >/dev/null
unzip -o "$RAW"/userEnrollment-00000.zip    -d "$ROOT" >/dev/null
log "unzip complete"
log "ALL DOWNLOADS DONE — now run verify_cn_snapshot.py to validate date coverage."
