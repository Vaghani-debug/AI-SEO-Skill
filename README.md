# AI SEO Agent

AI SEO Agent is a ready-to-use MVP for generating professional SEO audit reports from a website URL. It provides a small FastAPI web application with a browser UI, deterministic SEO evidence collection, LLM-powered report writing (Gemini, Perplexity, or OpenAI — selectable via a single config switch), Markdown preview, JSON persistence, and downloadable PDF reports.

The MVP is intentionally simple: enter a URL, run one audit, review the generated SEO report, and download the PDF.

## Features

- Single-page web UI served by FastAPI
- Website URL validation and normalization, including bare domains such as `example.com`
- Homepage, `robots.txt`, and `sitemap.xml` fetching
- Deterministic extraction of visible SEO evidence, including metadata, headings, links, images, canonical tags, robots data, and sitemap data
- LLM-generated Markdown audit report based only on verified evidence, with a configurable provider (Gemini, Perplexity, or OpenAI)
- Live-web-search research (keyword, competitor, authority, and local-demand claims) backed by each provider's real citations — a claim is discarded unless its source URL was actually returned by the provider's search
- Professional PDF report generation with ReportLab
- Local JSON and PDF report storage in `reports/`
- REST API endpoints with interactive Swagger documentation
- Unit and integration tests with `pytest`

## Tech Stack

- Python 3.12+
- FastAPI
- Uvicorn
- Pydantic and Pydantic Settings
- HTTPX
- Beautiful Soup and lxml
- LLM providers: Google Gemini (`google-genai`), Perplexity, and OpenAI (`openai`) — selected at runtime via `LLM_PROVIDER`
- ReportLab
- pytest

## Requirements

- Windows, macOS, or Linux
- Python 3.12 or newer
- An API key for at least one supported LLM provider (Gemini, Perplexity, or OpenAI)
- Git, if cloning the repository

## Quick Start

These commands use Windows PowerShell because this project is developed on Windows.

1. Create and activate a virtual environment:

```powershell
python -m venv .venv
Set-ExecutionPolicy -Scope Process -ExecutionPolicy RemoteSigned
.\.venv\Scripts\Activate.ps1
```

2. Install dependencies:

```powershell
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

3. Create a `.env` file in the project root (copy `.env.example` as a starting point, then fill in the key for whichever provider you select):

```env
LLM_PROVIDER=gemini
GEMINI_API_KEY=your_gemini_api_key_here
GEMINI_MODEL=gemini-2.5-flash
FETCH_TIMEOUT_SECONDS=15
FETCH_MAX_REDIRECTS=5
REPORTS_DIR=reports
DEBUG=false
```

4. Start the application:

```powershell
uvicorn src.main:app --reload
```

5. Open the MVP UI:

```text
http://127.0.0.1:8000/
```

## Using the MVP

1. Open `http://127.0.0.1:8000/` in your browser.
2. Enter a website URL, for example `https://example.com` or `example.com`.
3. Select **Audit**.
4. Wait for the audit to fetch the site, extract SEO evidence, generate the report, and create the PDF.
5. Review the Markdown report in the browser.
6. Select **Download PDF** to save the generated report.

Generated files are saved locally in `reports/`:

- `reports/{audit_id}.json` contains the stored audit response.
- `reports/{audit_id}.pdf` contains the downloadable SEO audit report.

## API Usage

The browser UI uses the same API that is available to developers.

### Health Check

```powershell
Invoke-RestMethod -Uri http://127.0.0.1:8000/health
```

### Start an Audit

```powershell
Invoke-RestMethod `
	-Method Post `
	-Uri http://127.0.0.1:8000/api/v1/audits/ `
	-ContentType "application/json" `
	-Body '{"url":"https://example.com"}'
```

The response includes:

- `audit_id`: unique report identifier
- `url`: normalized audited URL
- `markdown_report`: generated SEO audit report
- `pdf_download_url`: relative PDF download path
- `created_at`: UTC timestamp

### Retrieve a Stored Audit

```powershell
Invoke-RestMethod -Uri http://127.0.0.1:8000/api/v1/audits/{audit_id}
```

### Download a PDF

Open this URL in a browser:

```text
http://127.0.0.1:8000/api/v1/audits/{audit_id}/pdf
```

Interactive API documentation is available at:

- Swagger UI: `http://127.0.0.1:8000/docs`
- ReDoc: `http://127.0.0.1:8000/redoc`

## Configuration

Configuration is loaded from environment variables and an optional `.env` file in the project root. See `.env.example` for the full list with defaults.

`LLM_PROVIDER` is the single switch that selects the active provider everywhere (report generation and research). Set it to `gemini`, `perplexity`, or `openai`, and configure the matching API key below — no other code or config changes are needed to switch providers. An invalid value is rejected at startup; a missing API key for the selected provider raises a clear configuration error at call time.

| Variable | Required | Default | Description |
| --- | --- | --- | --- |
| `LLM_PROVIDER` | No | `gemini` | Active LLM provider: `gemini`, `perplexity`, or `openai`. |
| `GEMINI_API_KEY` | If provider is `gemini` | Empty | Google Gemini API key. |
| `GEMINI_MODEL` | No | `gemini-2.5-flash` | Gemini model used for report generation and research. |
| `GEMINI_THINKING_LEVEL` | No | `high` | Gemini reasoning depth (`low`/`high`) — higher improves multi-step SEO reasoning at the cost of latency/tokens. |
| `PERPLEXITY_API_KEY` | If provider is `perplexity` | Empty | Perplexity API key. |
| `PERPLEXITY_MODEL` | No | `sonar-pro` | Perplexity model — always search-grounded, so every call includes live citations. |
| `OPENAI_API_KEY` | If provider is `openai` | Empty | OpenAI API key. |
| `OPENAI_MODEL` | No | `gpt-5.6` | OpenAI model used via the Responses API. |
| `OPENAI_REASONING_EFFORT` | No | `medium` | OpenAI Responses API reasoning effort. |
| `OPENAI_SEARCH_CONTEXT_SIZE` | No | `high` | OpenAI web_search tool context size (`low`/`medium`/`high`) — higher favors thorough research coverage over token cost and latency. |
| `FETCH_TIMEOUT_SECONDS` | No | `15` | Timeout for outbound website fetch requests. |
| `FETCH_MAX_REDIRECTS` | No | `5` | Maximum redirects followed while fetching a site. |
| `REPORTS_DIR` | No | `reports` | Local directory for generated JSON and PDF reports. |
| `DEBUG` | No | `false` | Enables development logging and debug behavior. |

### Live-search cost, latency, and citations

Research calls (keyword, competitor, authority, and local-demand claims) always use each provider's live web search, which costs more tokens/time than a plain report call:

- **Gemini**: research calls add the `google_search` tool; report-writing calls stay tool-free.
- **Perplexity**: `sonar-pro` is always search-grounded, so both report and research calls are equally search-backed — no separate toggle.
- **OpenAI**: research calls force the Responses API `web_search` tool (`tool_choice=required`); `OPENAI_SEARCH_CONTEXT_SIZE` trades thoroughness for cost/latency.

Every research claim must cite a source URL that the provider's own search actually returned. A claim whose `source_url` does not match one of the provider's real citations is discarded rather than shown in the report, to prevent the LLM from inventing sources.

Do not commit `.env` files or API keys.

## What the Audit Checks

The MVP uses deterministic fetching and extraction where possible. It can collect evidence for:

- HTTP status and final URL
- HTTPS usage
- Page title
- Meta description
- H1 and H2 headings
- Internal and external links
- Image URLs and image `alt` text
- `robots.txt` availability and rules
- Sitemap availability and sitemap URLs
- Canonical tag visible in static HTML

The report generator is instructed to avoid guessing. When the MVP cannot verify a topic from the fetched static evidence, the report should say:

```text
Could not be verified in this audit.
```

## MVP Limitations

This version is designed for a focused, ready-to-use audit workflow. It does not include:

- User accounts or authentication
- Billing or subscriptions
- Database persistence
- Scheduled audits
- Historical audit comparison
- Full-site crawling at scale
- Search Console data
- Backlink analysis
- Competitor analysis
- Real Core Web Vitals collection
- Browser-rendered JavaScript analysis

## Project Structure

```text
src/
	main.py                  FastAPI application entry point
	config.py                Environment-based settings
	api/
		models.py              Pydantic request and response models
		routes/audit.py        Audit and PDF API endpoints
	services/
		url_service.py         URL normalization and validation
		fetch_service.py       Homepage, robots.txt, and sitemap fetching
		extractor_service.py   Deterministic SEO evidence extraction
		prompt_loader.py       Runtime loading of report guidance files
		llm_service.py          Provider-neutral LLM boundary (Gemini/Perplexity/OpenAI adapters)
		report_service.py      LLM-backed Markdown report generation (via llm_service)
		pdf_service.py         PDF rendering
	static/
		index.html             Browser UI
		styles.css             UI styles
		app.js                 UI behavior and API calls
test/                      Unit and integration tests
docs/                      Product, architecture, SEO, scoring, and report docs
reports/                   Generated audit JSON and PDF files
```

## Testing

Run the test suite from the project root:

```powershell
python -m pytest
```

Run a focused test file:

```powershell
python -m pytest test/test_audit_api.py -v
```

## Troubleshooting

### The app starts, but audits fail with an LLM API key error

Confirm `.env` exists in the project root and contains the API key matching your `LLM_PROVIDER` setting (`GEMINI_API_KEY`, `PERPLEXITY_API_KEY`, or `OPENAI_API_KEY`).

### The browser says it cannot reach the audit server

Confirm Uvicorn is running and open `http://127.0.0.1:8000/health`.

### A website cannot be fetched

Check that the URL is public, uses `http://` or `https://`, and is reachable from your network. Some sites block automated HTTP clients or require JavaScript rendering, which is outside this MVP.

### The PDF download is missing

The Markdown audit may still be available even if PDF generation failed. Check the terminal logs and the `reports/` directory.

## Documentation

The implementation follows the repository documentation:

- `docs/PRODUCT.md`
- `docs/ARCHITECTURE.md`
- `docs/SEO_RULES.md`
- `docs/REPORT_SPECIFICATION.md`
- `docs/AI_REPORT_GUIDELINES.md`
- `docs/Roadmap/MVP_PLAN.md`

## Development Notes

- Keep deterministic SEO checks in services, not API routes.
- Keep LLM usage isolated to report generation.
- Update the relevant docs when audit behavior, scoring, or report structure changes.
- Add or update tests for behavior changes.
- Do not commit secrets, generated API keys, or local environment files.

## License

No license file is currently included in this repository.
