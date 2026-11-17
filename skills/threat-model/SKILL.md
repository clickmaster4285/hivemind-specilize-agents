---
name: threat-model
description: 'Build a threat model for a system: what is worth protecting, where the trust boundaries are, how it could be attacked, and the mitigations. Use for "threat model", "attack surface", "what could go wrong security-wise".'
metadata:
  title: Threat model
  category: IT & security
  specialist: it
  inputs: the system and how its parts talk to each other
---

# Threat model

## Steps
1. Draw the system: components, data flows, and trust boundaries (draw_diagram for a diagram).
2. List the assets (data, accounts, money, uptime) and who might attack them.
3. Walk each boundary with STRIDE: spoofing, tampering, repudiation, information disclosure, denial of service, elevation of privilege.
4. Rank threats by likelihood × impact; give a mitigation and its owner for each high one.

## Output
### System
The diagram.
### Assets
### Threats
| Risk | Boundary | STRIDE | Threat | Mitigation |
### Next steps
