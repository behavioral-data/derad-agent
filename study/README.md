# study/

All study material for the mock-X experiment lives here.

## Layout

| Directory | Contents |
|---|---|
| `data/` | Single source of truth for study data: `posts.csv`, `notes.csv`, `media/` + `media_index.csv`, `replies.csv`, the participant allocation (`profiles/`), and the built `study.db` |
| `interface/` | Mock-X Flask app + SQLite DB build scripts (`study.db` created by `setup_db.sh`, read from/written to `data/`) |
| `post_selection/` | Post selection and participant-assignment algorithms (`post_assignment.py`) |
| `viewpoint/` | Community Notes viewpoint pipeline (`viewpoint/`) + analysis scripts (`analyze_*.py`) |
| `scripts/` | `batch_generate_replies.py` — batch-generates bot reply text via the fact-check pipeline |
| `docs/` | Specs and plans: `spec-mock-x-interface.md`, `plan-mock-x-interface.md`, `spec-viewpoint.md`, `plan-viewpoint.md`, allocation logic, and the power analysis |
| `qualtrics_survey/` | Qualtrics survey definitions (`.qsf`); the wired versions carry a `<SESSION_TOKEN>` placeholder |
| `data_analysis/` | Pilot survey analysis notebook and its boxplot figures |

The paper itself is maintained separately (Overleaf) and is not tracked here.

## Quickstart

```bash
bash study/interface/setup_db.sh   # builds study/data/study.db from committed data artifacts
python -m study.interface.server   # http://127.0.0.1:8000
```
