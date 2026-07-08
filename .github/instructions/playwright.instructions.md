---
applyTo: "**/*playwright*.py,**/*crawl*.py,**/*crawler*.py,**/*extract*.py,**/*browser*.py"
---

# Playwright Development Instructions

## Purpose

These instructions define how Playwright must be used throughout this project.

This project uses Playwright for **SEO website crawling and content extraction**, not browser automation testing.

The objective is to collect deterministic website data that serves as evidence for SEO analysis.

Always follow the repository-wide instructions in:

.github/copilot-instructions.md

These instructions extend those repository rules.

---

# Primary Goal

Playwright is responsible for collecting website data.

Playwright is NOT responsible for:

- SEO scoring
- AI reasoning
- Business recommendations
- PDF generation
- Report writing

Its responsibility ends after accurate website data has been collected.

---

# General Principles

Always prefer:

- Accuracy
- Stability
- Repeatability
- Simplicity
- Maintainability

Never optimise for shortest code.

Optimise for reliable website analysis.

---

# Browser Configuration

Prefer Chromium.

Launch browser in headless mode by default.

Only use headed mode during debugging.

Always configure:

- reasonable timeout
- viewport
- user agent
- HTTPS error handling

Use project configuration instead of hardcoded values.

---

# Context Configuration

Create one BrowserContext per audit.

Avoid sharing BrowserContext objects between audits.

Each audit should be isolated.

---

# Page Navigation

Always:

Validate the URL before navigation.

Normalize URLs.

Support:

https://

http://

www

Redirects

Use page.goto()

Prefer:

wait_until="domcontentloaded"

Avoid:

networkidle

unless explicitly required.

Navigation should fail gracefully.

Never crash the application because one page cannot be loaded.

---

# Timeouts

Always use explicit timeouts.

Do not rely on Playwright defaults.

Example:

Navigation timeout

30 seconds

Selector timeout

10 seconds

Extraction timeout

5 seconds

Store timeout values in configuration.

---

# Error Handling

Always handle:

Navigation timeout

DNS failure

SSL failure

Connection refused

Certificate errors

Redirect loops

JavaScript errors

Unexpected browser closure

Return structured error objects.

Never return None.

Never silently ignore failures.

---

# Crawling Philosophy

The crawler should collect facts.

Never interpret data.

Never calculate SEO scores.

Never generate recommendations.

Never use the LLM.

Only collect evidence.

---

# Extraction Order

Extract information in this order:

1. Final URL

2. HTTP Status

3. Page Title

4. Meta Description

5. Canonical

6. Meta Robots

7. Language

8. Headings

9. Images

10. Links

11. Structured Data

12. Open Graph

13. Twitter Cards

14. Performance Metrics

15. Accessibility Information

This order should remain consistent.

---

# HTML Access

Always extract:

Rendered DOM

Do not analyse raw HTML when rendered HTML is required.

Allow JavaScript execution.

Wait until DOM is available before extraction.

---

# Metadata

Extract:

Title

Description

Canonical

Robots

Charset

Viewport

Language

Theme Color

Author

Generator

Do not guess missing metadata.

Missing values should be represented explicitly.

---

# Heading Extraction

Extract:

H1

H2

H3

H4

H5

H6

Preserve:

Text

Order

Count

Hierarchy

Never modify heading text.

---

# Image Extraction

Collect:

Image URL

ALT text

Width

Height

Loading attribute

File type

Lazy loading

Broken images

Never download images unless required.

---

# Link Extraction

Collect separately:

Internal links

External links

Anchor text

Target attribute

Nofollow

Sponsored

UGC

Broken links

Remove duplicate links.

Preserve original URLs.

---

# Structured Data

Extract:

JSON-LD

Microdata

RDFa

Schema.org types

Do not validate schema here.

Validation belongs to SEO analysis.

---

# Performance Collection

Collect only measurable metrics.

Examples:

DOM Load Time

Load Event

Resource Count

Largest Contentful Paint (if available)

Cumulative Layout Shift (if available)

Interaction to Next Paint (if available)

Do not estimate metrics.

---

# Accessibility

Collect:

Missing ALT

ARIA labels

Heading order

Viewport

Language

Collect facts only.

Do not evaluate accessibility score.

---

# Screenshots

Capture one full-page screenshot.

Store screenshots in a predictable location.

Use PNG.

Only capture additional screenshots during debugging.

---

# Logging

Log:

Audit started

Browser launched

Navigation started

Navigation completed

Extraction completed

Browser closed

Execution time

Do not log HTML content.

Do not log sensitive data.

---

# Resource Management

Always:

Close pages.

Close contexts.

Close browsers.

Release resources.

Never leave browser processes running.

---

# Async Programming

Use async Playwright APIs.

Avoid blocking operations.

Await all browser operations.

Never mix synchronous Playwright with asynchronous implementation.

---

# Retries

Retry only transient failures.

Maximum retries:

2

Never retry:

404

403

Invalid URL

Permanent SSL failures

Retry logic should be configurable.

---

# Deterministic Behaviour

Running the crawler twice against an unchanged website should produce identical extracted data whenever possible.

The crawler must never introduce randomness.

---

# Code Organization

Separate responsibilities into modules.

Suggested structure:

crawler.py

Browser lifecycle

navigator.py

Navigation

extractor.py

Metadata extraction

links.py

Link extraction

images.py

Image extraction

headings.py

Heading extraction

performance.py

Performance metrics

utils.py

Shared utilities

Do not place all logic into one file.

---

# Data Models

Return structured models.

Prefer:

Pydantic models

or

dataclasses

Avoid raw dictionaries where possible.

Define explicit schemas for crawl results.

---

# Security

Never execute arbitrary JavaScript.

Never submit forms.

Never log into websites.

Never bypass authentication.

Never modify website content.

The crawler is read-only.

---

# Testing

Every crawler feature should include tests.

Test:

Valid website

Invalid URL

Timeout

Redirect

SSL error

404 page

JavaScript-heavy website

Empty page

Broken HTML

Large page

---

# Performance Optimisation

Reuse browser binaries.

Avoid unnecessary page reloads.

Avoid duplicate DOM queries.

Batch element extraction when possible.

Prefer locator APIs over repeated selector lookups.

Optimise for deterministic execution, not aggressive speed.

---

# Final Rule

Playwright is the evidence collection layer of the AI SEO Agent.

It must always return accurate, structured, repeatable website data.

It must never perform SEO reasoning, calculate scores, or generate recommendations.

Every downstream component—including the scoring engine and AI assistant—depends on the quality of the data produced here.

Accuracy is always more important than speed.