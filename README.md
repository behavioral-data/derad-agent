# derad-agent

**Retrieve Community Notes at scale and respond to any claim with evidence-backed reasoning.**

`derad-agent` takes a natural-language claim, retrieves semantically relevant [Community Notes](https://communitynotes.x.com/) from a prebuilt FAISS index, expands each result by its tweet cluster, proportionally samples notes from the misleadingness distribution, and passes them to an LLM as independent evidence. The LLM reads the actual note content — without seeing any labels or scores — and produces a direct response to the claim with grounded reasons and source links.

```
claim --> query planning --> semantic retrieval --> tweet-cluster expansion --> dedupe --> proportional sampling --> LLM response
```

## Prerequisites

- Python 3.9+
- Azure OpenAI credentials (used for embeddings and LLM-based query planning)

## Installation

```bash
python -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
pip install -e .
```

## Environment setup

Copy the template and fill in your Azure OpenAI credentials:

```bash
cp derad_agent/llm/.env.example derad_agent/llm/.env
```

Required variables:

| Variable | Purpose |
|---|---|
| `AZURE_OPENAI_API_KEY` | API key for Azure OpenAI |
| `AZURE_OPENAI_ENDPOINT` | Endpoint URL (shared by chat and embeddings) |
| `AZURE_OPENAI_DEPLOYMENT_EMBED` | Deployment name for the embedding model |
| `AZURE_OPENAI_DEPLOYMENT_CHAT` | Deployment name for the chat/completion model |

Optional path overrides (can also be set in `.env`):

| Variable | Default | Purpose |
|---|---|---|
| `DERAD_AGENT_NOTES_TSV_ROOT` | `data/full` | Directory containing Community Notes TSV files |
| `DERAD_AGENT_INDEX_ROOT` | `indexes` | Root directory for FAISS index storage |

## Quick start

### 1. Download prebuilt artifacts

The fastest way to get running is to use the onboarding CLI, which downloads a prebuilt FAISS index and the full Community Notes TSV dataset from Google Drive:

```bash
pip install gdown
python -m derad_agent.cli.onboard_data
```

If you installed with `pip install -e .`, you can also use the shorthand:

```bash
derad-onboard-data
```

After downloading, set the path overrides so the runtime can find the artifacts:

```bash
export DERAD_AGENT_INDEX_ROOT="$(pwd)/indexes"
export DERAD_AGENT_NOTES_TSV_ROOT="$(pwd)/data/full"
```

### 2. Build the global index from scratch (alternative)

If you have your own Community Notes TSV export, build a FAISS index directly:

```bash
python -m derad_agent.cli.build_indexes \
  --tsv-root /path/to/community_notes_tsv \
  --global-index \
  --index-root /path/to/indexes
```

### 3. Query a statement

```bash
python -m derad_agent.cli.ask \
  --statement "Mail-in voting increases fraud." \
  --similarity-min 0.45 \
  --max-points 200
```

The CLI prints a Rich-formatted response panel and saves the full JSON result to `results/ask_runs/`.

### 4. Use the Python API directly

```python
from derad_agent.runtime.landscape_api import retrieve_statement_landscape

result = retrieve_statement_landscape(
    statement="Mail-in voting increases fraud.",
    similarity_min=0.45,
)
print("points:", len(result["misleadingness_landscape"]["points"]))
```

## Example output

Running `python -m derad_agent.cli.ask --statement "Vaccines cause autism." --similarity-min 0.45 --max-points 120` produces output like:

```
╭─────────────────────────────── Response ─────────────────────────────────────╮
│ This claim is not supported by scientific evidence. Andrew Wakefield's      │
│ original study linking the MMR vaccine to autism was found to be fraudulent │
│ and has been retracted. Since then, multiple large-scale, peer-reviewed     │
│ studies have found no causal link between vaccines and autism. Public       │
│ health agencies including the CDC and the National Academies confirm this   │
│ scientific consensus.                                                       │
│                                                                             │
│ Reasons:                                                                    │
│ 1. Andrew Wakefield's original study linking the MMR vaccine to autism was  │
│    found fraudulent and was retracted.                                      │
│    - source: https://doi.org/10.1136/bmj.c5347                             │
│ 2. Multiple large-scale, peer-reviewed studies have found no causal link    │
│    between vaccines and autism.                                             │
│ 3. Public health agencies (CDC, National Academies) confirm the scientific  │
│    consensus: vaccines do not cause autism.                                 │
│    - source: https://www.cdc.gov/vaccinesafety/concerns/autism.html         │
│ 4. Several studies cited in support of the vaccine-autism link were later   │
│    retracted or criticized for methodological flaws.                        │
╰──────────────────────────────────────────────────────────────────────────────╯
```

Each reason includes `note_id`, `tweet_id`, and `evidence_links`. The full structured JSON is saved alongside the terminal output.

## Repository structure

```
derad_agent/
├── cli/                 Command-line entry points (ask, build_indexes, onboard_data)
├── indexing/            TSV parsing, chunking, embedding, and FAISS index construction
├── llm/                 Azure OpenAI configuration and LLM prompt templates
├── runtime/             Single-pass landscape pipeline, retrieval, and scoring
│   └── steps/           Pipeline steps: planning, retrieval, augmentation, output
└── shared/              Constants, validation, text utilities, and Community Notes helpers
tests/                   Unit tests
```

## Pipeline architecture

### Entry point

```python
derad_agent.runtime.landscape_api.retrieve_statement_landscape(statement, ...)
```

Key parameters: `similarity_min`, `max_points`, `filter_docs_before_utc`, `exclude_tweet_id`, `include_classifications`.

### Retrieval flow

1. **Query planning** — An LLM generates 3-6 focused search queries from the input claim.
2. **Semantic retrieval** — Each query is embedded and matched against the FAISS index to find seed notes.
3. **Tweet-cluster expansion** — For every seed note, all other notes on the same tweet are pulled in to capture the full conversation context.
4. **Deduplication** — Notes are deduplicated globally by `note_id` (fallback: `tweet_id` + content), keeping the highest-similarity copy.
5. **Proportional sampling** — Notes are bucketed by their misleadingness score and sampled proportionally so the LLM sees a representative slice of the evidence distribution.
6. **Response output** — The sampled notes are passed to an LLM as independent evidence (text and links only — no labels or scores). The LLM reads the note content, weighs the evidence, and produces a direct `response` to the claim with grounded `reasons`.

### Misleadingness scoring (internal)

Each note is placed on a 1-D `misleadingness_axis` in [-1, +1] using dataset-native labels only (no LLM stance classification):

| Signal | Effect |
|---|---|
| `classification = NOT_MISLEADING` | Pushes toward +1 |
| `classification = MISINFORMED_OR_POTENTIALLY_MISLEADING` | Pushes toward -1 |
| `notMisleading*` label flags | Push toward +1 |
| `misleading*` label flags | Push toward -1 |

Interpretation: -1 = strongly misleading, 0 = mixed/unclear, +1 = strongly not misleading.

These scores drive proportional sampling and per-note/tweet-level analytics but are **not** passed to the response LLM. The `classification` field reflects the note author's assessment of their original tweet, not the note's relationship to the queried claim — so the LLM sees only note text and links and forms its own judgment.

### Output structure

`retrieve_statement_landscape(...)` returns a dictionary with:

| Key | Contents |
|---|---|
| `statement` | The input statement |
| `queries` | Generated search queries |
| `documents` | Retrieved and deduplicated Community Notes documents |
| `misleadingness_landscape` | Per-note scores, tweet clusters, quantile ranges |
| `bucket_landscape` | Tweet-level buckets (StronglyMisleading through StronglyNotMisleading) |
| `statement_landscape` | `response` (direct claim response) and `reasons` (evidence-backed reasons with links) |

## Data management

### What goes in Git

- `data/samples/*.tsv` — small fixtures for testing
- `data/manifest.json` and `data/checksums.sha256` — integrity metadata

### What stays local

- `data/full/` — full Community Notes TSV exports
- `indexes/` — generated FAISS indexes
- `results/` — CLI run outputs

The `.gitignore` is configured to enforce this separation. Use `DERAD_AGENT_NOTES_TSV_ROOT` to point at your local TSV location.

### Community Notes dataset anatomy

The [public Community Notes dataset](https://communitynotes.x.com/guide/en/under-the-hood/download-data) ships as several TSV files. This project currently ingests only the **notes** file:

| File | Used? | Contents |
|---|---|---|
| `notes-00000.tsv` | **Yes** | Note text (`summary`), `classification`, label flags (`misleading*` / `notMisleading*`), `believable`, `harmful`, `trustworthySources`, etc. |
| `ratings-00000.tsv` | No | Per-rater helpfulness ratings on each note: `helpfulnessLevel` (`HELPFUL`, `SOMEWHAT_HELPFUL`, `NOT_HELPFUL`), plus reason flags like `helpfulInformative`, `helpfulClear`, `notHelpfulIrrelevant`, `notHelpfulSourcesMissing`, etc. |
| `noteStatusHistory-00000.tsv` | No | Historical status changes for each note (when it was rated helpful/not helpful over time). |

**`classification` vs. helpfulness:** The `classification` field in the notes TSV is the **note author's assessment of the tweet** (either `MISINFORMED_OR_POTENTIALLY_MISLEADING` or `NOT_MISLEADING`). It does *not* indicate whether the note itself was found helpful. The note-level helpfulness status (`CURRENTLY_RATED_HELPFUL`, `NEEDS_MORE_RATINGS`, `CURRENTLY_RATED_NOT_HELPFUL`) lives in `noteStatusHistory-00000.tsv` and is derived from the per-rater helpfulness scores in `ratings-00000.tsv`. The current pipeline uses `classification` internally for misleadingness scoring and proportional sampling, but does **not** expose it to the response LLM — the LLM sees only note text and links. The pipeline does not ingest note-level helpfulness status or raw per-rater ratings.

### Validating sample fixtures

```bash
python -m derad_agent.cli.onboard_data --data-check-only
```

### Refreshing checksums after sample updates

```bash
python -m derad_agent.cli.onboard_data --data-check-only --write-checksums
```

## Development

Run the test suite:

```bash
pytest -q
```

Validate tracked sample data:

```bash
python -m derad_agent.cli.onboard_data --data-check-only
```
