# Mock X — study interface (Part 2)

Renders a mock-X thread for one `(post_id, condition)`. Local-first; no cloud deps.

## Build the database

```bash
# One-time heavy extract (scans the ~1.4 GB Community Notes dumps -> small CSV).
# Already committed as mockx/data/notes_selected.csv; re-run only if posts change.
python -m mockx.extract_notes

# One-time media fetch (downloads attached tweet images/video stills via the X
# API into static/media/, writes media_index.csv). Both are committed, so this
# only needs re-running if the post set changes. Requires X creds in agent/llm/.env.
python -m mockx.fetch_media

# Fast: build the read-only study.db (170 posts x 4 conditions = 680 rows).
python -m mockx.build_db
```

## Run

```bash
python -m mockx.server   # http://127.0.0.1:8000
```

Open: `http://127.0.0.1:8000/?post_id=<tweetId>&condition=<c>`
where `<c>` ∈ `neutral | agreeable | satirical | control`.

- `neutral|agreeable|satirical` → one bot reply from **@eddiexbot** (stub text until Part-1 generation).
- `control` → the post's real community note as a "Readers added context" card.
- Posts with attached media (97 of 170) render an X-style image grid (videos/GIFs show
  their preview frame with a play badge); images are served locally from `static/media/`.

## Test

```bash
pytest tests/test_mockx_*.py
```

## Notes

- Bot reply bodies are **stubs** (`is_stub=1`); Part 1 overwrites them with generated tone replies.
- Authors are synthetic/anonymized; engagement counts are fabricated and fixed across conditions.
- Deployment (Azure) is deferred — see `docs/superpowers/specs/2026-06-27-mock-x-interface-design.md`.
