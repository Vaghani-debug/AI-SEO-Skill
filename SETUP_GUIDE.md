# AI SEO Agent — New Developer / Handover Setup Guide

This guide is for someone receiving this repository for the first time. It
covers prerequisites, automated and manual setup, a tour of the repo, a
review of `requirements.txt`, and known quirks worth knowing up front.

If you only want the fastest path, jump to **[Automated Setup](#2-automated-setup-recommended)**.

---

## 1. Prerequisites

| Requirement | Notes |
| --- | --- |
| **OS** | Windows (this project is developed and tested on Windows PowerShell). It is plain Python/FastAPI, so macOS/Linux will also work, but the setup script below is PowerShell-only. |
| **Python** | 3.12 or newer. The reference environment uses **3.14.2** — package versions in `requirements.txt` were chosen for 3.14 wheel availability. |
| **Git** | To clone the repository and manage branches. |
| **An LLM provider API key** | At least one of: Google Gemini, Perplexity, or OpenAI. You only need the key for the provider you plan to use — see [Getting an API key](#4-getting-an-api-key). |
| **No Docker, no database, no cloud account required** | This is a local, file-based MVP (SQLite/Postgres-free; reports are stored as JSON/PDF files under `reports/`). |

---

## 2. Automated Setup (recommended)

A PowerShell setup script is provided at [`scripts/setup.ps1`](scripts/setup.ps1). It:

1. Verifies Python 3.12+ is available.
2. Creates `.venv` if it doesn't already exist (safe to re-run — never recreates an existing venv).
3. Activates the venv for the current session and upgrades `pip`.
4. Installs everything in `requirements.txt`.
5. Copies `.env.example` to `.env` if `.env` doesn't already exist (never overwrites an existing `.env`).
6. Runs the test suite to confirm the environment is healthy.
7. Prints the next steps (how to fill in `.env`, how to start the server).

Run it from the repository root:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy RemoteSigned
.\scripts\setup.ps1
```

Optional flags:

```powershell
.\scripts\setup.ps1 -SkipTests   # Skip running the test suite after install
.\scripts\setup.ps1 -Start       # Start the dev server automatically once setup finishes
```

After it finishes, open `.env` and fill in the API key for whichever `LLM_PROVIDER` you intend to use (see [Configuration](#5-configuration-env)), then start the app:

```powershell
uvicorn src.main:app --reload
```

Open `http://127.0.0.1:8000/` in a browser.

---

## 3. Manual Setup (if you prefer not to use the script)

```powershell
# 1. Create and activate a virtual environment
python -m venv .venv
Set-ExecutionPolicy -Scope Process -ExecutionPolicy RemoteSigned
.\.venv\Scripts\Activate.ps1

# 2. Install dependencies
python -m pip install --upgrade pip
python -m pip install -r requirements.txt

# 3. Create your .env file
Copy-Item .env.example .env
# then edit .env and fill in the API key for your chosen LLM_PROVIDER

# 4. Run the tests to confirm everything works
python -m pytest test/ -q

# 5. Start the app
uvicorn src.main:app --reload
```

Then open `http://127.0.0.1:8000/`.

---

## 4. Getting an API key

You only need **one** of these, matching whichever `LLM_PROVIDER` you set in `.env`:

| Provider | Where to get a key |
| --- | --- |
| Google Gemini | [aistudio.google.com/apikey](https://aistudio.google.com/apikey) |
| Perplexity | [perplexity.ai/settings/api](https://www.perplexity.ai/settings/api) |
| OpenAI | [platform.openai.com/api-keys](https://platform.openai.com/api-keys) |

Never commit `.env` or paste a real key into a chat, issue, or commit message. `.env` is already listed in `.gitignore`.

---

## 5. Configuration (`.env`)

Copy `.env.example` to `.env` and fill in real values. Full variable reference is documented in [`README.md`](README.md#configuration) — the most important ones to check on a fresh setup are:

- `LLM_PROVIDER` — `gemini` (default), `perplexity`, or `openai`.
- `<PROVIDER>_API_KEY` — must match whichever provider you selected above.
- `REPORTS_DIR` — defaults to `reports/`, created automatically on first run.

The UI also lets you override the provider **per audit** via a dropdown (Perplexity/Gemini/OpenAI) without touching `.env` — but the corresponding API key must still be configured in `.env` for that provider to work.

---

## 6. Requirements Review (`requirements.txt`)

`requirements.txt` was audited against the currently installed environment (`pip freeze`) as part of this handover. **Every pinned version matches what is actually installed and passing all 386 tests — nothing needs to be added or removed.** Specifically verified:

| Package | Pinned | Installed | Status |
| --- | --- | --- | --- |
| fastapi | 0.115.6 | 0.115.6 | ✅ matches |
| uvicorn | 0.32.1 | 0.32.1 | ✅ matches |
| pydantic | 2.13.4 | 2.13.4 | ✅ matches |
| pydantic-settings | 2.7.0 | 2.7.0 | ✅ matches |
| python-dotenv | 1.0.1 | 1.0.1 | ✅ matches |
| httpx | 0.28.1 | 0.28.1 | ✅ matches |
| beautifulsoup4 | 4.12.3 | 4.12.3 | ✅ matches |
| lxml | 6.1.1 | 6.1.1 | ✅ matches |
| google-genai | 2.18.1 | 2.18.1 | ✅ matches |
| openai | 2.44.0 | 2.44.0 | ✅ matches |
| reportlab | 4.2.5 | 4.2.5 | ✅ matches |
| pytest | 8.3.4 | 8.3.4 | ✅ matches |
| pytest-asyncio | 0.24.0 | 0.24.0 | ✅ matches |

**Nothing extra is required.** Notably:

- **No Playwright dependency is needed**, despite `AGENTS.md`/`docs/ARCHITECTURE.md` describing "Playwright" as the website crawler. The actual, tested implementation (`src/services/fetch_service.py`) fetches sites with `httpx` and parses HTML with `beautifulsoup4`/`lxml` — no headless browser is used anywhere in the working code path. Treat the Playwright references in those two docs as stale/aspirational; do not install Playwright or add it to `requirements.txt` unless you deliberately change the crawler implementation.
- **No PDF-parsing/Markdown-parsing package is needed** beyond `reportlab`. PDF export (`src/services/pdf_service.py`) uses a small hand-written Markdown parser targeting ReportLab's mini-HTML, not the third-party `markdown` package.
- **No database, ORM, or migration tool is required.** Reports persist as flat JSON/PDF files under `reports/` (gitignored).

If you add a new feature that needs a new package, install it into `.venv`, then run `pip freeze` and copy just that one new line into `requirements.txt` under the matching section — don't regenerate the whole file, since `pip freeze` also captures transitive dependencies already covered by the direct pins above.

---

## 7. Repository Tour

```text
AI SEO Skill/
├── src/                      Application code
│   ├── main.py                FastAPI app entry point, static file mount, /health
│   ├── config.py              Settings (env vars) — see .env.example
│   ├── api/
│   │   ├── models.py           Request/response Pydantic models
│   │   └── routes/audit.py     All /api/v1/audits/* endpoints (thin — logic lives in services/)
│   ├── services/               All business logic: fetch, extract, score, LLM calls, PDF, report writing
│   └── static/                 The actual browser UI (plain HTML/CSS/JS — NOT the frontend/ folder below)
├── test/                      Active pytest suite — this is the real test directory
├── docs/                      Product/architecture/SEO methodology/report-spec documentation
├── reports/                   Generated JSON + PDF audit output (gitignored, created automatically)
├── scripts/                   Setup automation — see setup.ps1 added in this handover
├── .env.example               Template for your local .env (copy, don't edit directly)
├── requirements.txt           Python dependencies (see review above)
└── pytest.ini                 pytest-asyncio configuration (asyncio_mode = auto)
```

Two folders are **legacy/unused — safe to ignore**, and you will not see them at all after a fresh `git clone` since both are excluded via `.gitignore`:

- **`frontend/`** — an empty scaffold (only `node_modules/`/`dist/` build artifacts, no `package.json` or source). The real UI is `src/static/`, served directly by FastAPI. There is nothing to build or run in `frontend/`.
- **`tests/`** (plural, no `s` missing — note the near-duplicate name vs. `test/`) — empty except for a stray `__pycache__/`. The real, actively maintained test suite is `test/` (singular). Always run `pytest test/`, not `pytest tests/`.

---

## 8. Verifying Your Setup

```powershell
# Run the full test suite (should show "386 passed" or similar, 0 failed)
python -m pytest test/ -q --tb=short

# Start the server
uvicorn src.main:app --reload

# In another terminal, or a browser: confirm the health endpoint responds
Invoke-RestMethod -Uri http://127.0.0.1:8000/health
```

Then open `http://127.0.0.1:8000/`, select an LLM provider from the dropdown, enter a URL (e.g. `https://example.com`), and click **Audit**.

---

## 9. Known Quirks (save yourself some debugging time)

- **Static file caching**: `src/main.py` uses a custom `_NoCacheStaticFiles` class specifically to prevent browsers from serving a stale cached copy of `app.js`/`styles.css`/`index.html` after you edit them. If you still see old UI after a change, do a hard refresh (Ctrl+Shift+R) once — this only happens for tabs that were open *before* this fix existed in a given browser session.
- **`uvicorn --reload` on Windows** occasionally prints a `KeyboardInterrupt` traceback from its reloader subprocess when multiple files change in quick succession. This is a known `uvicorn`/`watchfiles` quirk on Windows, not an application bug — the server restarts successfully immediately afterward. If you don't see "Application startup complete." after a reload, just check whether a newer "Started server process [PID]" line follows the traceback.
- **`llm_provider` in the UI is optional at the API layer, required in the UI**: `AuditRequest.llm_provider` is optional server-side (falls back to `LLM_PROVIDER` in `.env`) so existing API-only callers never break, but the browser UI's JavaScript blocks submission until a provider is explicitly selected. This is intentional — see `src/static/app.js`.
- **PDF filenames** are generated as `{site-slug}-{dd_mm_yyyy}.pdf` (TLD stripped, e.g. `example-15_09_2026.pdf`), not from the audit ID — this is deliberate, not a bug.

---

## 10. Where to Go Next

- [`README.md`](README.md) — product overview, full API reference, full `.env` variable table.
- [`AGENTS.md`](AGENTS.md) — repository conventions and working principles for AI coding agents (and useful for humans too).
- [`docs/PRODUCT.md`](docs/PRODUCT.md), [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md), [`docs/SEO_RULES.md`](docs/SEO_RULES.md), [`docs/AI_REPORT_GUIDELINES.md`](docs/AI_REPORT_GUIDELINES.md), [`docs/CODING_GUIDELINES.md`](docs/CODING_GUIDELINES.md) — product/architecture/methodology source-of-truth documents. Note: `.github/copilot-instructions.md` also references a `docs/REPORT_SPECIFICATION.md`, which does not currently exist in `docs/` — another stale-doc gap worth flagging to the team, alongside the Playwright mention noted above.
- `.github/instructions/` — path-specific coding conventions (FastAPI, Python, SEO, testing, git workflow) enforced throughout this repo.
