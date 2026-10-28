"""One source of truth for the Mermaid diagram rules every answer path shares.

Both the direct assistant answer (``assistant.SYSTEM``) and the web-synthesis answer
(``web_search.WEB_SYSTEM``) can emit a ```mermaid block, so both must carry the same hard-won
rules — SIMPLE, emoji-on-every-node, parse-safe labels. The model benchmark (2026-10-28,
doc/RESPONSE_QUALITY.md) showed these rules, not a bigger model, are what make diagrams render
100% valid; keeping them in one place stops the two prompts from drifting apart.

The text contains no ``{`` / ``}`` so it is safe to concatenate into a ``str.format`` template.
"""

from __future__ import annotations

DIAGRAM_RULES = """When you DO draw a Mermaid diagram, keep it SIMPLE and VALID — a broken or cluttered diagram is worse
than none:
- Small: at most ~7 nodes, one direction (`flowchart LR` or `flowchart TD`). No legend box, no
  subnet/detail boxes, no nested subgraphs, no example "traffic flow" — unless the user explicitly asks.
- Icons: begin EVERY node label with one fitting emoji — `S["🖥️ Server"]`, `W["💻 PC"]`,
  `N["🔀 Switch"]`, `R["🛡️ Router"]`, `I["🌐 Internet"]`. Do NOT use classDef colours in place of icons.
- Parse-safe: always wrap a node label in double quotes and keep it short (2–4 words). Never put `(` `)`
  `:` `|` `/` or `<br>` inside any label. Prefer plain `-->` arrows with NO edge text; if an edge really
  needs a word use `A -->|"Ethernet"| B` — one short quoted word, no punctuation.
- Detail like IP addresses, masks or ports goes in the prose or a small Markdown table, NEVER inside the
  diagram nodes.
A simple office network, done right:
```mermaid
flowchart TD
  PC1["💻 PC 1"] --> SW["🔀 Switch"]
  PC2["💻 PC 2"] --> SW
  SRV["🖥️ File Server"] --> SW
  SW --> FW["🛡️ Router/Firewall"]
  FW --> NET["🌐 Internet"]
```
For a pure infrastructure topology you may instead use `architecture-beta` with real icons
(`service x(mdi:server)[Server]`, packs `mdi:` and `logos:`), but only when you can keep it small and
valid; otherwise use the emoji flowchart above."""
