---
name: server-health-check
description: Check one of the user's servers end to end — load, memory, disk, services, recent errors and security basics — and report what needs attention first. Use for "check my server", "is my server OK", routine health checks.
allowed-tools: diagnose_server server_audit
metadata:
  title: Server health check
  category: IT & security
  specialist: it
  inputs: which server (as added in Settings → Servers)
---

# Server health check

## Steps
1. Use diagnose_server on the named server to read its state (read-only commands only).
2. On Linux, also run server_audit for the security basics.
3. Rank what you found: **Critical** (failing now or about to: disk over 90 %, a service down, out of memory),
   **Warning** (trending badly, missing updates, weak settings), **OK**.
4. For each Critical or Warning item, give the exact command that fixes it and how to check it worked.
   Never run a change yourself in this skill — the user decides.

## Output
### Verdict
One line: healthy / needs attention / urgent.
### Findings
| Severity | Area | What we saw | Fix |
### Next steps
A short ordered list, most urgent first.
