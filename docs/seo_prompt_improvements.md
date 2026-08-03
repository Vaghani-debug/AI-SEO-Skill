# SEO Report Format Prompt - Improvement Analysis & Recommendations

## Current State Analysis

The `.github/prompts/SEO_Report_Format.md` file is a comprehensive prompt for generating SEO audit reports. While thorough, it has several areas for improvement:

### Issues Identified

1. **Numbering Inconsistencies** - Lines 16-20 and 54-55 use duplicate numbering (1., 1., 1., 1.)
2. **Redundant Instructions** - Some instructions repeat across sections
3. **Single Large File** - 236 lines in one file makes maintenance difficult
4. **Missing Validation Rules** - No explicit validation for output format compliance
5. **No Token Optimization Guidance** - Could be more concise for LLM efficiency
6. **Hardcoded Values** - Table structures are rigid, not configurable
7. **Missing Error Handling Guidance** - No instruction for handling API failures
8. **No Versioning** - No way to track prompt iterations

---

## Recommended Improvements

### 1. Fix Numbering Issues

**Current (lines 16-20, 54-55):**
```markdown
1. Browse and inspect the live website before producing the report.
1. Do not rely only on the homepage.
1. Examine all publicly accessible sources...
...
1. Follow redirects and report the final destination when relevant.
1. Evaluate legal and utility pages individually...
```

**Fixed:**
```markdown
1. Browse and inspect the live website before producing the report.
2. Do not rely only on the homepage.
3. Examine all publicly accessible sources...
...
10. Follow redirects and report the final destination when relevant.
11. Evaluate legal and utility pages individually...
```

---

### 2. Modularize into Separate Files

Create a prompt directory structure:
```
.github/prompts/
├── seo_audit/
│   ├── system_prompt.md          # Role definition & expertise
│   ├── audit_method.md           # Step-by-step process
│   ├── output_format.md          # Report structure & tables
│   ├── quality_rules.md          # Final validation rules
│   └── prompt_loader.py          # Composable loader
```

**Benefits:**
- Easier to maintain and version
- Can A/B test individual sections
- Reusable across different audit types
- Better git diff visibility

---

### 3. Add Output Validation Schema

Add a JSON schema for validating the generated report structure:

```json
{
  "type": "object",
  "properties": {
    "business_name": {"type": "string"},
    "domain": {"type": "string"},
    "primary_location": {"type": "string"},
    "part_1": {
      "type": "object",
      "properties": {
        "core_pages": {
          "type": "array",
          "items": {
            "type": "object",
            "properties": {
              "number": {"type": "integer"},
              "page_name": {"type": "string"},
              "url": {"type": "string", "format": "uri"},
              "title_tag": {"type": "string"},
              "seo_strategy": {"type": "string"}
            },
            "required": ["number", "page_name", "url", "title_tag", "seo_strategy"]
          }
        },
        "service_sub_pages": {
          "type": "array",
          "items": {
            "type": "object",
            "properties": {
              "number": {"type": "integer"},
              "page_name": {"type": "string"},
              "url": {"type": "string", "format": "uri"},
              "current_status": {"type": "string"}
            },
            "required": ["number", "page_name", "url", "current_status"]
          }
        },
        "verification_notes": {
          "type": "array",
          "items": {
            "type": "object",
            "properties": {
              "item": {"type": "string"},
              "verification_limitation": {"type": "string"},
              "recommended_manual_check": {"type": "string"}
            },
            "required": ["item", "verification_limitation", "recommended_manual_check"]
          }
        }
      },
      "required": ["core_pages", "service_sub_pages"]
    }
  },
  "required": ["business_name", "domain", "primary_location", "part_1"]
}
```

---

### 4. Add Token Optimization Guidelines

Add to the prompt:
```markdown
## TOKEN OPTIMIZATION

- Use concise bullet points (max 2 lines each)
- Avoid repetitive phrases across rows
- Reference findings by number instead of repeating full descriptions
- Omit empty table cells with "—" instead of leaving blank
- Limit SEO Strategy to 8-30 words as specified
- Use abbreviations after first use (e.g., "E-E-A-T" after "Experience, Expertise, Authoritativeness, Trustworthiness")
```

---

### 5. Add Explicit Anti-Hallucination Rules

```markdown
## ANTI-HALLUCINATION RULES

- NEVER invent URLs, title tags, or page content
- NEVER guess HTTP status codes — use "Unable to verify" if unknown
- NEVER assume indexation status without Search Console access
- NEVER claim search volume, traffic, or ranking data
- NEVER fabricate structured data findings
- If a tool fails, report the failure explicitly
- Distinguish "not found" from "not checked"
```

---

### 6. Improved Prompt Loader (Composable)

```python
# src/prompt_loader.py - Enhanced version
from functools import lru_cache
from pathlib import Path
from typing import List


REPO_ROOT = Path(__file__).resolve().parents[1]
PROMPT_DIR = REPO_ROOT / ".github" / "prompts" / "seo_audit"


SECTION_FILES = [
    "system_prompt.md",
    "instructions.md",
    "audit_method.md",
    "output_format.md",
    "quality_rules.md",
]


@lru_cache
def load_audit_prompt(sections: List[str] = None) -> str:
    """Load the SEO audit prompt from modular sections.
    
    Args:
        sections: Optional list of section filenames to include.
                  Defaults to all standard sections.
    
    Returns:
        Combined prompt text with sections separated by clear boundaries.
    """
    if sections is None:
        sections = SECTION_FILES
    
    parts = []
    for section_file in sections:
        path = PROMPT_DIR / section_file
        if path.exists():
            content = path.read_text(encoding="utf-8").strip()
            parts.append(f"## {section_file.replace('.md', '').replace('_', ' ').title()}\n\n{content}")
        else:
            parts.append(f"## {section_file}\n\n[SECTION NOT FOUND: {section_file}]")
    
    return "\n\n---\n\n".join(parts)


def get_available_sections() -> List[str]:
    """Return list of available prompt section files."""
    return [f.name for f in PROMPT_DIR.glob("*.md") if f.is_file()]
```

---

### 7. Add Prompt Versioning

```markdown
<!-- 
PROMPT VERSION: 2.0.0
LAST UPDATED: 2026-08-03
CHANGELOG:
- v2.0.0: Modularized into sections, added validation schema, fixed numbering
- v1.0.0: Initial monolithic prompt
-->
```

---

### 8. Add Example Output (Few-Shot)

Include a minimal valid example in `output_format.md`:

```markdown
## EXAMPLE OUTPUT (Reference)

Complete SEO Audit & Advanced Ranking Strategy
Example Business (example.com) — New York, NY
PART 1: FULL WEBSITE AUDIT — ALL PAGES & URLs

### 1.1 Core Pages

Discovery sources: sitemap.xml, main navigation, footer

| # | Page Name | URL | Title Tag | SEO Strategy |
|---|---|---|---|---|
| 1 | Home | https://example.com/ | Example Business - Professional Services | • Title matches H1 • Add local keyword "NYC" • Include primary service in title |
| 2 | About Us | https://example.com/about/ | About Us | • Thin content (150 words) • Add team credentials for E-E-A-T • Link to service pages |

### 1.2 Service Sub-Pages

| # | Page Name | URL | Current Status |
|---|---|---|---|
| 1 | SEO Consulting | https://example.com/services/seo/ | • Exists, accessible (200) • Verified title • Linked from service hub • Thin content (200 words) • Needs Service schema • Add case studies |

#### Service Architecture Findings

1. **Thin service pages** — All 5 service pages under 300 words; expand with process, outcomes, FAQs
2. **Missing Service schema** — No structured data on any service page; add Service schema with areaServed
3. **Weak internal linking** — Service hub links to services but services don't cross-link; add related services section
```

---

### 9. Add Configuration for Customization

```python
# src/prompt_config.py
from dataclasses import dataclass
from typing import Optional


@dataclass
class PromptConfig:
    """Configuration for SEO audit prompt behavior."""
    
    # Output control
    include_verification_notes: bool = True
    max_core_pages: int = 50
    max_service_pages: int = 30
    max_findings: int = 5
    
    # Strictness
    require_citations: bool = True
    allow_unverified_titles: bool = True  # Mark as "Not verified" instead of guessing
    strict_no_hallucination: bool = True
    
    # Format
    table_format: str = "markdown"  # or "json"
    include_example: bool = False
    
    # Business context
    business_type: str = "service"  # "service", "ecommerce", "local", "blog"
    primary_location: Optional[str] = None
    
    def to_prompt_addendum(self) -> str:
        """Generate prompt addendum from config."""
        lines = ["## RUNTIME CONFIGURATION"]
        lines.append(f"- Business type: {self.business_type}")
        if self.primary_location:
            lines.append(f"- Primary location: {self.primary_location}")
        lines.append(f"- Max core pages: {self.max_core_pages}")
        lines.append(f"- Max service pages: {self.max_service_pages}")
        lines.append(f"- Max findings: {self.max_findings}")
        lines.append(f"- Require citations: {self.require_citations}")
        lines.append(f"- Allow unverified titles: {self.allow_unverified_titles}")
        return "\n".join(lines)
```

---

### 10. Add Unit Tests for Prompt Loading

```python
# tests/test_prompt_loader.py
import pytest
from src.prompt_loader import load_audit_prompt, get_available_sections


def test_load_default_prompt():
    """Test that default prompt loads without error."""
    prompt = load_audit_prompt()
    assert isinstance(prompt, str)
    assert len(prompt) > 1000  # Substantial prompt
    assert "SEO" in prompt
    assert "audit" in prompt.lower()


def test_load_specific_sections():
    """Test loading only specific sections."""
    prompt = load_audit_prompt(["system_prompt.md", "instructions.md"])
    assert "System Prompt" in prompt
    assert "Instructions" in prompt
    assert "Audit Method" not in prompt


def test_available_sections():
    """Test that expected sections exist."""
    sections = get_available_sections()
    expected = {"system_prompt.md", "instructions.md", "audit_method.md", 
                "output_format.md", "quality_rules.md"}
    assert expected.issubset(set(sections))


def test_prompt_contains_required_elements():
    """Test that prompt contains all required structural elements."""
    prompt = load_audit_prompt()
    required = [
        "Complete SEO Audit",
        "PART 1: FULL WEBSITE AUDIT",
        "1.1 Core Pages",
        "1.2 Service Sub-Pages",
        "Discovery sources:",
        "| # | Page Name | URL | Title Tag | SEO Strategy |",
        "| # | Page Name | URL | Current Status |",
        "Service Architecture Findings",
        "FINAL QUALITY RULES",
    ]
    for element in required:
        assert element in prompt, f"Missing required element: {element}"
```

---

## Implementation Priority

| Priority | Improvement | Effort | Impact |
|----------|-------------|--------|--------|
| 1 | Fix numbering inconsistencies | Low | High (correctness) |
| 2 | Add anti-hallucination rules | Low | High (reliability) |
| 3 | Modularize prompt files | Medium | High (maintainability) |
| 4 | Enhance prompt_loader.py | Medium | High (flexibility) |
| 5 | Add output validation schema | Medium | Medium (quality) |
| 6 | Add token optimization guidelines | Low | Medium (cost) |
| 7 | Add prompt versioning | Low | Medium (traceability) |
| 8 | Add example output (few-shot) | Medium | High (consistency) |
| 9 | Add configuration class | Medium | Medium (customization) |
| 10 | Add unit tests | Medium | High (reliability) |

---

## Quick Wins (Do First)

1. **Fix the duplicate numbering** in the current file (lines 16-20, 54-55, 63-70)
2. **Add anti-hallucination rules** section
3. **Add token optimization guidelines** section
4. **Add prompt version header** at top of file

These four changes can be made directly to the existing file with minimal risk and immediate benefit.│   ├── instructions.md           # Core instructions (numbered)
│   ├── audit_method.md           # Step-by-step process
│   ├── output_format.md          # Report structure & tables
│   ├── quality_rules.md          # Final validation rules
│   └── prompt_loader.py          # Composable loader
```

**Benefits:**
- Easier to maintain and version
- Can A/B test individual sections
- Reusable across different audit types
- Better git diff visibility

---

### 3. Add Output Validation Schema

Add a JSON schema for validating the generated report structure:

```json
{
  "type": "object",
  "properties": {
    "business_name": {"type": "string"},
    "domain": {"type": "string"},
    "primary_location": {"type": "string"},
    "part_1": {
      "type": "object",
      "properties": {
        "core_pages": {
          "type": "array",
          "items": {
            "type": "object",
            "properties": {
              "number": {"type": "integer"},
              "page_name": {"type": "string"},
              "url": {"type": "string", "format": "uri"},
              "title_tag": {"type": "string"},
              "seo_strategy": {"type": "string"}
            },
            "required": ["number", "page_name", "url", "title_tag", "seo_strategy"]
          }
        },
        "service_sub_pages": {
          "type": "array",
          "items": {
            "type": "object",
            "properties": {
              "number": {"type": "integer"},
              "page_name": {"type": "string"},
              "url": {"type": "string", "format": "uri"},
              "current_status": {"type": "string"}
            },
            "required": ["number", "page_name", "url", "current_status"]
          }
        },
        "verification_notes": {
          "type": "array",
          "items": {
            "type": "object",
            "properties": {
              "item": {"type": "string"},
              "verification_limitation": {"type": "string"},
              "recommended_manual_check": {"type": "string"}
            },
            "required": ["item", "verification_limitation", "recommended_manual_check"]
          }
        }
      },
      "required": ["core_pages", "service_sub_pages"]
    }
  },
  "required": ["business_name", "domain", "primary_location", "part_1"]
}
```

---

### 4. Add Token Optimization Guidelines

Add to the prompt:
```markdown
## TOKEN OPTIMIZATION

- Use concise bullet points (max 2 lines each)
- Avoid repetitive phrases across rows
- Reference findings by number instead of repeating full descriptions
- Omit empty table cells with "—" instead of leaving blank
- Limit SEO Strategy to 8-30 words as specified
- Use abbreviations after first use (e.g., "E-E-A-T" after "Experience, Expertise, Authoritativeness, Trustworthiness")
```

---

### 5. Add Explicit Anti-Hallucination Rules

```markdown
## ANTI-HALLUCINATION RULES

- NEVER invent URLs, title tags, or page content
- NEVER guess HTTP status codes — use "Unable to verify" if unknown
- NEVER assume indexation status without Search Console access
- NEVER claim search volume, traffic, or ranking data
- NEVER fabricate structured data findings
- If a tool fails, report the failure explicitly
- Distinguish "not found" from "not checked"
```

---

### 6. Improved Prompt Loader (Composable)

```python
# src/prompt_loader.py - Enhanced version
from functools import lru_cache
from pathlib import Path
from typing import List


REPO_ROOT = Path(__file__).resolve().parents[1]
PROMPT_DIR = REPO_ROOT / ".github" / "prompts" / "seo_audit"


SECTION_FILES = [
    "system_prompt.md",
    "instructions.md",
    "audit_method.md",
    "output_format.md",
    "quality_rules.md",
]


@lru_cache
def load_audit_prompt(sections: List[str] = None) -> str:
    """Load the SEO audit prompt from modular sections.
    
    Args:
        sections: Optional list of section filenames to include.
                  Defaults to all standard sections.
    
    Returns:
        Combined prompt text with sections separated by clear boundaries.
    """
    if sections is None:
        sections = SECTION_FILES
    
    parts = []
    for section_file in sections:
        path = PROMPT_DIR / section_file
        if path.exists():
            content = path.read_text(encoding="utf-8").strip()
            parts.append(f"## {section_file.replace('.md', '').replace('_', ' ').title()}\n\n{content}")
        else:
            parts.append(f"## {section_file}\n\n[SECTION NOT FOUND: {section_file}]")
    
    return "\n\n---\n\n".join(parts)


def get_available_sections() -> List[str]:
    """Return list of available prompt section files."""
    return [f.name for f in PROMPT_DIR.glob("*.md") if f.is_file()]
```

---

### 7. Add Prompt Versioning

```markdown
<!-- 
PROMPT VERSION: 2.0.0
LAST UPDATED: 2026-08-03
CHANGELOG:
- v2.0.0: Modularized into sections, added validation schema, fixed numbering
- v1.0.0: Initial monolithic prompt
-->
```

---

### 8. Add Example Output (Few-Shot)

Include a minimal valid example in `output_format.md`:

```markdown
## EXAMPLE OUTPUT (Reference)

Complete SEO Audit & Advanced Ranking Strategy
Example Business (example.com) — New York, NY
PART 1: FULL WEBSITE AUDIT — ALL PAGES & URLs

### 1.1 Core Pages

Discovery sources: sitemap.xml, main navigation, footer

| # | Page Name | URL | Title Tag | SEO Strategy |
|---|---|---|---|---|
| 1 | Home | https://example.com/ | Example Business - Professional Services | • Title matches H1 • Add local keyword "NYC" • Include primary service in title |
| 2 | About Us | https://example.com/about/ | About Us | • Thin content (150 words) • Add team credentials for E-E-A-T • Link to service pages |

### 1.2 Service Sub-Pages

| # | Page Name | URL | Current Status |
|---|---|---|---|
| 1 | SEO Consulting | https://example.com/services/seo/ | • Exists, accessible (200) • Verified title • Linked from service hub • Thin content (200 words) • Needs Service schema • Add case studies |

#### Service Architecture Findings

1. **Thin service pages** — All 5 service pages under 300 words; expand with process, outcomes, FAQs
2. **Missing Service schema** — No structured data on any service page; add Service schema with areaServed
3. **Weak internal linking** — Service hub links to services but services don't cross-link; add related services section
```

---

### 9. Add Configuration for Customization

```python
# src/prompt_config.py
from dataclasses import dataclass
from typing import Optional


@dataclass
class PromptConfig:
    """Configuration for SEO audit prompt behavior."""
    
    # Output control
    include_verification_notes: bool = True
    max_core_pages: int = 50
    max_service_pages: int = 30
    max_findings: int = 5
    
    # Strictness
    require_citations: bool = True
    allow_unverified_titles: bool = True  # Mark as "Not verified" instead of guessing
    strict_no_hallucination: bool = True
    
    # Format
    table_format: str = "markdown"  # or "json"
    include_example: bool = False
    
    # Business context
    business_type: str = "service"  # "service", "ecommerce", "local", "blog"
    primary_location: Optional[str] = None
    
    def to_prompt_addendum(self) -> str:
        """Generate prompt addendum from config."""
        lines = ["## RUNTIME CONFIGURATION"]
        lines.append(f"- Business type: {self.business_type}")
        if self.primary_location:
            lines.append(f"- Primary location: {self.primary_location}")
        lines.append(f"- Max core pages: {self.max_core_pages}")
        lines.append(f"- Max service pages: {self.max_service_pages}")
        lines.append(f"- Max findings: {self.max_findings}")
        lines.append(f"- Require citations: {self.require_citations}")
        lines.append(f"- Allow unverified titles: {self.allow_unverified_titles}")
        return "\n".join(lines)
```

---

### 10. Add Unit Tests for Prompt Loading

```python
# tests/test_prompt_loader.py
import pytest
from src.prompt_loader import load_audit_prompt, get_available_sections


def test_load_default_prompt():
    """Test that default prompt loads without error."""
    prompt = load_audit_prompt()
    assert isinstance(prompt, str)
    assert len(prompt) > 1000  # Substantial prompt
    assert "SEO" in prompt
    assert "audit" in prompt.lower()


def test_load_specific_sections():
    """Test loading only specific sections."""
    prompt = load_audit_prompt(["system_prompt.md", "instructions.md"])
    assert "System Prompt" in prompt
    assert "Instructions" in prompt
    assert "Audit Method" not in prompt


def test_available_sections():
    """Test that expected sections exist."""
    sections = get_available_sections()
    expected = {"system_prompt.md", "instructions.md", "audit_method.md", 
                "output_format.md", "quality_rules.md"}
    assert expected.issubset(set(sections))


def test_prompt_contains_required_elements():
    """Test that prompt contains all required structural elements."""
    prompt = load_audit_prompt()
    required = [
        "Complete SEO Audit",
        "PART 1: FULL WEBSITE AUDIT",
        "1.1 Core Pages",
        "1.2 Service Sub-Pages",
        "Discovery sources:",
        "| # | Page Name | URL | Title Tag | SEO Strategy |",
        "| # | Page Name | URL | Current Status |",
        "Service Architecture Findings",
        "FINAL QUALITY RULES",
    ]
    for element in required:
        assert element in prompt, f"Missing required element: {element}"
```

---

## Implementation Priority

| Priority | Improvement | Effort | Impact |
|----------|-------------|--------|--------|
| 1 | Fix numbering inconsistencies | Low | High (correctness) |
| 2 | Add anti-hallucination rules | Low | High (reliability) |
| 3 | Modularize prompt files | Medium | High (maintainability) |
| 4 | Enhance prompt_loader.py | Medium | High (flexibility) |
| 5 | Add output validation schema | Medium | Medium (quality) |
| 6 | Add token optimization guidelines | Low | Medium (cost) |
| 7 | Add prompt versioning | Low | Medium (traceability) |
| 8 | Add example output (few-shot) | Medium | High (consistency) |
| 9 | Add configuration class | Medium | Medium (customization) |
| 10 | Add unit tests | Medium | High (reliability) |

---

## Quick Wins (Do First)

1. **Fix the duplicate numbering** in the current file (lines 16-20, 54-55, 63-70)
2. **Add anti-hallucination rules** section
3. **Add token optimization guidelines** section
4. **Add prompt version header** at top of file





