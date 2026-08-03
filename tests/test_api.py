from fastapi.testclient import TestClient

from src.main import app


def test_create_audit_rejects_invalid_url() -> None:
    client = TestClient(app)

    response = client.post("/api/audit", json={"url": "not-a-url"})

    assert response.status_code == 422


def test_create_audit_returns_markdown(monkeypatch) -> None:
    def fake_load_audit_prompt() -> str:
        return "Return Markdown."

    def fake_generate_audit_report(url: str, prompt_instruction: str) -> str:
        assert url == "https://example.com/"
        assert prompt_instruction == "Return Markdown."
        return "# SEO Audit\n\n- Looks ready."

    monkeypatch.setattr("src.main.load_audit_prompt", fake_load_audit_prompt)
    monkeypatch.setattr("src.main.generate_audit_report", fake_generate_audit_report)

    client = TestClient(app)
    response = client.post("/api/audit", json={"url": "https://example.com"})

    assert response.status_code == 200
    assert response.json() == {
        "url": "https://example.com/",
        "report_markdown": "# SEO Audit\n\n- Looks ready.",
    }
