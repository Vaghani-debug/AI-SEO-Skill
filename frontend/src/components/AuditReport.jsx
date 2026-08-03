import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

export function AuditReport({ markdown }) {
  if (!markdown) {
    return null;
  }

  return (
    <article className="audit-report" aria-labelledby="audit-report-title">
      <h2 id="audit-report-title">Generated SEO Audit Report</h2>
      <div className="markdown-report">
        <ReactMarkdown remarkPlugins={[remarkGfm]}>{markdown}</ReactMarkdown>
      </div>
    </article>
  );
}
