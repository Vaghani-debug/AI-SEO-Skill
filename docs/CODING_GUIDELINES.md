# Coding Guidelines

## Python

- Prefer readable functions with explicit inputs and outputs.
- Keep network access isolated so tests can use fixtures.
- Use structured data models where report shape matters.
- Handle malformed HTML and missing metadata gracefully.

## Repository Hygiene

- Keep generated artifacts out of source control unless they are intentional fixtures.
- Store repeatable examples under `test/` or a future `fixtures/` directory.
- Update documentation when workflows or report formats change.
