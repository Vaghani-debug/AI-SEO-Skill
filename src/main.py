import asyncio

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

load_dotenv()

from .models import AuditRequest, AuditResponse
from .openai_audit import AuditGenerationError, generate_audit_report
from .prompt_loader import load_audit_prompt


app = FastAPI(title="AI SEO Agent API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
    allow_credentials=False,
    allow_methods=["POST"],
    allow_headers=["*"],
)


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
