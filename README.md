# AI-SEO-Agent

AI-SEO-Agent is a workspace for building repeatable, agent-assisted SEO audit workflows.

## Runtime Model

This branch now uses a monolithic FastAPI web app:

- FastAPI serves the API endpoints.
- FastAPI also serves the built frontend static files.
- You run one server and use one URL.

## API Keys

Configure API keys in your local [.env](.env) file (already ignored by git):

```env
GEMINI_API_KEY=your_gemini_key
OPENAI_API_KEY=your_openai_key
PERPLEXITY_API_KEY=your_perplexity_key
```

A template is available at [.env.example](.env.example).

## Structure

- `.github/copilot-instructions.md`: Project-wide Copilot guidance.
- `.github/instructions/`: Scoped coding instructions for Python, FastAPI, Playwright, LangGraph, and tests.
- `.github/prompts/`: Reusable audit, debug, and refactor prompts.
- `docs/`: Product, architecture, roadmap, coding, and SEO rule references.
- `skills/agentic-seo/`: Placeholder directory for a future agentic SEO skill.
- `test/`: Current experimental fetch, analysis, and captured result files.

## Getting Started (One URL)

1. Build frontend assets once (or after frontend changes):

   ```powershell
   cd frontend
   npm ci
   npm run build
   ```

2. Run the FastAPI app from repository root:

   ```powershell
   python -m uvicorn src.main:app --reload --port 8000
   ```

3. Open:

   - `http://127.0.0.1:8000`
