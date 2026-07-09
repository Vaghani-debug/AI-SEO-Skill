# MVP Plan: Simple SEO Audit UI

## Objective

Build a minimum-viable UI for the AI SEO Agent where a user can enter a website URL, click **Audit**, receive a generated SEO audit report, and download the report as a PDF.

The recommended implementation is a small FastAPI application that serves a vanilla HTML/CSS/JavaScript page and uses Python services for URL validation, website fetching, SEO data extraction, LLM report generation, and PDF generation.

This approach keeps the MVP simple while staying aligned with the repository architecture:

- Single AI agent workflow
- Python and FastAPI stack
- Deterministic SEO evidence collection where possible
- AI used for report writing, explanation, prioritization, and recommendations
- PDF as the primary client-ready deliverable

## MVP Scope

### In Scope

- Single web page UI
- Website URL input
- Audit button
- Loading and error states
- Generated Markdown report preview
- Downloadable PDF report
- Basic deterministic homepage fetch
- Basic robots.txt and sitemap checks
- Verified metadata, headings, links, and image extraction where available
- LLM-generated report based on verified evidence
- Use of existing prompt, skill, and report documentation as runtime context

### Out of Scope

- User accounts
- Authentication
- Billing
- Multi-user workspaces
- Scheduled audits
- Historical audit comparisons
- Database persistence
- Full-site crawling at scale
- Advanced competitor analysis
- Search Console integration
- Backlink analysis
- Real Core Web Vitals collection
- Browser-rendered schema detection in the first version
- Interactive report chat in the first UI version

## Recommended Architecture

```text
User
	|
	v
HTML UI
	|
	v
FastAPI audit endpoint
	|
	v
URL validation and normalization
	|
	v
Homepage, robots.txt, and sitemap fetch
	|
	v
Verified SEO data extraction
	|
	v
Prompt, skill, and report guideline loading
	|
	v
LLM Markdown report generation
	|
	v
PDF generation
	|
	v
Markdown preview + PDF download
```

## Step-by-Step Implementation Plan

### 1. Confirm Input Behavior

Accept a website URL as the primary input.

If the user enters a bare domain, normalize it automatically.

Examples:

- `www.truelinesolution.com` becomes `https://www.truelinesolution.com`
- `truelinesolution.com` becomes `https://truelinesolution.com`
- `https://www.truelinesolution.com` remains unchanged

Validation rules:

- Reject empty values.
- Reject unsupported schemes such as `ftp://`.
- Require a valid domain.
- Return a clear user-friendly error if the URL cannot be fetched.

### 2. Add Runtime Dependencies

Update `requirements.txt` with the minimum dependencies needed for the MVP.

Recommended packages:

```text
fastapi
uvicorn[standard]
pydantic
python-dotenv
httpx
beautifulsoup4
reportlab
pytest
```

Add the LLM SDK that matches the configured provider. If the project uses Gemini, add the appropriate Google Generative AI package.

Use `reportlab` for the first PDF implementation because it is simple and Windows-friendly.

Do not add Playwright in the first UI implementation unless browser-rendered extraction is required immediately. Static HTTP fetching is enough for a first working demo, as long as unverifiable items are clearly marked.

### 3. Create the FastAPI Application Shell

Create the base application files:

```text
src/main.py
src/config.py
src/api/models.py
src/api/routes/audit.py
```

Responsibilities:

- `src/main.py`: FastAPI app entry point, static file mounting, route registration.
- `src/config.py`: environment-based configuration for API keys, timeouts, output directory, and audit limits.
- `src/api/models.py`: Pydantic request and response models.
- `src/api/routes/audit.py`: audit and PDF download endpoints.

Recommended API models:

- `AuditRequest`
- `AuditResult`
- `AuditError`
- `ReportDownload`

### 4. Create the UI Page

Create a small static UI served by FastAPI.

Files:

```text
src/static/index.html
src/static/styles.css
src/static/app.js
```

UI requirements:

- URL input field
- Audit button
- Loading state while the audit runs
- Error message area
- Markdown report preview area
- Download PDF button after report generation

Keep the UI intentionally simple. Do not introduce React, Vite, Next.js, or a separate frontend build for the first MVP.

### 5. Implement URL Validation and Normalization

Create:

```text
src/services/url_service.py
```

Responsibilities:

- Normalize user input into a full URL.
- Validate the domain.
- Enforce supported protocols.
- Return structured validation errors.

This service keeps URL handling out of the API route and makes it easy to test.

### 6. Implement Website Fetching

Create:

```text
src/services/fetch_service.py
```

The first MVP should fetch:

- Homepage
- `/robots.txt`
- `/sitemap.xml`
- Sitemap URLs referenced inside robots.txt

Use timeouts and clear error handling.

Do not silently ignore failed fetches. If a file cannot be fetched, record that as audit evidence and pass it to the report generator.

### 7. Implement Verified SEO Data Extraction

Create:

```text
src/services/extractor_service.py
```

Extract only data that can be verified from fetched content.

Recommended extracted fields:

- Final URL
- HTTP status
- HTTPS status
- Page title, if available
- Meta description, if available
- H1 and H2 headings
- Internal links
- External links
- Image URLs
- Image ALT text visible in HTML
- robots.txt rules
- Sitemap URLs and accessibility status
- Basic canonical tag, if visible in static HTML

Mark these as unverifiable in the first MVP unless a proper tool is added:

- Core Web Vitals
- Mobile friendliness
- Browser-rendered schema
- JavaScript-injected metadata
- Google Search Console data
- Keyword rankings
- Backlinks
- Competitors
- Full broken-link crawl

Use the required phrase:

```text
Could not be verified in this audit.
```

### 8. Load Prompt, Skill, and Report Documents at Runtime

Create:

```text
src/services/prompt_loader.py
```

The web application must explicitly read the project guidance files. Copilot skills and prompts are not automatically applied inside the running FastAPI app unless the app loads them.

Load these files:

```text
.github/prompts/seo_audit.prompt.md
.agents/skills/seo-audit-skill/SKILL.md
docs/REPORT_SPECIFICATION.md
docs/AI_REPORT_GUIDELINES.md
```

Use them as runtime context for the LLM report generator.

Purpose of each file:

- `seo_audit.prompt.md`: report prompt and user-facing audit instruction
- `SKILL.md`: SEO audit methodology and checks
- `REPORT_SPECIFICATION.md`: official report structure source of truth
- `AI_REPORT_GUIDELINES.md`: tone, hallucination prevention, severity, and recommendation rules

### 9. Implement Report Generation

Create:

```text
src/services/report_service.py
```

Responsibilities:

- Combine extracted verified audit evidence with the loaded prompt, skill, and docs.
- Call the LLM to generate the Markdown report.
- Instruct the LLM to use only verified evidence.
- Instruct the LLM to state `Could not be verified in this audit` when evidence is unavailable.
- Return Markdown as the canonical first MVP output.

The LLM must not invent:

- Metadata
- Broken links
- Schema
- Page speed scores
- Core Web Vitals
- Keyword rankings
- Backlinks
- Competitors

### 10. Store Generated Reports

For the MVP, use one of these simple approaches:

Option A: In-memory storage

- Fastest to build.
- Good for local demo.
- Reports disappear when the server restarts.

Option B: Local `reports/` folder

- Slightly more work.
- Better for demo reliability.
- Allows generated PDFs and Markdown reports to be downloaded after creation.

Recommended MVP choice: local `reports/` folder keyed by generated `audit_id`.

Example:

```text
reports/
	<audit_id>.md
	<audit_id>.pdf
```

### 11. Implement PDF Generation

Create:

```text
src/services/pdf_service.py
```

Responsibilities:

- Convert generated Markdown into a readable PDF.
- Preserve headings, paragraphs, bullets, and simple tables where practical.
- Include website URL and audit date.
- Save the PDF to the local reports directory.
- Return a file path for download.

For the first MVP, prioritize readable output over perfect design.

### 12. Add API Routes

Create endpoints in:

```text
src/api/routes/audit.py
```

Recommended endpoints:

```text
POST /api/audits
GET /api/audits/{audit_id}
GET /api/audits/{audit_id}/pdf
```

`POST /api/audits` should:

- Accept the user URL.
- Normalize and validate the URL.
- Fetch website evidence.
- Extract verified SEO data.
- Generate the Markdown report.
- Generate the PDF.
- Return the audit ID, normalized URL, Markdown report, and PDF download URL.

`GET /api/audits/{audit_id}` should:

- Return the stored Markdown report if available.

`GET /api/audits/{audit_id}/pdf` should:

- Stream the generated PDF as a download.

### 13. Add Error Handling

Handle these failures clearly:

- Invalid URL
- Unsupported URL scheme
- DNS failure
- Connection timeout
- HTTP fetch failure
- Missing prompt, skill, or docs file
- LLM API key missing
- LLM generation failure
- PDF generation failure
- Missing audit ID

API errors should return structured responses, not raw exceptions.

User-facing UI errors should be plain English and actionable.

### 14. Add Focused Tests

Create tests for the first MVP slice.

Recommended files:

```text
test/test_url_service.py
test/test_extractor_service.py
test/test_prompt_loader.py
test/test_pdf_service.py
test/test_audit_api.py
```

Test coverage should include:

- URL normalization
- Invalid URL handling
- HTML title extraction
- Meta description extraction
- H1/H2 extraction
- Link extraction
- Image ALT extraction
- Prompt and docs file loading
- PDF file generation
- API success response with mocked fetch and LLM services
- API error response for invalid input

### 15. Manual Demo Validation

Run the application locally:

```text
uvicorn src.main:app --reload
```

Open:

```text
http://127.0.0.1:8000/
```

Test with:

```text
www.truelinesolution.com
```

Confirm:

- The page loads.
- The URL can be submitted.
- A loading state appears.
- A Markdown report is generated.
- A PDF download button appears.
- The PDF downloads successfully.
- Unverifiable findings are marked with `Could not be verified in this audit`.
- The report does not invent unavailable performance, schema, backlink, ranking, or competitor data.

## File Plan

### Files to Modify

```text
requirements.txt
```

### Files to Create

```text
src/main.py
src/config.py
src/api/models.py
src/api/routes/audit.py
src/static/index.html
src/static/styles.css
src/static/app.js
src/services/url_service.py
src/services/fetch_service.py
src/services/extractor_service.py
src/services/prompt_loader.py
src/services/report_service.py
src/services/pdf_service.py
test/test_url_service.py
test/test_extractor_service.py
test/test_prompt_loader.py
test/test_pdf_service.py
test/test_audit_api.py
```

### Existing Files Used as Runtime Context

```text
.github/prompts/seo_audit.prompt.md
.agents/skills/seo-audit-skill/SKILL.md
docs/REPORT_SPECIFICATION.md
docs/AI_REPORT_GUIDELINES.md
```

## Verification Checklist

1. Install dependencies:

	 ```text
	 pip install -r requirements.txt
	 ```

2. Run tests:

	 ```text
	 pytest test/
	 ```

3. Run the app:

	 ```text
	 uvicorn src.main:app --reload
	 ```

4. Open the UI:

	 ```text
	 http://127.0.0.1:8000/
	 ```

5. Test an audit:

	 ```text
	 www.truelinesolution.com
	 ```

6. Confirm report and PDF behavior.

7. Review generated report for hallucination prevention.

8. Run `git status` and review changed files before committing.

## Key Decisions

- Use FastAPI plus vanilla HTML/CSS/JavaScript for the first UI.
- Use Markdown as the canonical report output.
- Generate PDF as an export from the Markdown report.
- Use ReportLab for the first PDF implementation.
- Read the prompt, skill, and report docs from disk at runtime.
- Keep the audit evidence-based and transparent.
- Use `Could not be verified in this audit` for unavailable checks.
- Avoid React, database persistence, background queues, authentication, and advanced crawling in the first UI.

## Future Enhancements

These should be considered after the first UI works end to end:

1. Add Playwright for browser-rendered extraction.
2. Add JavaScript-rendered schema detection.
3. Add PageSpeed Insights integration for performance and Core Web Vitals.
4. Add full-site crawling with crawl limits.
5. Add persistent audit history.
6. Add interactive chat based on the generated report.
7. Add improved branded PDF templates.
8. Add Search Console integration.
9. Add background job processing if audit generation becomes slow.
10. Add authentication and multi-user support in a later SaaS phase.

## Implementation Order

Recommended build sequence:

1. Add dependencies.
2. Create FastAPI shell.
3. Create static UI.
4. Implement URL validation.
5. Implement website fetch service.
6. Implement data extraction service.
7. Implement prompt and docs loader.
8. Implement report generation service.
9. Implement PDF generation service.
10. Wire API routes.
11. Add tests.
12. Run manual demo.
13. Review generated report quality.
14. Commit changes.

## Definition of Done

The MVP UI is complete when:

- A user can open the local web page.
- A user can enter a website URL.
- A user can click **Audit**.
- The app generates an SEO report using verified website evidence.
- The app uses the SEO audit prompt, SEO audit skill, report specification, and AI report guidelines as runtime context.
- The report is shown in the UI.
- A PDF version can be downloaded.
- Unverifiable fields are clearly marked.
- Tests cover the critical services and API behavior.
- The app runs locally with a documented command.
- Git changes are reviewed before commit.
