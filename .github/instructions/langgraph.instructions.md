---
description: "Use when building LangGraph workflows, graph state, nodes, edges, or agent orchestration."
applyTo: "**/*graph*.py"
---

# LangGraph Instructions

- Keep graph state schemas explicit and stable.
- Make each node responsible for one clear transformation or decision.
- Prefer deterministic tool boundaries and record enough state to resume or debug runs.
- Avoid hiding business rules inside prompt text when they can be represented as code or data.