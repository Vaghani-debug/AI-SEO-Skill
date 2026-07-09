/**
 * app.js — AI SEO Agent MVP UI
 *
 * This script handles all browser-side interactions:
 *   1. Reads the URL the user typed in the input field
 *   2. Validates it locally before sending it to the server
 *   3. POSTs it to the FastAPI audit endpoint
 *   4. Shows a loading state while the server is working
 *   5. Renders the returned Markdown report as HTML using marked.js
 *   6. Enables the PDF download button
 *   7. Handles and displays errors in a user-friendly way
 *
 * No framework or build step is required — this is plain ES6 JavaScript
 * loaded directly by the browser.
 */

"use strict";
// "use strict" enables strict mode: catches common bugs like undeclared variables

// ---------------------------------------------------------------------------
// Module-level state
// ---------------------------------------------------------------------------

let currentAuditId = null;
// currentAuditId stores the audit identifier returned by the API so the
// PDF download function knows which report to request

// ---------------------------------------------------------------------------
// DOM element references
// ---------------------------------------------------------------------------
// We collect these once at startup rather than querying the DOM every time a
// function runs (better performance and easier to read).

const urlInput       = document.getElementById("website-url");    // The URL text input
const loadingSection = document.getElementById("loading-section"); // Spinner + message shown while waiting
const errorSection   = document.getElementById("error-section");   // Red error card shown on failure
const errorMessage   = document.getElementById("error-message");   // Paragraph filled with the error text
const reportSection  = document.getElementById("report-section");  // White card containing the audit report
const reportMeta     = document.getElementById("report-meta");     // Small text showing URL and audit time
const reportBody     = document.getElementById("report-body");     // Article element where Markdown is rendered
const auditBtn       = document.getElementById("audit-btn");       // The "Audit" button
const downloadBtn    = document.getElementById("download-btn");    // The "Download PDF" button


// ---------------------------------------------------------------------------
// handleAudit()
// ---------------------------------------------------------------------------
// Called when the user clicks the Audit button.
// Orchestrates the full client-side workflow.

async function handleAudit() {
  // async function: allows us to use await for the fetch call without blocking the browser

  const rawUrl = urlInput.value.trim();
  // Read and trim the URL the user typed; trim() removes accidental leading/trailing spaces

  // --- Local pre-validation ------------------------------------------------

  if (!rawUrl) {
    // Guard: reject empty input before making a network request
    showError("Please enter a website URL before clicking Audit.");
    return; // Stop here — no point calling the API with no URL
  }

  if (rawUrl.startsWith("ftp://") || rawUrl.startsWith("file://")) {
    // Guard: reject unsupported schemes that the backend also rejects
    showError("Only http:// and https:// URLs are supported. Please enter a web address.");
    return;
  }

  // --- Show loading state --------------------------------------------------

  resetSections();
  // Clear any previous report, error, or loading display before starting a new audit

  showSection(loadingSection);
  // Reveal the spinner and "Analysing website…" message

  disableAuditButton(true);
  // Disable the button so the user cannot submit a second request while one is in progress


  // --- Call the audit API --------------------------------------------------

  try {
    // try/catch handles network errors (offline, DNS failure, timeout)

    const response = await fetch("/api/v1/audits/", {
      // POST to the FastAPI audit endpoint defined in src/api/routes/audit.py
      method: "POST",
      // POST method: we are sending data (the URL) to the server

      headers: {
        "Content-Type": "application/json",
        // Tell the server the body is JSON so FastAPI's Pydantic model can parse it
      },

      body: JSON.stringify({ url: rawUrl }),
      // JSON.stringify converts the JS object to a JSON string: {"url": "https://..."}
      // This maps to the AuditRequest model in src/api/models.py
    });

    // --- Handle non-OK HTTP responses ------------------------------------

    if (!response.ok) {
      // response.ok is true for 2xx status codes; false for 4xx/5xx errors

      let errorText = "The audit failed. Please check the URL and try again.";
      // Default message used if the server does not return a JSON error body

      try {
        const errorData = await response.json();
        // Try to parse the JSON error body returned by FastAPI's HTTPException

        if (errorData.detail) {
          errorText = errorData.detail;
          // Use the server-provided error message if available
        } else if (errorData.message) {
          errorText = errorData.message;
          // Fallback to the AuditError.message field if detail is absent
        }
      } catch {
        // If the response body is not valid JSON, use the default message
        // (no action needed — errorText is already set above)
      }

      showError(errorText);
      // Display the error card with the message
      return; // Stop here — nothing to render
    }

    // --- Parse the successful response ------------------------------------

    const data = await response.json();
    // Parse the JSON body of the 202 Accepted response
    // This should match the AuditResult model in src/api/models.py

    currentAuditId = data.audit_id;
    // Store the audit ID so downloadPdf() can construct the PDF endpoint URL

    renderReport(data);
    // Render the Markdown report and show the report section

  } catch (networkError) {
    // Catches errors like: no internet, server unreachable, CORS issues

    showError(
      "Could not reach the audit server. Please check your internet connection and try again. " +
      "If the problem continues, ensure the server is running on http://127.0.0.1:8000."
    );
    // Plain-English message that helps the user diagnose common local-dev issues

  } finally {
    // finally always runs, whether the try succeeded or the catch handled an error

    disableAuditButton(false);
    // Re-enable the Audit button so the user can submit another URL
  }
}


// ---------------------------------------------------------------------------
// renderReport(data)
// ---------------------------------------------------------------------------
// Takes the AuditResult JSON returned by the API and displays it on the page.

function renderReport(data) {
  // data: the parsed AuditResult object from the API response

  const auditTime = data.created_at
    ? new Date(data.created_at).toLocaleString()
    : new Date().toLocaleString();
  // Format the audit timestamp for display; fall back to current time if missing

  reportMeta.innerHTML =
    `<strong>Audited URL:</strong> <a href="${escapeHtml(data.url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(data.url)}</a>` +
    ` &nbsp;|&nbsp; <strong>Generated:</strong> ${auditTime}`;
  // Fill the meta row with the audited URL (as a link) and the audit timestamp
  // rel="noopener noreferrer" prevents the opened tab from accessing window.opener (security)

  const markdownText = data.markdown_report || "_No report content returned._";
  // Use the Markdown string from the API; fall back to a placeholder if absent

  reportBody.innerHTML = marked.parse(markdownText);
  // marked.parse() converts Markdown text to an HTML string
  // Assigning to innerHTML renders it as actual HTML elements inside the article

  hideSection(loadingSection);
  // Hide the spinner now that the report is ready

  showSection(reportSection);
  // Reveal the report card containing the rendered Markdown

  reportSection.scrollIntoView({ behavior: "smooth", block: "start" });
  // Smoothly scroll the page so the report is visible without the user having to scroll manually
}


// ---------------------------------------------------------------------------
// downloadPdf()
// ---------------------------------------------------------------------------
// Called when the user clicks the Download PDF button.
// Opens the PDF download endpoint in a new tab, which triggers the browser's
// file download dialog.

function downloadPdf() {
  if (!currentAuditId) {
    // Guard: button should not be clickable without an audit ID, but check anyway
    showError("No audit available for download. Please run an audit first.");
    return;
  }

  const pdfUrl = `/api/v1/audits/${currentAuditId}/pdf`;
  // Construct the PDF endpoint URL using the stored audit ID
  // This maps to GET /api/v1/audits/{audit_id}/pdf in src/api/routes/audit.py

  window.open(pdfUrl, "_blank");
  // Open the PDF URL in a new browser tab
  // The FastAPI FileResponse will set Content-Disposition: attachment which triggers the download dialog
}


// ---------------------------------------------------------------------------
// UI helper functions
// ---------------------------------------------------------------------------

function showError(message) {
  // Display the error card with the given message string

  hideSection(loadingSection); // Hide spinner if it was visible
  hideSection(reportSection);  // Hide report if one was previously shown

  errorMessage.textContent = message;
  // textContent is used (not innerHTML) to prevent any injected HTML in the error message
  // from being executed — safe against XSS in error strings

  showSection(errorSection);   // Reveal the red error card
}

function showSection(element) {
  element.classList.remove("hidden");
  // Remove the CSS "hidden" class (display:none) to make the element visible
}

function hideSection(element) {
  element.classList.add("hidden");
  // Add the CSS "hidden" class to set display:none and hide the element
}

function resetSections() {
  // Hide all dynamic sections and clear their content before starting a new audit

  hideSection(loadingSection); // Hide spinner
  hideSection(errorSection);   // Hide error card
  hideSection(reportSection);  // Hide report card

  errorMessage.textContent = "";   // Clear previous error text
  reportMeta.innerHTML = "";       // Clear previous audit URL / timestamp
  reportBody.innerHTML = "";       // Clear previous report HTML
  currentAuditId = null;           // Reset the stored audit ID
}

function resetUI() {
  // Called by the "Try again" button to return the UI to its initial state

  resetSections();    // Clear all dynamic sections
  urlInput.focus();   // Move keyboard focus back to the URL input for convenience
}

function disableAuditButton(disabled) {
  auditBtn.disabled = disabled;
  // disabled=true: prevents clicks and dims the button while the audit runs
  // disabled=false: re-enables the button when the audit is complete

  auditBtn.textContent = disabled ? "Auditing…" : "Audit";
  // Change button text to "Auditing…" while working so the user knows something is happening
}

function escapeHtml(text) {
  // Escape special HTML characters in strings before inserting them into innerHTML
  // Prevents any URL or text returned by the API from being interpreted as HTML (XSS prevention)

  const map = {
    "&": "&amp;",   // Ampersand must be escaped first
    "<": "&lt;",    // Less-than becomes HTML entity
    ">": "&gt;",    // Greater-than becomes HTML entity
    '"': "&quot;",  // Double-quote becomes HTML entity
    "'": "&#039;",  // Single-quote becomes HTML entity
  };

  return text.replace(/[&<>"']/g, (char) => map[char]);
  // Replace every special character with its safe HTML entity equivalent
}


// ---------------------------------------------------------------------------
// Keyboard shortcut: Enter key submits the audit
// ---------------------------------------------------------------------------

urlInput.addEventListener("keydown", (event) => {
  // Listen for keydown events on the URL input field

  if (event.key === "Enter") {
    // Check if the pressed key is Enter

    event.preventDefault();
    // Prevent any default Enter behaviour (such as submitting a form)

    handleAudit();
    // Trigger the audit as if the user clicked the button
  }
});
