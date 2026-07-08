---
description: "Use when adding or changing tests, fixtures, assertions, or validation scripts."
applyTo: "test/**/*.py"
---

# Testing Instructions

- Favor focused tests that exercise one behavior or failure mode at a time.
- Use captured HTML and JSON fixtures for SEO analysis tests.
- Assert on structured outputs before prose text where possible.
- Keep external service calls optional, skipped, or mocked.