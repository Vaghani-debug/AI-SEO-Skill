---
applyTo: "**/*"
---

# Git Workflow Instructions

## Purpose

These instructions define the Git workflow for this repository.

The objective is to maintain a clean, traceable, and production-ready Git history.

Always follow these instructions together with:

- .github/copilot-instructions.md
- AGENTS.md

---

# Git Philosophy

Every meaningful change should be version controlled.

Changes should be:

- Small
- Focused
- Easy to review
- Easy to revert

Avoid large commits containing unrelated changes.

---

# Before Modifying Code

Before making changes:

1. Understand the requested task.
2. Identify the affected files.
3. Minimize the scope of changes.
4. Preserve existing functionality.

---

# After Completing a Task

After implementation:

1. Review modified files.
2. Check for obvious issues.
3. Ensure documentation is updated if needed.
4. Verify tests (when available).
5. Summarize the changes.

---

# Commit Strategy

Create commits only after a logical unit of work is complete.

Do not create partial or incomplete commits.

One feature or one bug fix should normally equal one commit.

---

# Commit Message Format

Use Conventional Commits.

Examples:

feat: add SEO audit report generation

fix: resolve metadata extraction bug

refactor: simplify audit service

docs: update SEO rules

test: add crawler integration tests

chore: update dependencies

---

# Commit Quality

Every commit should:

- Build successfully
- Keep the project in a working state
- Be understandable without additional explanation

---

# Push Strategy

Never push automatically.

Always wait for user confirmation before pushing.

After completing a task, suggest the following workflow:

1. Review changes
2. Commit
3. Push

---

# Pull Requests

When preparing a pull request:

Provide:

- Summary
- Files changed
- Reason for the change
- Testing performed
- Potential risks

---

# Branching

For MVP development, use:

main

or

develop

Feature branches may be introduced later.

Do not create unnecessary branches.

---

# Documentation

If project behavior changes:

Update the appropriate documentation before committing.

Examples:

docs/PRODUCT.md

docs/ARCHITECTURE.md

docs/SEO_RULES.md

---

# Security

Never commit:

- API keys
- Passwords
- Tokens
- Secrets
- Environment files

If secrets are detected, stop and notify the user.

---

# Final Rule

At the end of every completed development task:

Summarize:

- What changed
- Which files changed
- Suggested commit message

Then ask:

"Would you like me to prepare this for commit and push?"