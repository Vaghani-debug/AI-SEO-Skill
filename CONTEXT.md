# Domain Glossary

This file defines the vocabulary used across code, docs, and agent instructions.
Add terms here as they become stable — fuzzy terms can be noted as `(provisional)`.

## Core Concepts

| Term | Meaning in this project |
| ---- | ---------------------- |
| **Audit** | A full evaluation of a single web page: fetch → parse → analyse → report. |
| **Audit run** | One complete execution of an audit for a given target URL. |
| **Target** | The URL (or local HTML fixture) being audited. |
| **Finding** | A single observed issue or signal from an audit run, with a severity level. |
| **Recommendation** | An actionable fix derived from one or more findings. |
| **Severity** | Priority classification for a finding: `Critical`, `High`, `Medium`, `Low`. |
| **Skill** | A bundled SKILL.md prompt that instructs the AI model on how to analyse a page. |
| **Provider** | The AI model backend used for analysis (currently Gemini or OpenAI). |
| **Fixture** | Captured HTML/JSON from a prior fetch, used as deterministic test input. |
| **Fetch** | The step that retrieves raw HTML and metadata for a target URL. |
| **Metadata** | Structured signal extracted from a page during fetch: title, description, canonical, OG tags, robots directives. |
| **Report** | The Markdown output produced by an audit run, saved as `last_result_<skill>.md`. |

## Module Vocabulary (architecture)

| Term | Meaning |
| ---- | ------- |
| **module** | A file or package with a clear responsibility and a narrow interface. |
| **interface** | The public surface of a module — what callers depend on. |
| **depth** | A deep module hides a lot behind a small interface; a shallow one does not. |
| **seam** | A point where one module can be substituted for another (e.g. for testing). |
| **adapter** | A module that translates between a seam and a concrete external dependency. |
| **locality** | The degree to which a concept is understandable without bouncing between files. |
| **leverage** | A small interface change that simplifies a wide range of callers. |
