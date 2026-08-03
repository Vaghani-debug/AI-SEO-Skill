export async function submitAuditRequest(url) {
  let response;

  try {
    response = await fetch("/api/audit", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ url }),
    });
  } catch (error) {
    throw new Error(
      "The backend API is not reachable. Please make sure FastAPI is running on port 8000."
    );
  }

  const data = await response.json().catch(() => null);

  if (!response.ok) {
    const message = getErrorMessage(data, response.status);
    throw new Error(message);
  }

  return data;
}

function getErrorMessage(data, status) {
  if (typeof data?.detail === "string") {
    return data.detail;
  }

  if (Array.isArray(data?.detail)) {
    return data.detail
      .map((item) => item.msg)
      .filter(Boolean)
      .join(" ");
  }

  return `The SEO audit could not be generated. Backend returned HTTP ${status}.`;
}
