---
applyTo: "**/*api*.py,**/*router*.py,**/*routes*.py,**/*endpoint*.py,**/*main.py,**/*app.py"
---

# FastAPI Development Instructions

## Purpose

These instructions apply to all FastAPI-related source files.

This project uses FastAPI as the backend API layer for the AI SEO Agent.

FastAPI is responsible for:

- Receiving requests
- Validating input
- Calling business services
- Returning structured responses
- Handling errors
- Logging requests
- Exposing REST APIs

FastAPI is NOT responsible for:

- SEO analysis
- Website crawling
- AI reasoning
- SEO scoring
- PDF generation

Those responsibilities belong to dedicated service modules.

Always follow:

- .github/copilot-instructions.md
- .github/instructions/python.instructions.md

---

# Architecture Philosophy

FastAPI should remain thin.

Business logic must never live inside route handlers.

Routes should only:

1. Validate request
2. Call service
3. Return response

---

# Project Structure

Prefer this structure:

app/

    main.py

    api/

        routes/

    services/

    models/

    schemas/

    core/

    config/

    dependencies/

    exceptions/

    middleware/

    utils/

Keep responsibilities separated.

---

# API Design

Design APIs using REST principles.

Use nouns instead of verbs.

Good

POST /audit

GET /audit/{id}

GET /health

Bad

POST /createAudit

POST /runAuditNow

---

# Versioning

All APIs should support versioning.

Example

/api/v1/

Future versions should not break existing clients.

---

# Request Validation

Always validate request bodies.

Use Pydantic models.

Never accept raw dictionaries.

Bad

def audit(data: dict):

Good

def audit(request: AuditRequest):

---

# Response Models

Always define response models.

Avoid returning arbitrary dictionaries.

Responses should be predictable.

---

# HTTP Status Codes

Use standard HTTP status codes.

200

Success

201

Created

202

Accepted

400

Bad Request

401

Unauthorized

403

Forbidden

404

Not Found

409

Conflict

422

Validation Error

429

Rate Limited

500

Internal Error

Never always return HTTP 200.

---

# Endpoint Responsibilities

Each endpoint should have one responsibility.

Example

POST /audit

Starts an SEO audit.

GET /audit/{id}

Returns an audit.

GET /health

Returns application health.

Do not combine unrelated functionality.

---

# Dependency Injection

Use FastAPI dependency injection.

Inject:

Configuration

Database

Logger

Authentication

Services

Avoid creating dependencies inside route handlers.

---

# Service Layer

Routes must call services.

Example

Route

↓

AuditService

↓

Crawler

↓

SEO Engine

↓

LLM

↓

Report Generator

Never implement business logic directly inside endpoints.

---

# Asynchronous Programming

Use async endpoints whenever appropriate.

Await:

Playwright

External APIs

Database operations

LLM requests

Avoid blocking calls inside async endpoints.

---

# Error Handling

Create custom exceptions.

Examples

AuditNotFoundError

InvalidWebsiteError

CrawlerTimeoutError

LLMServiceError

Convert exceptions into structured HTTP responses.

Never expose stack traces.

---

# Global Exception Handler

Register global exception handlers.

Return consistent error responses.

Example

{
  "success": false,
  "error": {
      "code": "INVALID_URL",
      "message": "Website URL is invalid."
  }
}

Avoid inconsistent error formats.

---

# Validation

Validate:

Website URL

Request body

Headers

Query parameters

Environment configuration

Reject invalid input early.

---

# API Documentation

Use FastAPI automatic documentation.

Ensure every endpoint contains:

Summary

Description

Tags

Request model

Response model

Status codes

Examples

Swagger documentation should remain production quality.

---

# Tags

Group endpoints logically.

Example

Audit

Health

Reports

Chat

Administration

Avoid ungrouped endpoints.

---

# Health Endpoint

Always implement:

GET /health

Response should include:

Application status

Version

Environment

Timestamp

Optional

LLM availability

Database availability

---

# Configuration

Configuration belongs in settings.

Never hardcode:

URLs

API keys

Timeouts

File paths

Model names

Read configuration from environment variables.

---

# Logging

Log:

Request received

Audit started

Audit completed

Execution time

Errors

Warnings

Never log:

Secrets

Tokens

Passwords

PII

Large HTML payloads

---

# Middleware

Use middleware only when appropriate.

Examples

Request logging

Execution timing

Correlation ID

Security headers

Compression

Avoid unnecessary middleware.

---

# Authentication

The MVP does not require authentication.

Do not introduce authentication unless explicitly requested.

Future versions may support:

JWT

OAuth

API Keys

---

# File Uploads

Validate uploaded files.

Restrict file size.

Restrict file type.

Sanitize filenames.

Never trust uploaded content.

---

# Background Tasks

Long-running operations should support background execution.

Examples

SEO audit

PDF generation

Future versions may use queues.

Keep route handlers responsive.

---

# Timeouts

Always configure explicit timeouts.

Do not rely on framework defaults.

Return meaningful timeout errors.

---

# Rate Limiting

The MVP may omit rate limiting.

Design endpoints so rate limiting can be added later without breaking APIs.

---

# Response Format

Every successful response should follow a consistent structure.

Example

{
  "success": true,
  "message": "SEO audit completed successfully.",
  "data": {}
}

Every error response should follow a consistent structure.

---

# API Stability

Do not remove existing fields.

Prefer adding optional fields.

Maintain backward compatibility.

---

# Testing

Every endpoint should have tests.

Include:

Success

Validation failure

Invalid URL

Timeout

Unexpected exception

Edge cases

---

# Performance

Avoid duplicate service calls.

Reuse dependency injection.

Avoid unnecessary serialization.

Return only required data.

Minimize memory usage.

---

# Security

Validate all external input.

Escape user input when appropriate.

Use HTTPS in production.

Protect secrets.

Never expose internal implementation details.

---

# AI Integration

The API should treat the LLM as a service.

The endpoint should not construct prompts.

Prompt engineering belongs inside dedicated AI service modules.

Keep the API layer independent of AI implementation details.

---

# Documentation

Every endpoint should include:

Purpose

Input

Output

Error responses

Usage example

Swagger documentation should be sufficient for frontend developers.

---

# Final Rule

FastAPI is the communication layer of the AI SEO Agent.

Its responsibility is to expose clean, secure, maintainable APIs.

Business logic belongs in services.

SEO logic belongs in the SEO engine.

AI logic belongs in AI services.

Routes should remain thin, predictable, and easy to maintain.