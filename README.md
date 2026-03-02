# derad-agent

`derad-agent` builds a statement-specific **Community Notes note space** from a global FAISS index.
It runs a single-pass pipeline that plans queries, retrieves semantically similar notes, expands by tweet cluster, and scores each note on a 1D misleadingness axis using dataset-native labels.

Pipeline summary:

`statement -> query planning -> semantic retrieval -> tweet-cluster expansion -> dedupe -> misleadingness landscape`

## Repository structure

- `derad_agent/indexing/`: TSV parsing, chunking, embedding, FAISS index builds
- `derad_agent/runtime/`: single-pass landscape pipeline and API
- `derad_agent/llm/`: model config and prompt templates
- `derad_agent/cli/`: command-line entry points
- `derad_agent/shared/`: shared helpers and schema normalization
- `tests/`: unit tests

## Pipeline explanation

### Entry point

- API: `derad_agent.runtime.landscape_api.retrieve_statement_landscape(statement, ...)`
- Main options:
  - `similarity_min`
  - `max_points`
  - metadata filters (`filter_docs_before_utc`, `exclude_tweet_id`, `include_classifications`)

### Retrieval flow (single pass)

1. Generate focused search queries from the statement (`step_1_generate_queries`).
2. Retrieve semantic seed notes from FAISS (`step_2_retrieve_documents`).
3. Expand by `tweet_id` cluster (`step_3_augment_documents`).
4. Deduplicate globally by `note_id` (fallback: `tweet_id + content`), preferring higher similarity.
5. Generate final statement landscape output (`step_4_build_landscape_output`) with:
   - `landscape_summary` (plain-language landscape overview)
   - `key_reasons` (top evidence-backed reasons from retrieved notes)

Retrieval metadata attached to documents:

- `retrieval_similarity`
- `retrieval_distance`
- `retrieval_source`
- `retrieval_query`

### 1D misleadingness scoring (dataset-native only)

The runtime uses `build_misleadingness_landscape(...)`.

Per note, `misleadingness_axis` in `[-1, 1]` is computed from:

- `classification` prior:
  - `NOT_MISLEADING` -> positive
  - `MISINFORMED...` / `MISLEADING` -> negative
- `label_flags` adjustment:
  - `notMisleading*` flags push right
  - `misleading*` flags push left

No statement-conditioned text stance scoring is used.

### Output contract

`run_landscape_agent(...)` returns:

- `statement`
- `queries`
- `iterations` (single diagnostics entry)
- `documents`
- `misleadingness_landscape`:
  - `thresholds`: `similarity_min`
  - `points`: `note_id`, `tweet_id`, `misleadingness_axis`, `similarity`, `classification`, flag counts, `summary_preview`
  - `tweet_clusters`: `centroid_misleadingness`, `avg_similarity`, `note_count`
  - `ranges`: quantiles for `misleadingness_axis` and `similarity`
- `bucket_landscape`:
  - tweet-level bucket summary:
    - `StronglyMisleading`, `Misleading`, `MixedUnclear`, `NotMisleading`, `StronglyNotMisleading`
- `statement_landscape`:
  - `landscape_summary`: plain-language text describing the retrieved landscape around the statement
  - `key_reasons`: top evidence-backed reasons distilled from retrieved notes (`reason`, `bucket`, `note_id`, `tweet_id`, `classification`, `similarity`, `misleadingness_axis`, `evidence_links`)

### Interpretation

- `misleadingness_axis = -1` means strongly misleading.
- `misleadingness_axis = +1` means strongly not misleading.
- Values near `0` indicate mixed or weak direct labeling signals.

## Prerequisites

- Python 3.9+
- Azure OpenAI credentials for embeddings and query planning

## Installation

```bash
python -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
pip install -e .
```

## Environment setup

Copy the template and fill in your credentials:

```bash
cp derad_agent/llm/.env.example derad_agent/llm/.env
```

Required variables:

- `AZURE_OPENAI_API_KEY`
- `AZURE_OPENAI_ENDPOINT` (used for both chat and embeddings)
- `AZURE_OPENAI_DEPLOYMENT_EMBED`
- `AZURE_OPENAI_DEPLOYMENT_CHAT`

Optional path overrides:

- `DERAD_AGENT_NOTES_TSV_ROOT`
- `DERAD_AGENT_INDEX_ROOT`

## Quick start

### 0) Fast onboarding (download index + full notes)

Use the onboarding CLI:

```bash
pip install gdown
python -m derad_agent.cli.onboard_data
```

If you installed with `pip install -e .`, you can also run:

```bash
derad-onboard-data
```

This downloads:

- Prebuilt FAISS index folder: `https://drive.google.com/drive/folders/1L3xD8DRFDDaVraH7ikCa3a16QqExPtkG?usp=drive_link`
- Full notes TSV zip: `https://drive.google.com/file/d/1o864Ed-zXP7OJK42qISZAaEFK3K8qOJG/view?usp=drive_link`

Then set env vars:

```bash
export DERAD_AGENT_INDEX_ROOT="$(pwd)/indexes"
export DERAD_AGENT_NOTES_TSV_ROOT="$(pwd)/data/full"
```

Expected FAISS layout after download:

- `indexes/community_notes_global/faiss_idx/index.faiss`
- `indexes/community_notes_global/faiss_idx/index.pkl`

### 1) Build the global index

```bash
python -m derad_agent.cli.build_indexes \
  --tsv-root /absolute/path/to/community_notes_tsv_or_file \
  --global-index \
  --index-root /absolute/path/to/indexes
```

### 2) Retrieve a note space for a statement

```bash
python -m derad_agent.cli.ask \
  --statement "Mail-in voting increases fraud." \
  --similarity-min 0.45 \
  --max-points 200 \
  --index-root /absolute/path/to/indexes
```

### 2b) Example statements and outputs

These are real example inputs run against the 1% sample index (`indexes/indexes_1pct`) using:

```bash
python -m derad_agent.cli.ask \
  --statement "<statement>" \
  --index-root indexes/indexes_1pct \
  --similarity-min 0.45 \
  --max-points 120
```

Example statement:

- Input: `Vaccines cause autism.`
- Command:

```bash
python -m derad_agent.cli.ask \
  --statement "Vaccines cause autism." \
  --index-root indexes/indexes_1pct \
  --similarity-min 0.45 \
  --max-points 120
```

- Terminal output:

```text
Building statement-conditioned misleadingness landscape
Statement: Vaccines cause autism.


╭──────────────────────────── Statement Landscape ─────────────────────────────╮
│ The retrieved landscape is strongly dominated by notes classifying the       │
│ statement as misleading: 17 of 18 notes (94.4%) flag the claim that vaccines │
│ cause autism as misinformed or potentially misleading, while 1 note is       │
│ labeled not misleading. Many notes point to a scientific consensus and       │
│ multiple large-scale studies finding no link between vaccines and autism,    │
│ and several notes cite the retraction and ethical problems in Wakefield's    │
│ original MMR study. A smaller set of notes highlights methodological         │
│ problems or retractions for some studies that have been used to support the  │
│ claim. Evidence in the collection is fairly rich regarding debunking sources │
│ and authoritative reviews, although one retrieved note references studies    │
│ described as “showing cause for concern,” indicating some heterogeneity in   │
│ the sources cited.                                                           │
│                                                                              │
│ Key reasons:                                                                 │
│ 1. Andrew Wakefield’s original study linking the MMR vaccine to autism was   │
│ found fraudulent and was retracted; investigations showed conflicts of       │
│ interest and falsified data.                                                 │
│    - source: https://briandeer.com/mmr/lancet-summary.htm                    │
│    - source: https://www.bmj.com/content/340/bmj.c696                        │
│    - source: https://www.ncbi.nlm.nih.gov/pmc/articles/PMC2831678/           │
│    - source: https://www.nature.com/articles/nm0310-248b                     │
│    - source: https://www.bmj.com/content/342/bmj.c5347                       │
│    - source: https://www.ncbi.nlm.nih.gov/pmc/articles/PMC3136032/           │
│    - source:                                                                 │
│ https://goodreads.com/book/show/52527565-the-doctor-who-fooled-the-world     │
│    - source:                                                                 │
│ https://goodreads.com/en/book/show/3360358-autism-s-false-prophets           │
│ 2. Multiple large-scale, peer-reviewed studies and reviews have found no     │
│ causal link between vaccines and autism, supporting the conclusion that      │
│ vaccines do not cause autism.                                                │
│    - source: https://ncbi.nlm.nih.gov/pubmed/29398935                        │
│    - source: https://pubmed.ncbi.nlm.nih.gov/19128068/                       │
│    - source:                                                                 │
│ https://www.aap.org/en/news-room/fact-checked/fact-checked-vaccines-safe-and │
│ -effect-no-link-to-autism                                                    │
│    - source:                                                                 │
│ https://www.chop.edu/vaccine-education-center/vaccine-safety/vaccines-and-ot │
│ her-conditions/autism                                                        │
│    - source: https://ncbi.nlm.nih.gov/pubmed/31217233                        │
│    - source: https://ncbi.nlm.nih.gov/pubmed/30831578                        │
│    - source: https://ncbi.nlm.nih.gov/pubmed/30104424                        │
│    - source: https://ncbi.nlm.nih.gov/pubmed/26417083                        │
│ 3. Public health agencies and scientific bodies (e.g., CDC, National         │
│ Academies) state there is no evidence that vaccines cause autism and         │
│ summarize the consensus from multiple studies.                               │
│    - source: https://www.cdc.gov/vaccinesafety/concerns/autism.html          │
│    - source:                                                                 │
│ https://www.nationalacademies.org/based-on-science/vaccines-do-not-cause-aut │
│ ism                                                                          │
│    - source:                                                                 │
│ https://www.factcheck.org/2023/07/scicheck-false-claim-about-cause-of-autism │
│ -highlighted-on-pennsylvania-senate-panel/                                   │
│    - source:                                                                 │
│ https://www.parents.com/health/autism/vaccines/health-update-more-proof-that │
│ -vaccines-dont-cause-autism/                                                 │
│    - source:                                                                 │
│ https://www.usatoday.com/story/news/factcheck/2023/10/12/vaccines-rarely-con │
│ tain-mercury-do-not-cause-autism-fact-check/71126393007/                     │
│    - source:                                                                 │
│ https://publications.aap.org/patiented/article-abstract/doi/10.1542/peo_docu │
│ ment599/82016/Vaccines-Autism-Toolkit?redirectedFrom=fulltext?autologincheck │
│ =redirected                                                                  │
│    - source: https://www.webmd.com/brain/autism/do-vaccines-cause-autism     │
│ 4. Some studies that have been cited to support the vaccine–autism link were │
│ later retracted or criticized for serious methodological flaws and conflicts │
│ of interest.                                                                 │
│    - source:                                                                 │
│ https://retractionwatch.com/2017/05/08/retracted-vaccine-autism-study-republ │
│ ished/                                                                       │
│    - source:                                                                 │
│ https://science.feedback.org/review/significant-methodological-flaws-in-a-20 │
│ 20-study-claiming-to-show-unvaccinated-children-are-healthier-brian-hooker-c │
│ hildrens-health-defense/                                                     │
│    - source: https://pubmed.ncbi.nlm.nih.gov/34360528/                       │
│    - source:                                                                 │
│ https://publications.aap.org/pediatrics/article-abstract/114/3/584/67149/Thi │
│ merosal-Exposure-in-Infants-and-Developmental?redirectedFrom=fulltext        │
│    - source:                                                                 │
│ https://publications.aap.org/pediatrics/article-abstract/112/5/1039/28714/Sa │
│ fety-of-Thimerosal-Containing-Vaccines-A-Two?redirectedFrom=fulltext         │
│    - source:                                                                 │
│ https://chop.edu/vaccine-education-center/vaccine-safety/vaccines-and-other- │
│ conditions/asthma-allergies#references                                       │
│    - source:                                                                 │
│ https://annualreviews.org/doi/abs/10.1146/annurev-virology-092818-015515     │
│    - source: https://jamanetwork.com/journals/jama/fullarticle/2275444       │
│ 5. A minority note points to studies that it interprets as cause for concern │
│ about links between vaccinations and neurodevelopmental disorders,           │
│ indicating some authors highlight different findings.                        │
│    - source: https://pubmed.ncbi.nlm.nih.gov/19043939/                       │
│    - source: https://pubmed.ncbi.nlm.nih.gov/36673825/                       │
│    - source: https://pubmed.ncbi.nlm.nih.gov/33198395/                       │
│    - source: https://pubmed.ncbi.nlm.nih.gov/31841767/                       │
│    - source: https://pubmed.ncbi.nlm.nih.gov/29721353/                       │
│    - source: https://pubmed.ncbi.nlm.nih.gov/16766480/                       │
│    - source: https://pubmed.ncbi.nlm.nih.gov/15795695/                       │
│    - source: https://pubmed.ncbi.nlm.nih.gov/14976450/                       │
╰──────────────────────────────────────────────────────────────────────────────╯
Full run output saved to: results/ask_runs/20260302T012906Z.json
```

### 3) Use the Python API directly

```bash
python - <<'PY'
from derad_agent.runtime.landscape_api import retrieve_statement_landscape

res = retrieve_statement_landscape(
    statement="Mail-in voting increases fraud.",
    similarity_min=0.45,
)
print("points:", len(res["misleadingness_landscape"]["points"]))
PY
```

## Data management

Raw/full TSV files are intentionally local-only and should not be committed.

- Track in Git: `data/samples/*.tsv`, `data/manifest.json`, `data/checksums.sha256`
- Keep local-only: `data/notes-*.tsv`, generated `indexes/`, generated `results/`
- Use `DERAD_AGENT_NOTES_TSV_ROOT` to point to your local/raw TSV location
- For first-time setup from shared Drive artifacts, run: `python -m derad_agent.cli.onboard_data`

Validate tracked sample fixtures:

```bash
python -m derad_agent.cli.onboard_data --data-check-only
```

Refresh checksums after sample updates:

```bash
python -m derad_agent.cli.onboard_data --data-check-only --write-checksums
```

## Development

Run tests:

```bash
pytest -q
python -m derad_agent.cli.onboard_data --data-check-only
```

## Data and artifact policy

This repository intentionally excludes local runtime artifacts and secrets:

- no prebuilt indexes
- no local results/plots/caches
- no `.env` secrets

The `.gitignore` file is configured to keep these out of version control.
