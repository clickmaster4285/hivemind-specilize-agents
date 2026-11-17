---
name: security-review
description: 'Review code, a config or a design for security problems using the OWASP practices: what is wrong, how it could be abused, and the fix. Use for "security review", "is this secure", "check for vulnerabilities".'
allowed-tools: search_knowledge_base web_search
metadata:
  title: Security review
  category: IT & security
  specialist: development
  inputs: the code, config or design
---

# Security review

## Steps
1. Read what the user gave (code, config, design).
2. Check the common weak points: input validation and injection, authentication and sessions, access control, secrets in code, crypto, error messages, logging, dependencies, file uploads, CORS and headers.
3. Look up the matching OWASP cheat sheet in the knowledge base and cite it.
4. Rank by risk (how easy to abuse × how bad). For each: where, how it could be abused, the fixed code.

## Output
### Verdict
### Findings
| Risk | Where | Problem | How it could be abused | Fix |
Then the corrected code for the high-risk items.
