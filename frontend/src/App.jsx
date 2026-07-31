import { useState } from "react";
import { AuditForm } from "./components/AuditForm.jsx";
import { SubmittedUrl } from "./components/SubmittedUrl.jsx";
import { submitAuditRequest } from "./services/auditService.js";

export default function App() {
  const [submittedUrl, setSubmittedUrl] = useState("");
  const [isLoading, setIsLoading] = useState(false);

  async function handleAuditSubmit(url) {
    setIsLoading(true);
    setSubmittedUrl("");

    const result = await submitAuditRequest(url);

    setSubmittedUrl(result.url);
    setIsLoading(false);
  }

  return (
    <main className="app-shell">
      <section className="audit-panel" aria-labelledby="audit-title">
        <div className="audit-copy">
          <h1 id="audit-title">SEO Audit MVP</h1>
          <p>
            Enter a website URL to start the first step toward a structured SEO
            audit report.
          </p>
        </div>

        <AuditForm onSubmit={handleAuditSubmit} isLoading={isLoading} />

        <SubmittedUrl url={submittedUrl} />
      </section>
    </main>
  );
}
