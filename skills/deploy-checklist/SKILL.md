---
name: deploy-checklist
description: 'Prepare a go / no-go checklist for a release: checks before, the deploy steps, checks after, and how to roll back. Use for "deploy checklist", "release prep", "going live".'
metadata:
  title: Deploy checklist
  category: IT & security
  specialist: it
  inputs: what is being released and where
---

# Deploy checklist

## Steps
1. Before: tests green, backups taken, migrations reviewed, config and secrets set, people told.
2. Deploy: the exact steps in order for the user's stack (server, PM2, Docker, Kubernetes).
3. After: health checks, logs, key pages and flows, error rates, for the first hour.
4. Rollback: the trigger (what makes it a no-go), the steps, and data concerns (migrations that cannot be undone).

## Output
Checklists with [ ] boxes under: Before · Deploy · After · Rollback.
