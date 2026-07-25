# Agonistic Agent

A reply-bot for X (Twitter) that performs web-evidence fact-checking when summoned. Built as a research instrument for an IRB-exempt study at the University of Washington on how the **tone** of a credible counter-message affects engagement with it.

The bot listens for mentions of a single account from a closed set of enrolled study participants, runs a multi-stage verification pipeline against the live web, and posts a short reply followed by a self-reply linking to a full evidence dossier. Each participant is assigned to one of three tone conditions (`agreeable`, `neutral`, `satirical`); the underlying evidence and reasoning are identical across tones — only the surface text differs.

This is a research artifact, not a production fact-checker. The code is open for reproducibility.

## What it does

For each mention from a registered participant, the pipeline:

1. Reads the parent tweet (text + images), the invoker's instruction, and the thread context.
2. Picks **one** of five actions based on the central proposition and what the invoker asked for: `verify`, `provide_context`, `challenge_opinion`, `surface_perspectives`, or `decline`.
3. Runs iterative web search to build evidence, scores every retrieved domain against a source-quality table, reconciles findings under a confidence gate, audits for drift, freezes the verdict to disk, and renders a reply in the participant's assigned tone.

## How it works

```
mention
  │
  ├─ Stage 1.5  multimodal: VLM image OCR + description + canonical-image match
  │             (agent/factcheck/multimodal.py)
  │
  ├─ Stage 2+3  claim extraction + action selection
  │             one Claude call decomposes the tweet into atomic propositions,
  │             marks the central one, parses any invoker instruction, and picks
  │             one action from {verify, provide_context, challenge_opinion,
  │             surface_perspectives, decline}. Silently pivots when the
  │             invoker's ask doesn't fit the claim character.
  │             (agent/factcheck/extract.py)
  │
  ├─ Stage 4    iterative verification (Papelo-style): the LLM generates the
  │             next search question conditioned on results so far, via
  │             Claude's web_search_20250305 server tool (Responses-API
  │             fallback). (agent/factcheck/verify.py, search.py)
  │
  ├─ Stage 4.5  reconciliation: assembles Supported / Refuted / Disputed /
  │             Contextual findings, counterpoints, perspectives. Domains
  │             are classified into tiers (fact-checker, reputable-news,
  │             primary-source, aggregator, low-quality, satirical) from
  │             IFCN signatories, Wikipedia perennial-sources, and a
  │             model-based fallback. A confidence gate requires distinct
  │             reliable-tier sources before committing to a finding —
  │             otherwise falls back to NotEnoughEvidence.
  │             (agent/factcheck/reconcile.py, sources.py, verdict.py)
  │
  ├─ Stage 5    mechanical audit: catches drift (e.g. URLs not in the
  │             source-quality table) and forces a graceful NEI fallback.
  │             (agent/factcheck/audit.py)
  │
  ├─ Stage 6    freeze the verdict to disk as an immutable research artifact.
  │             (agent/factcheck/freeze.py)
  │
  └─ Stage 7    render the reply: per-action templates, per-tone register
                (agreeable / neutral / satirical), strict no-URLs-in-body.
                The dossier link is posted as a separate self-reply; the
                /info page renders the full sources + reasoning.
                (agent/factcheck/render.py)
```

### Two engines

The staged pipeline above is the default (`DERAD_FACTCHECK_ENGINE=staged`) and the engine the live bot ran. The **main study** generated its stimuli with an alternative **evidence-first loop engine** (`agent/factcheck/loop.py`, selected with `--engine loop`). It replaces the fixed stages 2–5 with a single bounded agentic loop: the model runs its own web searches, deepens the one to three load-bearing threads that decide the verdict, checks the strongest case against its tentative conclusion, and finalizes — followed by an independent audit that can revise or downgrade the verdict. The loop infers the reply action from the post itself and applies the same source-quality tiers. Both engines share the freeze → render boundary (stages 6–7), so tone rendering is identical.

## Repository structure

```
agent/
├── app/             Flask app, streamer, dashboards, /about and /info pages
│   ├── app.py             — HTTP entry, pipeline dispatcher, dashboards
│   ├── streamer.py        — X Filtered-Stream listener
│   ├── participants.py    — registered invokers + tone assignment
│   ├── events.py          — Azure Tables event log
│   ├── dedup.py           — first-seen / once-only guard for mentions
│   ├── survey.py, utils.py, metrics.py
│   └── templates/         — about.html, info.html, dashboard.html
├── factcheck/       The fact-check pipeline (both engines).
│   ├── pipeline.py, pipeline_loop.py  — staged / loop orchestrators
│   ├── multimodal.py, video.py        — media extraction (Stage 1.5)
│   ├── extract.py, verify.py, search.py, reconcile.py, audit.py  — staged stages 2–5
│   ├── loop.py, loop_tools.py, draft.py, verifier.py, snapshot.py — evidence-first loop engine
│   ├── sources.py, verdict.py         — source-quality tiers + outcome logic
│   ├── freeze.py                      — Stage 6 (freeze verdict to disk)
│   ├── render.py, render_lint.py      — Stage 7 (tone rendering + lints)
│   ├── schema.py, context.py, llm.py, prompt_store.py, replay.py
│   └── __main__.py        — single-claim CLI driver
├── cli/             Operational CLIs (register / list / export / poll / etc.)
├── llm/             LLM and X-client config, .env loader
└── shared/          Small utilities (text, HTTP)
study/               All mock-X study material (see study/README.md)
├── data/            108-post stimulus set: posts / notes / replies / media / profiles
├── interface/       Mock-X Flask interface participants rate posts in
├── viewpoint/       Community Notes viewpoint-polarity scoring pipeline
├── post_selection/  Candidate-post selection + participant-assignment algorithms
├── qualtrics_survey/  Qualtrics survey definitions (.qsf)
├── data_analysis/   Pilot survey analysis notebook + figures
├── scripts/         batch_generate_replies.py (retrospective stimulus generation)
└── docs/            Allocation logic, power analysis, stimulus decisions, specs
infra/               Bicep templates (App Service, Storage, Key Vault, etc.)
scripts/             setup-env.sh, smoke tests, probes, ops scripts
tests/               pytest suite (436 tests, incl. repo-hygiene gates)
docs/                Architecture, database schema, design specs (superpowers/);
                     dated working notes archived under docs/archive/
```

## Local development

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e .
python -m pytest -q
```

Run the pipeline against a single claim (no X required, but the Claude credentials in `agent/llm/.env` must be set):

```bash
python -m agent.factcheck "Mail-in voting causes mass fraud."
python -m agent.factcheck --tone satirical "Vaccines cause autism."
python -m agent.factcheck --invoker "what's the context" "Photo shows Rosa Camfield, 101, with her 17th child."
python -m agent.factcheck --image https://example.com/photo.jpg "Photo shows the 2024 protest."
python -m agent.factcheck --all-tones "Climate change is a hoax."
```

`--invoker` injects what the invoker would have typed alongside the bot handle. With no `--invoker`, the action is inferred from the claim's character alone.

Each run writes a frozen verdict JSON to `data/freezes/<invocation_id>.json`.

## Deployment

Production runs on Azure App Service. Infrastructure (App Service, Storage Account with Tables, Key Vault, Container Registry, monitoring) is declared in `infra/main.bicep`. Deploy with:

```bash
azd up
```

`azure.yaml` wires the Bicep template, the Dockerfile, and remote ACR build into the standard `azd` lifecycle. Runtime configuration is pulled from Azure Key Vault by `scripts/setup-env.sh`, which writes a populated `agent/llm/.env` for local dev; the App Service reads the same variables from app settings in production. Authentication to Azure Tables uses Managed Identity via `DefaultAzureCredential`.

## Configuration

| Variable | Purpose |
|---|---|
| `AZURE_CLAUDE_ENDPOINT` | Foundry / Azure AI Services endpoint hosting Claude |
| `AZURE_CLAUDE_API_KEY` | API key for the above |
| `AZURE_CLAUDE_DEPLOYMENT_CHAT` | Chat deployment name (default `claude-sonnet-4-6`) |
| `CLAUDE_SEARCH_DEPLOYMENT` | Deployment used for Claude's `web_search_20250305` tool (preferred) |
| `FOUNDRY_PROJECT_ENDPOINT` | Azure OpenAI Responses-API endpoint (fallback search backend) |
| `FOUNDRY_SEARCH_MODEL` | Fallback search model (e.g. `gpt-5-mini-search`) |
| `X_BEARER_TOKEN`, `X_API_KEY`, `X_API_SECRET`, `X_ACCESS_TOKEN`, `X_ACCESS_TOKEN_SECRET` | X credentials for the single bot identity |
| `BOT_HANDLE`, `BOT_USER_ID` | Bot's @handle and numeric user ID (used for self-reply guard) |
| `SERVER_NAME` | Public hostname (e.g. App Service FQDN); required by Flask URL building |
| `DERAD_TABLES_ENDPOINT` | Azure Tables endpoint (`https://<acct>.table.core.windows.net`) |
| `DERAD_EVENTS_BACKEND` | `memory` (default) or `tables` |
| `DERAD_PARTICIPANTS_BACKEND` | `memory` (default) or `tables` |
| `DERAD_INGEST_MODE` | `webhooks` (Filtered Stream listener), `poll`, or `off` |
| `DERAD_DRY_RUN` | When `true`, run the pipeline but skip the actual X post |
| `DERAD_FORCE_TONE` | Override the participant's assigned tone (testing only) |

See `agent/llm/.env.example` and `scripts/setup-env.sh` for the full list and authoritative defaults.

## Tone conditions

Each registered participant is assigned to one of three conditions, balanced across enrolment:

| Tone | Register |
|---|---|
| `agreeable` | Warm, empathetic; acknowledges the concern before presenting evidence |
| `neutral` | Plain, measured fact-checker voice |
| `satirical` | Deadpan; exposes the claim's tension through irony |

The frozen verdict is invariant under tone — Stage 7 reads the same payload and only swaps surface register. The controlled study adds a fourth arm: a Community Notes control, where the post carries its real crowd-written note instead of a bot reply (see [Reproducing the studies](#reproducing-the-studies)).

## Reproducing the studies

Study materials, code, and generated stimuli live under `study/`:

- `study/data/` — the fielded stimulus set: 108 Community-Notes-flagged X posts (`posts.csv`), the crowd-written note shown in the control condition (`notes.csv`), the 324 generated bot replies across the three tones (`replies.csv`), the post media, and the pre-generated participant allocation (`profiles/`).
- `study/interface/` — the mock-X interface participants rate posts in, plus its build script (`build_db.py`).
- `study/post_selection/` and `study/viewpoint/` — how candidate posts were pulled from Community Notes and scored for viewpoint polarity.
- `study/data_analysis/` — the pilot analysis notebook and its figures.
- `study/docs/` — the allocation logic, power analysis, and stimulus decisions.

To regenerate the 108-post stimulus set end to end:

```bash
# 1. Download the Community Notes snapshot (re-downloadable; see the script header)
bash tsv_generation/download_cn_snapshot.sh

# 2. Score notes for viewpoint polarity, then select the topic × polarity cells
#    (study/viewpoint/, study/post_selection/)

# 3. Generate the three tone replies for every post from frozen verdicts
python -m study.scripts.batch_generate_replies --engine loop --study-mode
```

Run the full test suite, including the repository-hygiene checks that gate a public release, with:

```bash
python -m pytest -q
```

## Data availability

This repository is designed to be reproducible without redistributing anything sensitive.

- **Participant data is excluded.** No participant identifiers, recruitment allowlists, survey responses, or the pilot roster are committed. The pilot analysis notebook (`study/data_analysis/daily_survey.ipynb`) ships with its cell outputs cleared, and its raw inputs (`study/data_analysis/input/`) are git-ignored. Reproducing the pilot figures requires the raw survey export, which is held separately under the study's data-management plan.
- **The Community Notes snapshots are not committed.** They are multi-gigabyte public releases, re-downloadable with `tsv_generation/download_cn_snapshot.sh` (the paper's stimuli use the 2026-06-30 snapshot).
- **Stimulus media is included** (`study/data/media/`) so the mock-X interface renders exactly as participants saw it.

## Research and ethics

This bot is operated by researchers at the University of Washington under an **IRB-exempt** determination (Study ID `STUDY00025610`). The bot's `/about` page (`agent/app/templates/about.html`) carries the public disclosure required for AI-bot identification, including the contact email for the UW Human Subjects Division (`hsdinfo@uw.edu`).

Posts are only generated in response to mentions from enrolled participants who have consented to participate. The full source code, the source-quality classifier, the per-stage prompts (under `agent/factcheck/`), and the frozen verdict records are intended to make the study's behavior auditable and reproducible.

## License

License: TBD.
