# SEO Audit Philosophy

The AI SEO Agent should perform a professional website audit using a structured methodology based on Google's published guidance and modern SEO best practices.

The purpose of the audit is not only to identify technical issues, but also to explain their **business impact**, prioritize corrective actions, and provide practical implementation guidance.

Every audit must produce **consistent, repeatable, and actionable** results.

---

## Audit Categories

Rather than inventing our own categories, we use the categories that experienced SEO consultants naturally work with.

### 1. Technical SEO _(Highest Priority)_

The system should verify:

- HTTP Status Code
- HTTPS
- Crawlability
- Indexability
- `robots.txt`
- `sitemap.xml`
- Canonical URLs
- Redirects
- Broken Links
- Duplicate Pages
- URL Structure
- Mobile Friendliness
- Core Web Vitals
- Performance
- Structured Data
- Security Headers

> This is the foundation of every audit.

---

### 2. On-Page SEO

The system should analyze:

- Page Title
- Meta Description
- H1
- H2–H6 Structure
- Image ALT Text
- Image Size
- Internal Links
- External Links
- Anchor Text
- URL Readability
- Keyword Usage
- Content Length
- Duplicate Content
- Thin Content

---

### 3. Content Quality

The system should evaluate:

- Search Intent
- Content Completeness
- Readability
- Content Freshness
- Topic Coverage
- FAQ Presence
- User Value

> Unlike technical checks, these are AI-assisted evaluations.

---

### 4. User Experience

The audit should include:

- Mobile Responsiveness
- Navigation
- Accessibility
- Loading Speed
- Visual Stability
- User Flow

---

### 5. Authority _(Basic MVP)_

The MVP should perform only basic authority checks:

- Domain Authority _(optional)_
- Basic Backlink Summary _(optional)_
- Brand Presence

> Detailed backlink analysis belongs in a later version.

---

## Severity Levels

Every issue should be assigned one severity level.

| Severity | Meaning |
| -------- | ------- |
| **Critical** | Prevents crawling, indexing, or major SEO functionality |
| **High** | Strongly impacts rankings, visibility, or user experience |
| **Medium** | Meaningful improvement opportunity, not immediately blocking |
| **Low** | Best practice or minor optimization opportunity |
| **Informational** | Informational context or verified healthy status |

---

## Finding & Recommendation Statuses

Every audit check and finding must have an explicit status:

| Status | Meaning |
| ------ | ------- |
| **Pass** | Verified to meet the SEO standard or best practice |
| **Issue** | Verified defect or sub-optimal configuration requiring fix |
| **Opportunity** | Potential expansion or strategic optimization area |
| **Unverified** | Could not be measured or verified from available audit evidence |
| **Not applicable** | Check does not apply to this site type or page configuration |

---

## Evidence Provenance Tiers

Every finding and claim must trace to a verified provenance tier:

| Provenance Tier | Description |
| --------------- | ----------- |
| `measured` | Directly measured from crawler, DOM inspection, or technical fetch |
| `researched` | Provider-returned search citations with verified source URLs |
| `derived` | Deterministically calculated from measured inputs (e.g., ratios, counts) |
| `consultant_assessment` | Bounded LLM qualitative evaluation based strictly on evidence |
| `client_input_required` | Requires client/owner confirmation (e.g., target market, CMS) |
| `integration_required` | Requires third-party authenticated API (e.g., GSC, GA4, backlink API) |

---

## Every Issue Must Include

Instead of simply reporting `Missing Meta Description`, every recommendation must contain the standard fields:

| Field | Description | Example |
| ----- | ----------- | ------- |
| **Finding ID** | Unique identifier for tracking | `TECH-META-001` |
| **Category** | SEO category | `On-Page SEO` |
| **Affected URLs** | List of specific URLs | `https://example.com/services` |
| **Status** | Current state | `Issue` |
| **Evidence** | Measurable observation | `Meta description tag is absent in static HTML` |
| **Severity** | Impact severity | `High` |
| **Business Impact** | Impact on traffic/CTR/visibility | `Reduced click-through rate from search results` |
| **Why It Matters** | SEO reason | `Search engines generate automated or truncated snippets` |
| **Recommended Action** | Specific fix instructions | `Add a unique 150–160 character meta description with primary keyword` |
| **Priority** | Execution priority (1-5) | `2` |
| **Effort** | Implementation effort | `Easy` |
| **Estimated Time** | Time to implement | `10 minutes` |
| **Suggested Owner** | Responsible role | `Content Writer` |
| **Dependencies** | Pre-requisites | `Target keyword finalized` |
| **Validation Method** | How to verify after fix | `Inspect HTML <meta name="description"> and rerun audit` |
| **KPI** | Target metric | `Search snippet CTR` |
| **Provenance** | Data source tier | `measured` |

---

## SEO Scoring Engine & Category Weights

Each audit calculates a deterministic **Overall SEO Score** from **0–100** based only on verified evidence.

### Category Weights

| Category | Weight | Scope |
| -------- | ------ | ----- |
| **Technical SEO** | 40% | Crawlability, indexability, robots, sitemap, canonical, HTTPS, redirects, errors |
| **On-Page SEO** | 30% | Title tags, meta descriptions, headings, URL structure, image alt tags |
| **Content Quality** | 20% | Content depth, E-E-A-T signals, duplicate content, search intent alignment |
| **User Experience & Performance** | 10% | Mobile responsiveness, security headers, Core Web Vitals (when available) |
| **Total** | **100%** | Full Score |

### Scoring Principles

1. **Deterministic Only**: Scores are calculated using transparent, deterministic rules. Subjective LLM opinions never alter scores.
2. **Evidence Coverage Ratio**: The report displays both the numeric score and the **Evidence Coverage Ratio** (percentage of applicable audit checks verified).
3. **No Phantom Points**: Unverified checks (e.g., missing API integrations or unmeasured CWV) are never scored as 100% or 0% — they are omitted from the denominator or reported with explicit coverage limitations.
4. **Proportional Per-Page Deductions**: Per-page checks scale deductions proportionally to the fraction of audited pages affected.
5. **Non-Applicable Rules Excluded**: Rules marked `Not applicable` (such as local schema for a pure SaaS site) are excluded from scoring denominators.

---

## AI Recommendations

Every recommendation must answer five questions:

1. What is wrong?
2. Why is it important?
3. What happens if I ignore it?
4. How do I fix it?
5. How difficult is it?

This transforms the tool from an SEO scanner into an **AI SEO consultant**.

---

## Roadmap Feature — 30-Day SEO Action Plan

> **Not in MVP.** This feature is on the roadmap because it is expected to become a key differentiator.

After every audit, the AI should automatically generate a structured **30-Day SEO Action Plan**.

### Example Plan

#### Week 1

- Fix broken links
- Add missing meta descriptions
- Correct canonical URLs

#### Week 2

- Improve page speed
- Compress images
- Add structured data

#### Week 3

- Improve content quality
- Expand thin pages

#### Week 4

- Internal linking optimization
- Final verification audit

> No mainstream SEO platform provides a project-managed implementation roadmap as a core feature. This aligns with a project-management-first approach and could become one of the defining capabilities of the product.
