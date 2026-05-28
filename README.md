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
├── factcheck/       The pipeline. One file per stage.
│   ├── pipeline.py        — orchestrator
│   ├── multimodal.py      — Stage 1.5
│   ├── extract.py         — Stage 2+3
│   ├── verify.py, search.py  — Stage 4
│   ├── reconcile.py, sources.py, verdict.py  — Stage 4.5
│   ├── audit.py           — Stage 5
│   ├── freeze.py          — Stage 6
│   ├── render.py          — Stage 7
│   ├── schema.py, context.py, llm.py
│   └── __main__.py        — single-claim CLI driver
├── cli/             Operational CLIs (register / list / export / poll / etc.)
├── llm/             LLM and X-client config, .env loader
└── shared/          Small utilities (text, HTTP)
infra/               Bicep templates (App Service, Storage, Key Vault, etc.)
scripts/             setup-env.sh, smoke tests, probes, ops scripts
tests/               pytest suite (~173 tests)
docs/                Architecture notes, pipeline walkthrough, render inputs
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

The frozen verdict is invariant under tone — Stage 7 reads the same payload and only swaps surface register.

## Research and ethics

This bot is operated by researchers at the University of Washington under an **IRB-exempt** determination (Study ID `STUDY00025610`). The bot's `/about` page (`agent/app/templates/about.html`) carries the public disclosure required for AI-bot identification, including the contact email for the UW Human Subjects Division (`hsdinfo@uw.edu`).

Posts are only generated in response to mentions from enrolled participants who have consented to participate. The full source code, the source-quality classifier, the per-stage prompts (under `agent/factcheck/`), and the frozen verdict records are intended to make the study's behavior auditable and reproducible.

## License

License: TBD.
