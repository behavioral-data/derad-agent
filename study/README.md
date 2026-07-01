# study/

All study material for the mock-X experiment lives here.

## Layout

| Directory | Contents |
|---|---|
| `interface/` | Mock-X Flask app + SQLite DB build scripts (`study.db` created by `setup_db.sh`) |
| `posts/` | `selected_posts.csv` (71 study tweets) + `selected_posts_replies.csv` (generated bot replies) |
| `post_selection/` | Post selection and participant-assignment algorithms (`post_assignment.py`) |
| `viewpoint/` | Community Notes viewpoint pipeline (`viewpoint/`) + analysis scripts (`analyze_*.py`) |
| `scripts/` | `batch_generate_replies.py` — batch-generates bot reply text via the fact-check pipeline |
| `docs/` | Specs and plans: `spec-mock-x-interface.md`, `plan-mock-x-interface.md`, `spec-viewpoint.md`, `plan-viewpoint.md` |
| `data_analysis/` | Survey and behavioral data analysis notebooks |
| `paper/` | LaTeX source for the study paper |

## Quickstart

```bash
bash study/interface/setup_db.sh   # builds study/interface/study.db from committed data artifacts
python -m study.interface.server   # http://127.0.0.1:8000
```
