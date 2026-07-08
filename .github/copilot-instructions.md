# GitHub Copilot Instructions

## Project Identity

Project Name: AI SEO Agent

This repository contains the implementation of an AI-powered SEO auditing platform.

The project is currently in the Minimum Viable Product (MVP) phase.

The MVP focuses on delivering one high-quality SEO audit using a single AI agent.

The objective is to build a production-ready platform rather than a proof of concept.

---

# Product Objective

The primary objective is to automate professional SEO audits while maintaining high accuracy, consistency, and business value.

The system should:

- Audit websites
- Detect SEO issues
- Calculate an SEO score
- Prioritize findings
- Generate AI recommendations
- Produce a professional PDF report
- Answer follow-up questions based on the generated report

Always optimize for business value rather than feature count.

---

# Product Philosophy

The platform should behave as an experienced SEO consultant rather than simply an SEO scanner.

Every recommendation should explain:

- What is wrong
- Why it matters
- Business impact
- Recommended solution
- Expected benefit
- Estimated implementation effort

The AI should educate users rather than simply list issues.

---

# Current Project Phase

Current Phase:

Minimum Viable Product (MVP)

During the MVP phase:

- Prefer simplicity.
- Avoid unnecessary complexity.
- Deliver working software quickly.
- Build only core functionality.
- Avoid premature optimization.

Do not introduce enterprise features unless explicitly requested.

---

# Architecture

The MVP uses a Single AI Agent architecture.

Do NOT introduce:

- Multi-agent orchestration
- Planner agents
- Task delegation
- Distributed execution

Future versions may evolve toward a hybrid architecture.

---

# Technology Stack

Primary language:

Python

Framework:

FastAPI

Website crawling:

Playwright

AI:

LLM

Report generation:

PDF

Development environment:

Visual Studio Code

Version control:

Git

Operating System:

Windows

Always generate solutions compatible with this stack unless instructed otherwise.

---

# Coding Principles

Always generate production-quality code.

Code should be:

- Modular
- Readable
- Maintainable
- Well documented
- Reusable
- Testable

Follow:

- SOLID Principles
- Separation of Concerns
- DRY
- KISS

Avoid unnecessary abstractions.

---

# Python Standards

Always:

- Use type hints.
- Use dataclasses or Pydantic models where appropriate.
- Use meaningful variable names.
- Write docstrings for public functions.
- Add comments only where they improve understanding.
- Keep functions focused on one responsibility.

Prefer readability over clever implementations.

---

# Error Handling

Always:

- Validate inputs.
- Handle exceptions gracefully.
- Return structured error messages.
- Log useful debugging information.
- Avoid exposing internal exceptions to users.

Never silently ignore errors.

---

# Logging

Use structured logging.

Every major operation should log:

- Start
- Success
- Failure
- Execution time

Avoid excessive logging.

Never log secrets.

---

# Security

Never:

- Hardcode API keys.
- Hardcode passwords.
- Commit secrets.
- Disable SSL validation without explicit justification.

Always validate user input.

Use environment variables for configuration.

---

# SEO Philosophy

Follow the rules defined in:

docs/SEO_RULES.md

Do not invent SEO rules.

Recommendations must be based on measurable evidence.

If sufficient evidence is unavailable, clearly state the limitation instead of guessing.

---

# AI Behaviour

Follow:

docs/AI_REPORT_GUIDELINES.md

The AI should behave as:

Senior Technical SEO Consultant

The AI should never hallucinate technical findings.

Always explain recommendations in plain business language.

---

# Report Generation

Follow:

docs/REPORT_SPECIFICATION.md

Generate reports that are:

- Professional
- Consistent
- Actionable
- Client-ready

Do not change report structure without explicit approval.

---

# Scoring

Follow:

docs/SCORING_ENGINE.md

Never invent scoring logic.

Do not change scoring weights without updating the scoring specification.

---

# Documentation

Before implementing major features:

Review:

- docs/PRODUCT.md
- docs/ARCHITECTURE.md
- docs/SEO_RULES.md

Keep documentation synchronized with implementation.

---

# Development Workflow

When implementing new functionality:

1. Understand the requirement.
2. Review the relevant documentation.
3. Propose the simplest implementation.
4. Explain architectural trade-offs.
5. Implement production-quality code.
6. Suggest improvements separately.
7. Never mix MVP features with future roadmap items.

---

# Code Generation Rules

Prefer modifying existing files over creating unnecessary new files.

Keep folder structure organized.

Avoid duplicate logic.

Extract reusable functionality.

Use configuration instead of hardcoded values.

---

# Testing

Generate:

- Unit tests
- Integration tests

Tests should cover:

- Success cases
- Failure cases
- Edge cases

Avoid untested code.

---

# Performance

Prefer:

- Efficient algorithms
- Lazy loading when appropriate
- Minimal API calls
- Minimal LLM token usage

Avoid unnecessary processing.

---

# AI Cost Optimization

Use deterministic logic whenever possible.

Use the LLM only for:

- Reasoning
- Explanations
- Summaries
- Recommendations

Do not use the LLM for calculations that can be implemented deterministically.

---

# Communication Style

When answering development questions:

- Explain the reasoning.
- Explain trade-offs.
- Recommend the simplest production-ready solution.
- Distinguish MVP from future improvements.

Avoid unnecessary complexity.

---

# Repository Context

This repository follows a documentation-first approach.

The following documents define project requirements:

- docs/PRODUCT.md
- docs/ARCHITECTURE.md
- docs/SEO_RULES.md
- docs/REPORT_SPECIFICATION.md
- docs/SCORING_ENGINE.md
- docs/AI_REPORT_GUIDELINES.md

Follow all applicable instruction files under:

.github/instructions/

including:

- python.instructions.md
- playwright.instructions.md
- fastapi.instructions.md
- seo.instructions.md
- git.instructions.md

Repository-wide instructions and applicable path-specific instructions must both be followed.

Treat these documents as the source of truth.

If implementation conflicts with documentation, ask for clarification before changing behavior.

---

# Final Principle

When multiple valid solutions exist:

Choose the simplest solution that:

- Solves the business problem
- Is production ready
- Is maintainable
- Is scalable
- Keeps implementation understandable

Always prioritize correctness, maintainability, and long-term quality over short-term convenience.