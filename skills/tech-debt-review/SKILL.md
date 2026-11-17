---
name: tech-debt-review
description: List a codebase's technical debt and rank it by cost and risk, with a plan to pay it down. Use for "tech debt list", "what should we refactor", "code health".
metadata:
  title: Tech debt review
  category: Engineering
  specialist: development
  inputs: the codebase or the pain points
---

# Tech debt review

## Steps
1. From what the user describes or pastes, list the debt: outdated dependencies, missing tests, duplicated code, fragile modules, manual steps, unclear ownership.
2. For each: what it costs now (bugs, slowness, onboarding) and the risk if left.
3. Rank by cost × risk ÷ effort.
4. Turn the top items into concrete tasks small enough for one sprint each.

## Output
| Item | Cost now | Risk | Effort | Priority |
Then the first tasks.
