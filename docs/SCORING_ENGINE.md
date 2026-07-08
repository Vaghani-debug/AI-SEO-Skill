# SCORING_ENGINE.md

Version: 1.0
Status: Approved
Owner: Product Owner

---

# 1. Purpose

This document defines the official SEO scoring methodology for the AI SEO Agent.

The scoring engine converts hundreds of technical SEO checks into:

• Category Scores

• Overall SEO Score

• Website Health Grade

• Issue Priorities

• Business Impact

The scoring engine must always produce deterministic results.

Running the audit multiple times on an unchanged website should always produce identical scores.

---

# 2. Design Principles

The scoring engine follows five principles.

## Principle 1

Consistency

Identical input must produce identical output.

---

## Principle 2

Transparency

Every score deduction must be explainable.

The user should always know:

• Why points were deducted

• Which issue caused the deduction

• How to recover those points

---

## Principle 3

Business Impact

Not every issue has equal importance.

Missing ALT text is not as serious as:

• Blocked robots.txt

• Noindex

• Broken canonical

• 5xx errors

---

## Principle 4

Evidence Based

Only measurable issues should affect the score.

AI opinions must never directly reduce the score.

AI only explains findings.

---

## Principle 5

Fairness

Small websites should not be unfairly penalized.

Large enterprise websites should not receive inflated scores simply because they have more pages.

Scores should be normalized whenever possible.

---

# 3. Overall Score

The platform produces one score.

Range

0 – 100

Display

Large circular gauge.

Colour

90–100

Green

Excellent

80–89

Light Green

Good

70–79

Yellow

Needs Improvement

60–69

Orange

Poor

Below 60

Red

Critical

---

# 4. Category Weights

Overall Score

100%

Technical SEO

40%

On-Page SEO

25%

Content Quality

15%

Performance

10%

Accessibility

5%

Security

5%

Total

100%

These weights reflect the philosophy that if search engines cannot properly crawl, index, or understand a page, improvements in content alone may not achieve the desired results. Technical health forms the foundation, while content, performance, accessibility, and security contribute additional value. Google similarly emphasizes technical requirements and people-first content as complementary aspects of search success rather than a single numeric ranking formula.

---

# 5. Technical SEO Score

Weight

40%

Checks

HTTP Status

HTTPS

Robots.txt

Sitemap

Canonical

Redirects

Indexability

Crawlability

Duplicate URLs

Broken Links

Structured Data

Meta Robots

XML Sitemap

Internal Linking

URL Structure

Every check has:

Weight

Severity

Pass

Warning

Fail

Example

HTTPS

Weight

5

Pass

HTTPS enabled

5 points

Fail

HTTP only

0 points

---

# 6. On-Page SEO

Weight

25%

Checks

Title

Meta Description

H1

Heading Structure

Image ALT

Image File Names

Internal Links

External Links

Anchor Text

URL Readability

Keyword Usage

Content Structure

---

# 7. Content Quality

Weight

15%

Checks

Content Length

Readability

Search Intent

Duplicate Content

Thin Content

Semantic Coverage

FAQ Presence

Freshness

EEAT Indicators

AI assists in evaluating content quality, but scoring should remain tied to observable evidence and clearly defined rules wherever possible.

---

# 8. Performance

Weight

10%

Performance should be based on measurable metrics.

Examples

Largest Contentful Paint

Cumulative Layout Shift

Interaction to Next Paint

Page Size

Image Optimization

Caching

Compression

Render Blocking Resources

Performance metrics should follow current browser tooling and industry guidance rather than arbitrary thresholds.

---

# 9. Accessibility

Weight

5%

Checks

Missing ALT

Labels

ARIA

Heading Order

Contrast

Keyboard Navigation

Viewport

Accessibility improves usability and often supports SEO indirectly.

---

# 10. Security

Weight

5%

Checks

HTTPS

Mixed Content

Security Headers

Safe Browsing

Certificate

Secure Cookies

Security should not dominate the SEO score, but serious security problems should still be surfaced prominently.

---

# 11. Severity Levels

Every issue receives one severity.

Critical

Blocks crawling, indexing or website availability.

High

Strong ranking impact.

Medium

Noticeable improvement opportunity.

Low

Minor optimisation.

Information

No score deduction.

---

# 12. Severity Multipliers

Critical

100%

High

75%

Medium

40%

Low

15%

Information

0%

Severity multipliers determine how much of an issue's maximum deduction is applied.

---

# 13. Issue Priority Matrix

Priority 1

Critical

Fix Immediately

Priority 2

High

Fix This Week

Priority 3

Medium

Fix This Month

Priority 4

Low

Future Optimisation

Priority 5

Information

Monitor Only
