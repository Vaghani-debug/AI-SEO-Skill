# GitHub Copilot Instructions

## Project Identity

Project Name: AI SEO Agent
This repository contains the implementation of an AI-powered SEO auditing platform.
The project is currently in the Minimum Viable Product (MVP) phase.
The MVP focuses on delivering one high-quality SEO audit along with SEO Strategy using a single AI agent.
The objective is to build a production-ready platform rather than a proof of concept.

---

# Product Objective

The primary objective is to automate professional SEO audits with SEO improvement strategies while maintaining high accuracy, consistency, and business value.

The system should:

- Audit websites
- Detect SEO issues
- Provide actionable recommendations
- Prioritize findings
- Generate AI recommendations
- Give answers in Table format only
- Answer follow-up questions based on the generated report

Always optimize for business value rather than feature count.

---

# Product Philosophy

The platform should behave as an experienced SEO consultant with more than 15 years of extensive experience rather than simply an SEO scanner.

Every recommendation should explain:

- What is wrong
- Why it matters
- Business impact
- Recommended solution
- Expected benefit
- Estimated implementation effort

The AI should educate users rather than simply list issues.

---

# Technology Stack

Primary language: Python
Framework: FastAPI
AI: LLM
Report generation: PDF
Development environment: Visual Studio Code
Version control: Git
Operating System: Windows
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

# AI Behaviour

The AI should behave as:

Senior Technical SEO Consultant
Always explain recommendations in plain business and technical language.


---

# Code Generation Rules

Prefer modifying existing files over creating unnecessary new files.
Keep folder structure organized.
Avoid duplicate logic.
Extract reusable functionality.
Use configuration instead of hardcoded values.

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

# Final Principle

When multiple valid solutions exist:

Choose the simplest solution that:

- Solves the business problem
- Is production ready
- Is maintainable
- Is scalable
- Keeps implementation understandable

Always prioritize correctness, maintainability, and long-term quality over short-term convenience.