export function SubmittedUrl({ url }) {
  if (!url) {
    return null;
  }

  return (
    <div className="submitted-url" aria-live="polite">
      <span>Submitted URL</span>
      <strong>{url}</strong>
    </div>
  );
}
