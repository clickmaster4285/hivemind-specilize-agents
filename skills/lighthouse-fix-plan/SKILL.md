---
name: lighthouse-fix-plan
description: 'Turn a Lighthouse or PageSpeed report into a ranked fix plan: what each failing audit means, the fix, and the expected gain. Use for "fix my Lighthouse score", "PageSpeed results", "Core Web Vitals failing".'
allowed-tools: search_knowledge_base web_search site_audit
metadata:
  title: Lighthouse fix plan
  category: Engineering
  specialist: development
  inputs: the Lighthouse / PageSpeed results or the page URL
---

# Lighthouse fix plan

## Steps
1. Read the scores and failing audits the user pasted. If they gave only a URL, run site_audit for what can be checked and ask them to paste the PageSpeed results for the rest.
2. Look up each failing audit in the knowledge base (Lighthouse and web.dev sources) for what it measures and how to fix it; cite it.
3. Rank by impact on LCP, INP and CLS first, then the rest.
4. For each fix give the concrete change (the file, the tag, the setting) for the user's stack if known.
5. Say how to re-test (PageSpeed Insights, Lighthouse in Chrome DevTools).

## Output
### Scores
### Fix plan
| Priority | Audit | What it means | Fix | Expected gain |
### Re-test
