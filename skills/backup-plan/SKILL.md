---
name: backup-plan
description: 'Design a backup and restore plan for servers, databases and files: what to back up, how often, where, how long to keep it, and how to test a restore. Use for "backup plan", "how should I back up", "disaster recovery".'
metadata:
  title: Backup plan
  category: IT & security
  specialist: it
  inputs: the systems to protect
---

# Backup plan

## Steps
1. List what must be protected and how much loss is acceptable (RPO) and how fast it must come back (RTO).
2. Apply 3-2-1: three copies, two kinds of storage, one off-site (and one offline or immutable against ransomware).
3. For each system: method (database dump, snapshot, file sync), schedule, retention, encryption.
4. Plan the restore test: what, how often, and who checks it. A backup never restored is not a backup.
5. Give the commands or cron lines for the user's systems when known.

## Output
### What and why
| System | RPO | RTO |
### Schedule
| System | Method | When | Kept for | Where |
### Restore test
### Commands
