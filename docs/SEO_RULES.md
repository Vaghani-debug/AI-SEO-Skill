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
| **High** | Strongly impacts rankings or user experience |
| **Medium** | Should be improved but not urgent |
| **Low** | Best practice or optimization opportunity |
| **Info** | Informational only |

---

## Every Issue Must Include

Instead of simply reporting `Missing Meta Description`, every issue must contain the following fields:

| Field | Example |
| ----- | ------- |
| **Issue** | Missing Meta Description |
| **Severity** | High |
| **Business Impact** | Reduced click-through rate from search results |
| **Why It Matters** | Search engines may generate less compelling snippets |
| **Recommendation** | Add a unique 150–160 character meta description |
| **Estimated Effort** | 5–10 minutes |
| **Priority** | 2 |

> This structured format is one of the biggest improvements over traditional SEO reports.

---

## SEO Score

Each audit produces an **Overall SEO Score** from **0–100**, broken into weighted categories.

| Category | Weight |
| -------- | ------ |
| Technical SEO | 40% |
| On-Page SEO | 30% |
| Content Quality | 20% |
| User Experience | 10% |

**Why this weighting?**
Fixing technical issues before content is generally more impactful — if search engines cannot properly crawl or index the site, content improvements have limited effect.

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
