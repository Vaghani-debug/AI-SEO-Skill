# AGENTS.md

> AI SEO Agent Repository Guide
>
> This document provides operational guidance for AI coding agents working in this repository.
> It complements `.github/copilot-instructions.md` and the project documentation.
> Do not duplicate instructions. Use this document to understand how to work within the repository.

---

# Project Overview

Project Name

AI SEO Agent

Purpose

Build an AI-powered SEO auditing platform that automatically analyzes websites, detects SEO issues, generates professional audit reports, calculates SEO scores, and provides AI-powered recommendations.

Current Phase

Minimum Viable Product (MVP)

The MVP focuses on delivering one high-quality SEO audit using a single AI agent.

---

# Project Goals

The primary objectives are:

- Crawl websites reliably.
- Extract structured SEO information.
- Detect technical SEO issues.
- Calculate deterministic SEO scores.
- Generate professional PDF reports.
- Provide AI-generated explanations and recommendations.
- Support interactive report-based conversations.

Always prioritize correctness, reliability, maintainability, and business value.

---

# Source of Truth

Before implementing any feature, review the following documents.

docs/PRODUCT.md

Business requirements.

docs/ARCHITECTURE.md

System architecture.

docs/SEO_RULES.md

SEO methodology.

docs/SCORING_ENGINE.md

SEO scoring logic.

docs/REPORT_SPECIFICATION.md

Report structure.

docs/AI_REPORT_GUIDELINES.md

AI writing standards.

If implementation conflicts with documentation, stop and request clarification.

Never invent project requirements.

---

# Architecture

Current architecture:

Single AI Agent

Workflow

User

↓

Website URL

↓

Playwright Crawler

↓

Extraction

↓

SEO Rule Engine

↓

Scoring Engine

↓

LLM Analysis

↓

Report Generator

↓

PDF Report

↓

AI Chat

Do not introduce:

- Multi-agent orchestration
- Planner agents
- Distributed workflows
- Autonomous task delegation

These belong to future releases.

---

# Technology Stack

Language

Python 3.12+

Framework

FastAPI

Crawler

Playwright

AI

LLM

Development

Visual Studio Code

Operating System

Windows

Version Control

Git

Always generate solutions compatible with this stack.

---

# Working Principles

When implementing a feature:

1. Understand the requirement.
2. Read the relevant documentation.
3. Design the simplest solution.
4. Explain architectural trade-offs if necessary.
5. Implement production-quality code.
6. Add or update tests.
7. Update documentation if behavior changes.

Do not skip planning.

Do not guess requirements.

---

# Responsibilities

Playwright

Responsible only for website crawling and structured data extraction.

Never calculate SEO scores.

Never generate recommendations.

SEO Rule Engine

Responsible for deterministic SEO checks.

LLM

Responsible only for:

- Explanations
- Summaries
- Prioritization
- Recommendations
- Report writing

Do not use the LLM for deterministic calculations.

Scoring Engine

Responsible for deterministic SEO scoring.

Report Generator

Responsible for producing JSON and PDF reports.

FastAPI

Responsible only for API communication.

Business logic must not be implemented inside API routes.

---

# Development Rules

Prefer:

- Small commits
- Small pull requests
- Small functions
- Small classes

One responsibility per module.

Avoid duplicate logic.

Prefer composition over inheritance.

Keep modules independent.

---

# Coding Standards

Follow:

- SOLID
- DRY
- KISS
- Separation of Concerns

Always:

- Use type hints.
- Write docstrings.
- Validate inputs.
- Handle exceptions.
- Log important operations.

Never:

- Hardcode secrets.
- Ignore exceptions.
- Duplicate code.
- Introduce unnecessary abstractions.

---

# Testing

Every code change should include appropriate tests.

Minimum testing includes:

- Success path
- Failure path
- Validation
- Edge cases

Before completing work:

- Ensure tests pass.
- Avoid reducing test coverage.
- Preserve existing functionality.

---

# Logging

Log:

- Audit start
- Audit completion
- Errors
- Warnings
- Execution time

Never log:

- Secrets
- API keys
- Passwords
- Tokens

---

# Security

Validate all external input.

Never trust website data.

Use environment variables for secrets.

Do not expose internal exceptions to users.

Do not disable SSL verification unless explicitly required for development.

---

# Performance

Prefer:

- Deterministic algorithms
- Minimal LLM usage
- Efficient DOM queries
- Modular processing

Avoid:

- Duplicate crawling
- Duplicate extraction
- Repeated LLM calls
- Unnecessary browser instances

---

# AI Behaviour

The AI should behave as:

Senior Technical SEO Consultant

The AI must:

- Explain findings.
- Prioritize recommendations.
- Educate users.
- Use plain business language.

The AI must never:

- Invent technical findings.
- Guess website information.
- Change scoring logic.
- Contradict documented SEO rules.

---

# Repository Workflow

Before writing code:

Read documentation.

↓

Understand requirement.

↓

Plan implementation.

↓

Implement.

↓

Run tests.

↓

Review code.

↓

Update documentation if necessary.

↓

Complete task.

---

# Definition of Done

A task is complete only if:

✓ Business requirement implemented.

✓ Code follows project architecture.

✓ No unnecessary complexity introduced.

✓ Tests added or updated.

✓ Documentation updated if required.

✓ No hardcoded configuration.

✓ Code is production-ready.

---

# Decision Making

When multiple valid implementations exist:

Choose the solution that is:

- Simplest
- Deterministic
- Maintainable
- Testable
- Production-ready

Avoid premature optimization.

Always optimize for long-term maintainability.

---

# Repository Context

This repository follows a documentation-first approach.

Documentation defines the product.

Implementation follows the documentation.

Documentation should never be ignored.

---

# Future Evolution

After MVP validation, future versions may introduce:

- Hybrid architecture
- Multiple AI agents
- Background task processing
- Historical audit comparisons
- Competitor analysis
- Team collaboration
- User authentication
- Billing
- SaaS deployment

These features are intentionally out of scope for the MVP.

---

# Final Principle

This repository is intended to become a commercial-quality AI SEO platform.

Every implementation decision should improve:

- Reliability
- Maintainability
- Accuracy
- Consistency
- User value

When in doubt, choose the simpler solution that best satisfies the documented business requirements.