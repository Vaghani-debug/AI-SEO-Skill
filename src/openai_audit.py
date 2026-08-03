import os

from openai import OpenAI, OpenAIError


DEFAULT_MODEL = "gpt-5.2"


class AuditGenerationError(RuntimeError):
    """Raised when the audit report cannot be generated."""


def generate_audit_report(url: str, prompt_instruction: str) -> str:
    """Generate an SEO audit report in Markdown using the OpenAI Responses API."""
    if not os.getenv("OPENAI_API_KEY"):
        raise AuditGenerationError("OPENAI_API_KEY is not configured.")

    client = OpenAI()
    model = os.getenv("OPENAI_MODEL", DEFAULT_MODEL)

    try:
        response = client.responses.create(
            model=model,
            instructions=prompt_instruction,
            input=url,
            tools=[
                {
                    "type": "web_search_preview",
                    "search_context_size": "high",
                }
            ],
            store=False,
        )
    except OpenAIError as exc:
        raise AuditGenerationError("OpenAI audit generation failed.") from exc

    report = response.output_text.strip()
    if not report:
        raise AuditGenerationError("OpenAI returned an empty audit report.")

    return report
