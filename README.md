# App Research Agent (`app-research-agent`)

An automated research pipeline for assessing the feasibility of building Composio-style toolkits across 100 SaaS applications.

The agent investigates official developer documentation, extracts authentication mechanics, self-serve onboarding paths, API architecture, rate limits, webhooks, and MCP ecosystem availability, grounded strictly in verifiable evidence quotes.

---

## Architecture Overview

```
app-research-agent/
├── apps.csv                 # 100 target apps across 10 distinct categories
├── config.py                # Environment configuration (Pydantic Settings)
├── schema.py                # Strict Pydantic models for feasibility records & evidence
├── run.py                   # Main CLI entrypoint (supports --pass 1, --sample, --verify)
├── verifier.py              # Verification engine CLI (audits Pass 1 -> outputs Pass 2)
├── generate_human_check.py  # Stratified 20-app sampler generator
├── human_check.csv          # Ground-truth review sheet for manual human evaluation
├── accuracy.py              # Computes field-level & overall accuracy (never fabricated)
├── core/
│   ├── cache.py             # Atomic disk caching (pages, search, LLM responses)
│   ├── fetcher.py           # Async HTTP fetcher with anti-bot headers & bounded concurrency
│   ├── search.py            # Search router: Composio SDK -> Tavily/Brave -> Plain DDG
│   ├── extractor.py         # Grounded text analyzer & verbatim quote miner
│   ├── llm.py               # Configurable LLM client (OpenAI, Gemini, Groq, Claude, Ollama, Heuristic)
│   ├── verifier.py          # Evidence verification & cross-check engine
│   └── pipeline.py          # Master coordinator
├── cache/                   # Persistent cache directory
├── results/                 # Pipeline deliverables
│   ├── pass1.json / .csv    # Initial research findings
│   ├── pass2.json / .csv    # Stricter revised findings post-verification
│   ├── verification_report.json # Audit findings, failure types & disagreement rates
│   ├── human_check.csv      # Stratified human audit sample
│   ├── accuracy.json        # Ground-truth verified accuracy metrics
│   └── human_queue.csv      # Triage queue for unverified/low-confidence apps
└── tests/                   # Pytest test suite
```

---

## Key Features & Constraints

1. **Grounded in Fetched Documentation (No Pure Memory Hallucinations)**:
   - For every app, the agent resolves official developer docs, auth guides, and pricing/getting started pages via live search or direct domain discovery.
   - Pages are fetched and parsed into clean structured text before synthesis.
2. **Strict Evidence Verification Loop**:
   - Every claim is tied to an official URL and a verbatim quote under 25 words.
   - A verification loop verifies that the quote exists in the fetched page text.
   - If evidence cannot be verified, the field falls back to `"unknown"`, confidence is adjusted, and ambiguous apps are routed to `human_queue.csv`.
3. **Async Bounded Concurrency & Disk Caching**:
   - Concurrency is bounded to 5 concurrent tasks (`asyncio.Semaphore(5)`).
   - All network calls and LLM outputs are hashed and cached to `cache/` so reruns are instantaneous and free.
4. **Composio SDK Integration with Plain Search Fallback**:
   - If `COMPOSIO_API_KEY` is provided, queries route through Composio's search and scraping toolkits.
   - If unavailable, the router automatically falls back to plain search (e.g. DDG HTML/Lite) and logs which provider was active.
5. **Configurable LLM Provider**:
   - Configurable via `.env`: `openai`, `gemini`, `groq`, `anthropic`, `ollama` (OpenAI-compatible), or `heuristic` (zero-key fallback).

---

## Setup & Installation

### 1. Prerequisites
- Python 3.10+ (Tested on Python 3.11)

### 2. Install Dependencies
```bash
pip install -r requirements.txt
```

### 3. Configure Environment Variables (Optional)
Copy `.env.example` to `.env`:
```bash
cp .env.example .env
```
Populate your preferred LLM provider and search keys:
```env
# Select provider: openai, gemini, groq, anthropic, or auto
LLM_PROVIDER=auto

# Provide any available API key:
OPENAI_API_KEY=sk-...
GEMINI_API_KEY=...
GROQ_API_KEY=gsk-...

# Composio Integration (Optional)
COMPOSIO_API_KEY=...

# Concurrency & Cache
CONCURRENCY_LIMIT=5
CACHE_DIR=cache
```
*Note: If no API key is provided, the agent seamlessly operates in offline Heuristic Verbatim mode, extracting structured facts and genuine quotes directly from fetched DOM text.*

### Verification & Accuracy Workflow

1. **Run Verification Stage (Pass 1 -> Pass 2)**:
   ```bash
   python verifier.py
   # Or alternatively:
   python run.py --verify
   ```
   - Re-fetches every evidence URL in `results/pass1.json` to verify:
     1. URL loads (flags `dead_link`)
     2. Quoted text actually appears on page via fuzzy token matching (flags `quote_not_found`)
     3. Quote genuinely supports the claim via LLM judge (flags `outdated_docs` or `ambiguous_gating`)
   - Performs independent orthogonal cross-checks on highest-risk fields (`self_serve`, `existing_mcp`, `auth_methods`).
   - For flagged rows, executes a stricter Pass 2 re-research prompt feeding previous answers and verifier objections.
   - Outputs: `results/pass2.json`, `results/pass2.csv`, and `results/verification_report.json`.

2. **Generate Human Verification Sample**:
   ```bash
   python generate_human_check.py
   ```
   Produces `human_check.csv` with a stratified sample of 20 apps (exactly 2 per category) and empty columns (`human_verdict_auth`, `human_verdict_self_serve`, etc.) for manual human ground-truth marking.

3. **Compute Real Accuracy (Never Fabricated)**:
   ```bash
   python accuracy.py
   ```
   Reads `human_check.csv`. If unfilled, reports pending status. When filled with verdicts (`correct` or `wrong`), computes field-level and overall accuracy for Pass 1 vs Pass 2, delta improvement, and writes `results/accuracy.json`.

---

## How to Run

### Run 3 Sample Apps (Salesforce, Telegram, Twenty)
```bash
python run.py --sample
```

### Run Full 100 Apps (Pass 1)
```bash
python run.py --pass 1
```

### Additional Options
```bash
# Run specific apps by name
python run.py --apps Salesforce,Telegram,Twenty,HubSpot

# Limit run to first 10 apps
python run.py --limit 10

# Bypass disk cache to force fresh fetching
python run.py --pass 1 --no-cache

# Run with verbose debug logging
python run.py --pass 1 --verbose
```

### Run Test Suite
```bash
pytest -v
```

---

## Sample Run Quality Inspection (Salesforce, Twenty, Telegram)

| ID | App | Category | Auth Methods | Self-Serve | Buildability | Confidence | Quotes | Human Queue? |
|---|---|---|---|---|---|---|---|---|
| **1** | **Salesforce** | CRM and Sales | `api_key`, `oauth2` | `self_serve_free` | `easy_win` | 74% | 3 verified | No |
| **5** | **Twenty** | CRM and Sales | `oauth2`, `api_key`, `bearer_token` | `self_serve_free` | `easy_win` | 90% | 5 verified | No |
| **27** | **Telegram** | Communications | `api_key` | `self_serve_free` | `easy_win` | 66% | 2 verified | No |

### Extracted Verbatim Evidence Excerpts
- **Salesforce (REST API feasibility)**:
  > *"About REST API REST API is one of several web interfaces that you can use to access your Salesforce data without using"*
  > *(Source: `developer.salesforce.com`)*
- **Twenty (OAuth2 & Rate Limits)**:
  > *"error. fields createdat updatedat deletedat position fields ### rate limits limit value requests 100 per minute batch size 60 records per call"*
  > *(Source: `docs.twenty.com`)*
- **Telegram (Webhooks)**:
  > *"updates If you're using webhooks , you can perform a request to the Bot API while sending an answer to the webhook."*
  > *(Source: `core.telegram.org`)*
