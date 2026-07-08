---
applyTo: "**/*.py"
---

# Python Development Instructions

## Purpose

These instructions apply to all Python source files in this repository.

They extend the repository-wide instructions defined in:

.github/copilot-instructions.md

Always follow both files.

If a conflict exists, ask for clarification instead of making assumptions.

---

# Python Version

Target Python 3.12 or newer.

Always generate code compatible with Python 3.12 unless explicitly instructed otherwise.

---

# General Philosophy

Write production-quality Python.

Code should be:

- Simple
- Readable
- Modular
- Testable
- Maintainable

Prefer clarity over cleverness.

Follow the Zen of Python.

---

# Coding Style

Follow PEP 8.

Follow PEP 257 for docstrings.

Follow Python typing best practices.

Use four spaces.

Never use tabs.

Keep line length reasonable.

Avoid unnecessary comments.

Code should explain itself through meaningful names.

---

# Type Hints

Always use type hints.

All public functions must include:

- parameter types
- return type

Example

```python
def calculate_score(report: AuditReport) -> ScoreResult:
    ...
```

Avoid Any unless absolutely necessary.

Prefer:

- str
- int
- float
- bool
- Path
- list[str]
- dict[str, str]
- tuple
- Literal
- Enum

---

# Function Design

Functions should have one responsibility.

Prefer functions under 40 lines.

Split large functions.

Avoid deeply nested logic.

Maximum nesting:

3 levels.

Prefer early returns.

Example

Bad

```python
if valid:
    if page:
        if title:
            ...
```

Good

```python
if not valid:
    return

if page is None:
    return

if not title:
    return
```

---

# Classes

Classes should model real concepts.

Avoid "God Objects".

Prefer composition over inheritance.

Each class should have one responsibility.

Use dataclasses for immutable data models where appropriate.

Use Pydantic models for API request and response validation.

---

# File Organization

One module should solve one problem.

Example

crawler.py

Responsible only for crawling.

Do not calculate SEO score.

Do not generate PDF.

Do not call LLM.

---

extractor.py

Responsible only for extraction.

---

score_engine.py

Responsible only for scoring.

---

report_generator.py

Responsible only for reports.

---

# Imports

Use absolute imports.

Group imports in this order.

Standard Library

Third-party Libraries

Local Project Imports

Example

```python
from pathlib import Path

from fastapi import APIRouter

from app.models.audit import AuditReport
```

Avoid wildcard imports.

Never use:

```python
from module import *
```

---

# Constants

Never hardcode magic values.

Create named constants.

Example

```python
DEFAULT_TIMEOUT = 30
MAX_TITLE_LENGTH = 60
```

---

# Configuration

Configuration belongs in configuration files.

Never hardcode:

- URLs
- API Keys
- Tokens
- Secrets
- Passwords

Always use environment variables.

---

# Error Handling

Always handle expected exceptions.

Raise meaningful exceptions.

Never ignore errors.

Bad

```python
except:
    pass
```

Good

```python
except PlaywrightError as exc:
    logger.exception(exc)
    raise CrawlError(str(exc))
```

---

# Logging

Use Python logging.

Log:

- start
- finish
- execution time
- warnings
- failures

Do not log:

- API keys
- passwords
- tokens
- sensitive information

---

# Async Programming

Use async only when it provides real benefit.

Examples

- Playwright
- FastAPI endpoints
- API calls

Avoid unnecessary async functions.

---

# Validation

Validate all external input.

Validate:

URLs

Files

User Input

Configuration

API Responses

Never assume external data is correct.

---

# API Design

Functions should return structured objects.

Avoid returning mixed types.

Bad

```python
return True
```

Good

```python
return AuditResult(...)
```

---

# Models

Prefer structured models.

Use:

Pydantic

or

dataclasses

Avoid returning raw dictionaries unless required.

---

# Naming

Variables

snake_case

Functions

snake_case

Files

snake_case

Constants

UPPER_CASE

Classes

PascalCase

Private methods

_leading_underscore

---

# Comments

Do not explain obvious code.

Write comments only when they explain:

Business rules

SEO logic

Architectural decisions

Trade-offs

---

# Docstrings

Public modules

Public classes

Public functions

must contain docstrings.

Use Google-style docstrings.

Example

```python
def crawl(url: str) -> CrawlResult:
    """
    Crawl a website.

    Args:
        url:
            Website URL.

    Returns:
        Crawl result.
    """
```

---

# Testing

Every new feature should include tests.

Test:

Success

Failure

Edge Cases

Avoid writing code that cannot be tested.

---

# Performance

Prefer efficient algorithms.

Avoid duplicate processing.

Cache reusable values where appropriate.

Avoid repeated DOM queries.

Avoid repeated LLM calls.

---

# Security

Never trust external input.

Validate everything.

Escape user-generated content.

Never expose stack traces.

Never log secrets.

---

# AI Usage

The LLM should never perform deterministic calculations.

Python should handle:

Scoring

Counting

Validation

Extraction

Normalization

Use the LLM only for:

Reasoning

Summaries

Recommendations

Natural language generation

---

# Code Quality

Always prefer:

Readable code

Small functions

Reusable modules

Dependency injection where appropriate

Clear interfaces

Avoid:

Long functions

Duplicate logic

Circular imports

Hidden side effects

---

# Refactoring

When modifying existing code:

Improve readability.

Reduce duplication.

Preserve existing behaviour.

Avoid unnecessary rewrites.

---

# Pull Requests

When generating code:

Keep changes focused.

Do not modify unrelated files.

Do not introduce breaking changes without explanation.

---

# Final Rule

When multiple valid Python solutions exist:

Choose the solution that is:

- Simplest
- Most readable
- Production-ready
- Easy to maintain
- Easy to test
- Consistent with the existing architecture

Prefer long-term maintainability over short-term convenience.