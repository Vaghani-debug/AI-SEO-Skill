import asyncio
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles

load_dotenv()

from .models import AuditRequest, AuditResponse
from .llm_audit import AuditGenerationError, generate_audit_report
from .prompt_loader import load_audit_prompt


app = FastAPI(title="AI SEO Agent API")

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_FRONTEND_DIST_DIR = _PROJECT_ROOT / "frontend" / "dist"


@app.get("/health")
def health_check() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/audit", response_model=AuditResponse)
async def create_audit(request: AuditRequest) -> AuditResponse:
    try:
        prompt_instruction = load_audit_prompt()
    except OSError as exc:
        raise HTTPException(
            status_code=500,
            detail="SEO audit prompt file could not be loaded.",
        ) from exc

    url = str(request.url)

    try:
        report_markdown = await asyncio.to_thread(
            generate_audit_report,
            url,
            prompt_instruction,
        )
    except AuditGenerationError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return AuditResponse(url=url, report_markdown=report_markdown)


# Serve the built frontend from the same FastAPI process so the app uses one URL.
app.mount("/", StaticFiles(directory=_FRONTEND_DIST_DIR, html=True), name="frontend")
