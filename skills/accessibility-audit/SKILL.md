---
name: accessibility-audit
description: 'Check a page or a piece of HTML for accessibility problems (WCAG): what fails, who it affects, and the fix. Use for "accessibility check", "is this accessible", "WCAG audit", "screen reader".'
allowed-tools: search_knowledge_base site_audit web_search
metadata:
  title: Accessibility audit
  category: Engineering
  specialist: development
  inputs: the page URL or its HTML
---

# Accessibility audit

## Steps
1. Work from the HTML the user pasted, or the page's URL (site_audit reads it).
2. Check: text alternatives for images, headings in order, labels on form fields, link and button names, colour contrast, keyboard use and focus, ARIA used correctly (no ARIA is better than wrong ARIA), page language, landmarks.
3. Look up the rule in the knowledge base (MDN Accessibility) and cite it.
4. For each problem: the element, the WCAG criterion, who it affects (screen reader, keyboard, low vision), and the corrected code.

## Output
### Summary
A count of problems by severity.
### Problems
| Severity | Element | WCAG | Who it affects | Fix |
Then the corrected code for the worst ones.
