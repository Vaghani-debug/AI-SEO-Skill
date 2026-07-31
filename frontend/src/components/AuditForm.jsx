import { useState } from "react";

export function AuditForm({ onSubmit, isLoading }) {
  const [url, setUrl] = useState("");
  const [error, setError] = useState("");

  function handleSubmit(event) {
    event.preventDefault();

    const trimmedUrl = url.trim();
    if (!trimmedUrl) {
      setError("Please enter a website URL.");
      return;
    }

    setError("");
    onSubmit(trimmedUrl);
  }

  return (
    <form className="audit-form" onSubmit={handleSubmit} noValidate>
      <label htmlFor="website-url">Website URL</label>
      <div className="form-row">
        <input
          id="website-url"
          name="websiteUrl"
          type="url"
          value={url}
          onChange={(event) => {
            setUrl(event.target.value);
            if (error) {
              setError("");
            }
          }}
          placeholder="https://example.com"
          aria-describedby={error ? "website-url-error" : undefined}
          aria-invalid={error ? "true" : "false"}
          disabled={isLoading}
        />
        <button type="submit" disabled={isLoading}>
          {isLoading ? "Generating..." : "Generate SEO Audit Report"}
        </button>
      </div>
      {error ? (
        <p className="form-error" id="website-url-error" role="alert">
          {error}
        </p>
      ) : null}
    </form>
  );
}
