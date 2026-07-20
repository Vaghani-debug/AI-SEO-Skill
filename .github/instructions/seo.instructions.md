---
applyTo: "**/*seo*.py,**/*audit*.py,**/*score*.py,**/*report*.py,**/*analysis*.py,**/*analyzer*.py"
---

# SEO Development Instructions

## Purpose

These instructions define **developer and Copilot coding behavior** for SEO-related modules in this repository.

This file covers implementation rules only.
It does not define report content, tone, recommendation format, severity language, or SEO check methodology.

---

## Source of Truth

Before implementing any SEO feature, read the relevant source document.

| What you need | Where it lives |
|---|---|
| SEO checks, audit categories, methodology | `docs/SEO_RULES.md` |
| Report sections and structure | `docs/REPORT_SPECIFICATION.md` |
| AI tone, severity language, recommendation format | `docs/AI_REPORT_GUIDELINES.md` |
| Scoring weights and logic | `docs/SCORING_ENGINE.md` |
| Architecture and module responsibilities | `docs/ARCHITECTURE.md` |
| Product requirements | `docs/PRODUCT.md` |

If instructions conflict, ask for clarification rather than making assumptions.

---

## This File Is Not Runtime Context

Do **not** load this file inside the running FastAPI application.

The runtime LLM context is defined in `MVP_PLAN.md` and consists only of:

- `.github/prompts/seo_audit.prompt.md`
- `.agents/skills/seo-audit-skill/SKILL.md`
- `docs/REPORT_SPECIFICATION.md`
- `docs/AI_REPORT_GUIDELINES.md`

---

## Coding Rules

### Use Deterministic Code for All Findings

Every SEO issue must be computed from measurable, verifiable evidence.

The same website with no changes must produce identical findings on every run.

Never introduce randomness into issue detection.

### Use the LLM Only for Reasoning

The LLM is responsible for:

- Summaries
- Explanations
- Prioritization
- Natural language generation

The LLM must never determine measurable SEO facts such as HTTP status, title length, heading count, or link count.
These must be computed by Python code.

### Never Invent Rules

Do not add SEO categories, severity levels, scoring weights, or recommendation fields that are not defined in the source documents above.

To change any of these, update the relevant source document first, then implement.

### Mark Unverifiable Fields Explicitly

When a field cannot be determined from static content, use exactly this phrase in the evidence data:

```
Could not be verified in this audit.
```

This phrase is defined in `AI_REPORT_GUIDELINES.md` and must not be changed.

### SEO Checks Must Not Cross Module Boundaries

Extraction belongs in `extractor_service.py`.

Scoring belongs in the scoring module.

Report writing belongs in `report_service.py`.

Do not calculate scores inside extractors, or extract data inside report generators.

---

## Module Design

Design SEO modules to be:

- **Modular** — each module has one responsibility
- **Extensible** — new checks are added without modifying existing analyzers
- **Independent** — modules do not depend on each other's internal state
- **Testable** — every check can be tested in isolation with synthetic inputs

---

## SEO Philosophy

The platform helps website owners improve quality.
It does not manipulate rankings.

Never implement or recommend:

- Keyword stuffing
- Hidden text or cloaking
- Link schemes or automated backlink generation
- Doorway pages
- Any technique that violates published search engine guidelines

Always aim to educate the user while improving the quality of their website.