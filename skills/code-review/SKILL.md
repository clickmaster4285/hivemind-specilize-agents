---
name: code-review
description: Review code for bugs, security problems, readability and missing tests, with concrete fixes. Use when the user pastes code or a diff and asks for a review or "is this OK".
metadata:
  title: Code review
  category: Engineering
  specialist: development
  inputs: the code or diff to review
---

# Code review

## Steps
1. Work out what the code is meant to do; if it is unclear, say what you assumed.
2. Look for, in this order: bugs and wrong behaviour, security problems (injection, secrets, unsafe input),
   error handling, performance traps, readability, missing tests.
3. For every finding, quote the lines, say why it matters, and show the fixed code.
4. Mention what is done well, briefly.

## Output
### Verdict
Ready / ready with small fixes / needs changes.
### Findings
Most severe first, each: **severity** — what, why, fix.
### Tests to add
