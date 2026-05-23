# derad-agent

**Reply to polarizing social media claims with evidence from Community Notes.**

Given a claim, derad-agent plans search queries, retrieves semantically similar tweets from a pre-built notes index, selects the most recent `CURRENTLY_RATED_HELPFUL` notes, and asks an LLM to compose a grounded reply. The LLM reads note text and source links — never labels or scores — and writes a direct social media reply.

```
claim → query planning → cosine retrieval → note selection → relevance filter → LLM reply
```

## Prerequisites

- Python 3.9+
- Azure OpenAI credentials (embeddings)
- Azure AI Services credentials (Claude chat)

## Installation

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e .
```

## Environment

Copy the template and fill in your credentials:

```bash
cp derad_agent/llm/.env.example derad_agent/llm/.env
```

| Variable | Purpose |
|---|---|
| `AZURE_OPENAI_API_KEY` | Azure OpenAI API key (embeddings) |
| `AZURE_OPENAI_ENDPOINT` | Azure OpenAI endpoint URL (embeddings) |
| `AZURE_OPENAI_DEPLOYMENT_EMBED` | Embedding model deployment name |
| `AZURE_CLAUDE_ENDPOINT` | Azure AI Services endpoint (Claude chat) |
| `AZURE_CLAUDE_API_KEY` | Azure AI Services API key (Claude chat) |

Optional path overrides:

| Variable | Default | Purpose |
|---|---|---|
| `DERAD_AGENT_NOTES_TSV_ROOT` | `data/full` | Community Notes TSV directory |
| `DERAD_AGENT_INDEX_ROOT` | `indexes` | Notes index root directory |

## Quick start

### 1. Build the notes index

```bash
derad-embed-notes data/full/notes.tsv data/full/noteStatusHistory.tsv --out indexes/notes_index
```

### 2. Run a query

```bash
derad-ask --statement "Mail-in voting increases fraud."
```

Or with explicit style and similarity threshold:

```bash
derad-ask --statement "Vaccines cause autism." \
  --style agreeable \
  --similarity-min 0.35
```

### 3. Python API

```python
from derad_agent import retrieve_statement_landscape

result = retrieve_statement_landscape("Mail-in voting increases fraud.")
print(result["reply"]["response"])
```

## Response styles

| Style | Tone |
|---|---|
| `neutral` (default) | Impartial fact-checker; plain, measured language |
| `agreeable` | Warm and empathetic; acknowledges the concern before presenting evidence |
| `satirical` | Political satirist; exposes the claim's folly through irony and deadpan |

## CLI flags

| Flag | Default | Description |
|---|---|---|
| `--style` | `neutral` | Reply tone: `neutral`, `agreeable`, `satirical` |
| `--no-filter` | off | Skip LLM relevance filter (keep all retrieved notes) |
| `--similarity-min` | `0.0` | Minimum cosine similarity for retrieved tweets |
| `--k-per-query` | `25` | Tweets fetched per search query |
| `--notes-per-tweet` | `10` | Max helpful notes kept per tweet |
| `--max-sources` | `5` | Source URLs printed below the reply |
| `--exclude-tweet-id` | — | Exclude a specific tweet (e.g. self-exclusion) |
| `--index-root` | from env | Override the index root path |

## Repository structure

```
derad_agent/
├── cli/          Entry points: ask.py, embed_notes.py, ui.py
├── llm/          LLM config and prompt templates
├── runtime/      Pipeline orchestration and notes index
│   └── steps/    Planning, relevance filter, and output steps
└── shared/       Validation, text utils, logging
data/
├── full/         Full Community Notes TSV (local only, gitignored)
└── samples/      Small TSV fixtures for testing
indexes/          Built index artifacts (local only, gitignored)
results/          CLI run outputs (local only, gitignored)
tests/            Unit tests
docs/             Design docs and planning
```

## Development

```bash
pytest -q
```

## Data

This project uses the [public Community Notes dataset](https://communitynotes.x.com/guide/en/under-the-hood/download-data). Two TSV files are required:

**`notes.tsv`** — note content:

| Field | Used for |
|---|---|
| `noteId` | Join key + citation ID |
| `tweetId` | Grouping notes by tweet |
| `summary` | Embedding text and LLM evidence |
| `classification` | Stored in cache for future use |
| `createdAtMillis` | Recency ranking within a tweet |

**`noteStatusHistory.tsv`** — rating outcomes:

| Field | Used for |
|---|---|
| `noteId` | Join key |
| `currentStatus` | Filtering to `CURRENTLY_RATED_HELPFUL` only |
