#!/usr/bin/env bash
# Quick start: build the mock-X study database (study/interface/study.db).
#
# Fast path (default): the committed data artifacts (selected_posts.csv,
# notes_selected.csv, media_index.csv + static/media/) are already in the repo,
# so this just runs build_db — no network, no X credentials, a second or two.
#
#   bash study/interface/setup_db.sh
#
# Full rebuild (only if the post set changed): re-runs the heavy one-time steps
# first. extract_notes needs the ~1.4 GB Community Notes dumps under
# tsv_generation/cn_data/; fetch_media needs X API creds in agent/llm/.env.
#
#   bash study/interface/setup_db.sh --full
#
set -euo pipefail

# Run from repo root regardless of where the script is invoked.
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

PY="${PYTHON:-python3}"
FULL=0
[ "${1:-}" = "--full" ] && FULL=1

echo "── mock-X study DB setup ──────────────────────────────────────"
echo "repo: $ROOT"
echo "python: $("$PY" --version 2>&1)"
echo

if [ "$FULL" = "1" ]; then
  echo "[full] 1/3 extract community notes (scans the large CN dumps)…"
  "$PY" -m study.interface.extract_notes
  echo "[full] 2/3 fetch tweet media (needs X creds in agent/llm/.env)…"
  "$PY" -m study.interface.fetch_media
  STEP="3/3"
else
  # Verify the committed inputs exist; tell the user how to regenerate if not.
  missing=0
  for f in study/posts/selected_posts.csv study/interface/data/notes_selected.csv study/interface/data/media_index.csv; do
    if [ ! -f "$f" ]; then echo "  MISSING: $f"; missing=1; fi
  done
  if [ "$missing" = "1" ]; then
    echo
    echo "Some committed inputs are missing. Re-run the one-time steps with:"
    echo "    bash study/interface/setup_db.sh --full"
    echo "(extract_notes needs tsv_generation/cn_data/*.tsv; fetch_media needs X creds.)"
    exit 1
  fi
  STEP="1/1"
fi

echo "[$STEP] building study.db…"
"$PY" -m study.interface.build_db

echo
echo "── verify ─────────────────────────────────────────────────────"
"$PY" - <<'PYEOF'
import sqlite3
c = sqlite3.connect("study/interface/study.db")
posts = c.execute("select count(*) from posts").fetchone()[0]
iv = c.execute("select count(*) from interventions").fetchone()[0]
notes = c.execute("select count(*) from interventions where condition='control' and body!=''").fetchone()[0]
media = c.execute("select count(*) from posts where media_json!='[]'").fetchone()[0]
real = c.execute("select count(*) from interventions where kind='bot_reply' and is_stub=0").fetchone()[0]
stubs = c.execute("select count(*) from interventions where kind='bot_reply' and is_stub=1").fetchone()[0]
print(f"  posts ................ {posts}")
print(f"  interventions ........ {iv}  (= posts x 4 conditions)")
print(f"  control notes (real) . {notes}")
print(f"  posts with media ..... {media}")
print(f"  bot replies .......... {real} real, {stubs} stub")
PYEOF

cat <<EOF

── done ───────────────────────────────────────────────────────
Run the interface:
    pip install -e .            # one-time, for Flask (see requirements.txt)
    $PY -m study.interface.server         # http://127.0.0.1:8000

Open a thread:
    http://127.0.0.1:8000/?post_id=<tweetId>&condition=<neutral|agreeable|satirical|control>

Note: bot replies are ingested from study/posts/selected_posts_replies.csv
(committed). Any post without a generated reply falls back to stub text.
Regenerate replies with:
    python study/scripts/batch_generate_replies.py study/posts/selected_posts.csv
EOF
