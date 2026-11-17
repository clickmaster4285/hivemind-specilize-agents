---
name: k8s-troubleshoot
description: Find out why a Kubernetes workload is failing — pods pending, crash-looping, not ready, or not reachable — and fix it. Use for "pod is crashing", "CrashLoopBackOff", "service not reachable", "kubectl help".
allowed-tools: search_knowledge_base diagnose_server web_search
metadata:
  title: Kubernetes troubleshooting
  category: IT & security
  specialist: it
  inputs: the error or what kubectl shows
---

# Kubernetes troubleshooting

## Steps
1. Name the symptom: Pending, CrashLoopBackOff, ImagePullBackOff, not Ready, OOMKilled, unreachable.
2. Give the read-only commands to gather evidence in order: kubectl get pods -o wide, describe pod, logs (--previous), get events, get svc/endpoints.
3. From what the user pastes, name the most likely cause and the fix; cite the Kubernetes docs from the knowledge base.
4. If the cluster runs on one of the user's servers, diagnose_server can run the read-only commands.

## Output
### Symptom
### Evidence to collect
The commands.
### Likely cause
### Fix
The change, then how to confirm it worked.
