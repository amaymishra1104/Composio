# Composio App Research Agent (`app-research-agent`)

[![Tests](https://img.shields.io/badge/tests-6%2F6%20passing-brightgreen)](tests/)
[![Python](https://img.shields.io/badge/python-3.10%2B-blue)](https://python.org)
[![License](https://img.shields.io/badge/license-MIT-purple)]()

An autonomous research and verification pipeline assessing the feasibility of building Composio-style toolkits across **100 SaaS applications**.

The agent autonomously discovers official developer documentation, extracts authentication mechanics, self-serve onboarding paths, API architecture, rate limits, webhooks, and Model Context Protocol (MCP) availability, grounded strictly in verifiable evidence quotes taken from fetched HTML pages.

---

## Interactive Case Study Dashboard

A complete, self-contained interactive case study is published in **[`index.html`](index.html)**.
- **Reviewable in 2 minutes** with zero narration needed.
- Contains: Executive patterns, SVG/CSS bar charts, interactive searchable matrix of all 100 apps, 4-stage agent architecture, reproducible proof, verification audit trail, and instant data downloads (`.json` & `.csv`).

---

## Repository Structure

```
app-research-agent/
├── index.html               # Self-contained case study dashboard (single-file, dark/light mode)
├── build_index_html.py      # Rebuild script for index.html from results data
├── apps.csv                 # 100 target apps across 10 distinct categories
├── config.py                # Environment configuration (Pydantic Settings)
├── schema.py                # Strict Pydantic models for feasibility records & evidence
├── run.py                   # Main CLI entrypoint (--pass 1, --sample, --verify)
├── verifier.py              # Verification engine CLI (audits Pass 1 -> outputs Pass 2)
├── generate_human_check.py  # Stratified 20-app sampler generator
├── human_check.csv          # Ground-truth review sheet for manual human evaluation
├── accuracy.py              # Evaluates field-level accuracy (never fabricated)
├── requirements.txt         # Production dependencies
├── .env.example             # Documented template of environment variables
├── .gitignore               # Clean git exclusion rules
├── core/
│   ├── cache.py             # Atomic disk caching (pages, search, LLM responses)
│   ├── fetcher.py           # Async HTTP fetcher with rotating headers & retry backoff
│   ├── search.py            # Search router: Composio SDK -> Tavily/Brave -> Plain DDG
│   ├── extractor.py         # Grounded text analyzer & verbatim quote miner (<25 words)
│   ├── llm.py               # Configurable LLM client (Gemini, OpenAI, Groq, Claude, Ollama, Heuristic)
│   ├── verifier.py          # Evidence verification & cross-check engine
│   └── pipeline.py          # Master coordinator with bounded concurrency
├── results/                 # Output deliverables
│   ├── pass1.json / .csv    # Initial research findings (100 apps)
│   ├── pass2.json / .csv    # Refined findings post-verification (100 apps)
│   ├── verification_report.json # Audit findings, failure types & disagreement rates
│   ├── human_check.csv      # Stratified human audit sample
│   ├── accuracy.json        # Ground-truth accuracy metrics
│   └── human_queue.csv      # Triage queue for unverified/low-confidence apps
└── tests/                   # Pytest test suite (6 passing tests)
```

---

## Setup & Installation

### 1. Prerequisites
- Python 3.10+ (Tested on Python 3.11 & 3.12)
- Git

### 2. Clone Repository
```bash
git clone https://github.com/amaymishra1104/Composio.git
cd Composio
# Or if working inside app-research-agent folder:
cd app-research-agent
```

### 3. Create Virtual Environment & Install Dependencies
```bash
python -m venv venv
# On Windows (PowerShell):
.\venv\Scripts\Activate.ps1
# On macOS / Linux:
source venv/bin/activate

pip install -r requirements.txt
```

---

## Environment Configuration (`.env`)

Copy `.env.example` to `.env`:
```bash
cp .env.example .env
```

### Detailed Environment Variables

| Variable | Description | Default / Options |
|---|---|---|
| `LLM_PROVIDER` | LLM routing mode | `auto`, `gemini`, `openai`, `groq`, `anthropic`, `ollama`, or `heuristic` |
| `GEMINI_API_KEY` | Google Gemini API key | Optional (Recommended for high concurrency) |
| `OPENAI_API_KEY` | OpenAI API key | Optional (`gpt-4o-mini` / `gpt-4o`) |
| `GROQ_API_KEY` | Groq API key | Optional (`llama-3.3-70b-versatile`) |
| `ANTHROPIC_API_KEY` | Anthropic Claude API key | Optional (`claude-3-5-sonnet`) |
| `OPENAI_BASE_URL` | Custom OpenAI-compatible endpoint | Optional (e.g. `http://localhost:11434/v1` for Ollama) |
| `MODEL_NAME` | Model override identifier | Optional |
| `COMPOSIO_API_KEY` | Composio SDK API key for search/scraping toolkits | Optional (falls back to search router if omitted) |
| `TAVILY_API_KEY` | Tavily search API key fallback | Optional |
| `CONCURRENCY_LIMIT` | Maximum concurrent async HTTP / LLM requests | `5` |
| `REQUEST_TIMEOUT` | HTTP request timeout in seconds | `20` |
| `MAX_RETRIES` | Max exponential backoff retries per request | `3` |
| `CACHE_DIR` | Directory for atomic disk caches | `cache` |
| `RESULTS_DIR` | Output directory for pipeline deliverables | `results` |
| `VERIFY_QUOTES` | Enforce fuzzy quote verification on extract | `true` |

> **Note on Zero-API-Key Operation:** If no API keys are configured, the pipeline operates seamlessly using its internal heuristic DOM extraction engine, discovering endpoints and mining verbatim quotes directly from fetched live HTML.

---

## Step-by-Step Execution Guide

### 1. Run 3 Sample Apps Quality Check
Validates end-to-end extraction across Salesforce, Telegram, and Twenty:
```bash
python run.py --sample
```

### 2. Run Full Pass 1 Research (100 Apps)
Executes concurrent async discovery, fetching, quote extraction, and Pydantic schema validation:
```bash
python run.py --pass 1
```
*Outputs: `results/pass1.json`, `results/pass1.csv`, and `results/human_queue.csv`.*

### 3. Run Verifier & Generate Pass 2
Audits evidence URLs, performs fuzzy token matching, and cross-checks high-risk fields (`self_serve`, `existing_mcp`, `auth_methods`):
```bash
python verifier.py
# Or:
python run.py --verify
```
*Outputs: `results/pass2.json`, `results/pass2.csv`, and `results/verification_report.json`.*

### 4. Human Ground-Truth Review & Accuracy Calculation
Generates a stratified 20-app sample (2 apps per category) with blank verdict columns:
```bash
python generate_human_check.py
```
Open `human_check.csv`, fill in `correct` or `wrong` in the empty verdict columns, then compute real accuracy:
```bash
python accuracy.py
```
*Outputs: `results/accuracy.json` (Never fabricated; strictly computed from manual verdicts).*

### 5. Rebuild the Interactive Case Study Dashboard
Recompiles `index.html` with updated Pass 2 metrics, verification audit numbers, and embedded JSON data:
```bash
python build_index_html.py
```

### 6. Run Automated Test Suite
```bash
pytest -v
```
All 6 tests validate schema constraints, quote length (<25 words), verifier fuzzy matching, and strict accuracy calculation logic.

---

## Deployment Instructions

The file `index.html` is completely self-contained (zero framework dependencies, inline CSS/JS, embedded JSON data, mobile-responsive, light/dark mode).

### Option 1: Deploy to GitHub Pages (Recommended - 60 Seconds)

1. Push the repository to GitHub:
   ```bash
   git add -A
   git commit -m "Prepare repository for submission"
   git branch -M main
   git remote add origin https://github.com/amaymishra1104/Composio.git
   git push -u origin main
   ```
2. In your browser, navigate to your repository on GitHub:
   `https://github.com/amaymishra1104/Composio`
3. Click **Settings** (tab at top) → **Pages** (in left sidebar).
4. Under **Build and deployment**:
   - **Source**: Select `Deploy from a branch`.
   - **Branch**: Select `main` and folder `/ (root)`.
   - Click **Save**.
5. Within 1–2 minutes, your case study will be live at:
   `https://amaymishra1104.github.io/Composio/`

---

### Option 2: Deploy to Vercel

#### Method A: Via Vercel Web Dashboard
1. Go to [vercel.com](https://vercel.com) and log in.
2. Click **Add New...** → **Project**.
3. Import the `Composio` repository (`amaymishra1104/Composio`).
4. In Project Settings:
   - **Framework Preset**: Select `Other`.
   - **Root Directory**: `./` (or `./app-research-agent` if root is workspace).
5. Click **Deploy**. Vercel will instantly serve `index.html`.

#### Method B: Via Vercel CLI
```bash
npm install -g vercel
vercel login
vercel --prod
```

---

## Pushing to GitHub

To push the entire codebase and deliverables to GitHub:

```bash
# 1. Ensure you are in the repository directory
cd c:\Users\Amay\composio\app-research-agent

# 2. Stage all files
git add -A

# 3. Commit changes
git commit -m "Feasibility study pipeline: Pass 1, Verifier, Pass 2, and Case Study Dashboard"

# 4. Set branch to main and attach remote
git branch -M main
git remote add origin https://github.com/amaymishra1104/Composio.git

# 5. Push to GitHub
git push -u origin main
```

*(If the remote is already added, use `git remote set-url origin https://github.com/amaymishra1104/Composio.git`)*

---

## Empirical Findings Summary (100 Apps)

- **Universal Token Dominance:** 98% of audited platforms support API Keys or direct Bearer Tokens for immediate programmatic access. OAuth 2.0 is supported by 8% (mostly enterprise CRMs like Salesforce and Twenty).
- **Self-Serve Access:** 96% of audited platforms are self-serve via free tiers or self-service developer accounts. Only 4% require paid upgrades (2%) or partner sales review (2%).
- **MCP Green Field:** Only 18% of audited platforms have an existing official Model Context Protocol server. 82% of apps represent an untouched opportunity for Composio toolkits.
- **Immediate Win Feasibility:** 96 out of 100 applications are classified as `easy_win`, ready for autonomous toolkit generation today.
