# AI SEO Agent

AI SEO Agent is a ready-to-use MVP for generating professional SEO audit reports from a website URL. It provides a small FastAPI web application with a browser UI, deterministic SEO evidence collection, Gemini-powered report writing, Markdown preview, JSON persistence, and downloadable PDF reports.

The MVP is intentionally simple: enter a URL, run one audit, review the generated SEO report, and download the PDF.

## Features

- Single-page web UI served by FastAPI
- Website URL validation and normalization, including bare domains such as `example.com`
- Homepage, `robots.txt`, and `sitemap.xml` fetching
- Deterministic extraction of visible SEO evidence, including metadata, headings, links, images, canonical tags, robots data, and sitemap data
- Gemini-generated Markdown audit report based only on verified evidence
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
- Google Gemini SDK
- ReportLab
- pytest

## Requirements

- Windows, macOS, or Linux
- Python 3.12 or newer
- A Google Gemini API key
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

3. Create a `.env` file in the project root:

```env
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

Configuration is loaded from environment variables and an optional `.env` file in the project root.

| Variable | Required | Default | Description |
| --- | --- | --- | --- |
| `GEMINI_API_KEY` | Yes | Empty | Google Gemini API key used to generate reports. |
| `GEMINI_MODEL` | No | `gemini-2.5-flash` | Gemini model used for report generation. |
| `FETCH_TIMEOUT_SECONDS` | No | `15` | Timeout for outbound website fetch requests. |
| `FETCH_MAX_REDIRECTS` | No | `5` | Maximum redirects followed while fetching a site. |
| `REPORTS_DIR` | No | `reports` | Local directory for generated JSON and PDF reports. |
| `DEBUG` | No | `false` | Enables development logging and debug behavior. |

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
		report_service.py      Gemini-backed Markdown report generation
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

### The app starts, but audits fail with a Gemini API key error

Confirm `.env` exists in the project root and contains `GEMINI_API_KEY`.

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
- `docs/SCORING_ENGINE.md`
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
