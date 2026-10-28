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
For a NETWORK or INFRASTRUCTURE topology (servers, routers, firewalls, switches, sites, cloud), PREFER
`architecture-beta` with real vector icons from the bundled `mdi:` and `logos:` packs — it looks far better
than plain boxes. Keep it small and valid:
- Declare parts with `service <id>(mdi:<icon>)[Label]`; group related parts with `group <id>[Label]` and
  add ` in <id>` to each service inside it.
- Connect with sided edges: `a:R --> L:b` (sides are L / R / T / B). Put IPs, masks and ports in the prose,
  never in the diagram.
- Use ONLY real icon names. Safe ones: `mdi:cloud`, `mdi:server`, `mdi:firewall`, `mdi:router`,
  `mdi:router-network`, `mdi:lan`, `mdi:switch`, `mdi:monitor`, `mdi:laptop`, `mdi:database`, `mdi:printer`,
  `mdi:web`; and `logos:aws`, `logos:azure`, `logos:google-cloud`, `logos:nginx`, `logos:postgresql` for
  branded services. Represent many identical nodes as one (e.g. `Remote Sites`), not ten separate boxes.
An office network with remote sites, done right:
```mermaid
architecture-beta
  group hq[HQ Data Center]
  service net(mdi:cloud)[Internet]
  service fw(mdi:firewall)[Firewall] in hq
  service core(mdi:router-network)[Core Router] in hq
  service srv(mdi:server)[Servers] in hq
  service sites(mdi:lan)[Remote Sites]
  sites:R --> L:net
  net:R --> L:fw
  fw:R --> L:core
  core:R --> L:srv
```
Use the emoji `flowchart` above for processes, steps, flows and hierarchies — and whenever you are not sure
an `architecture-beta` icon name or its syntax is right, since a valid emoji flowchart beats a broken
topology."""


# Separate from DIAGRAM_RULES because it contains JSON braces: it must be appended to a prompt AFTER any
# str.format(), never inside a .format template. Renders to a designed infographic via NetworkDiagram.tsx.
NETDIAGRAM_RULES = """
NETWORK / INFRASTRUCTURE DESIGN (multi-site, enterprise, "N sites to HQ", hub-and-spoke). First pick the
LEVEL from the user's wording, then follow that level — they are two different deliverables:

HIGH-LEVEL DESIGN — the default, or when they say "HLD", "overview", "a network diagram", "how it
connects". Give one or two sentences, then a designed infographic as a fenced ```netdiagram block
containing ONLY JSON in this exact shape (list EVERY site, up to ~16):
```netdiagram
{"title": "10 Sites to HQ Network", "subtitle": "All sites connect securely to the Head Office (HQ)",
 "cloud": "Internet", "linkLabel": "VPN Tunnel (IPsec/SSL)",
 "hq": {"label": "Head Office (HQ)", "nodes": [{"icon": "firewall", "label": "Firewall / UTM"},
   {"icon": "core switch", "label": "Core Switch"}, {"icon": "load balancer", "label": "Load Balancer"},
   {"icon": "server", "label": "App / DB / File Servers"}, {"icon": "NAS", "label": "SAN / NAS Storage"}]},
 "sites": [{"label": "Site 1", "sub": "Branch Office"}, {"label": "Site 2", "sub": "Branch Office"}],
 "siteTemplate": {"label": "Inside each branch site", "devices": ["router", "switch", "PCs", "printer"]},
 "legend": ["Site / Office", "Router", "Switch", "Firewall", "Wi-Fi AP", "VPN Tunnel"],
 "features": ["Secure site-to-HQ VPN (IPsec/SSL)", "Centralized management", "Scalable to 10+ sites"],
 "notes": ["Each site has a VPN-capable router/firewall", "Static IPs or dynamic DNS", "Redundant HQ links"]}
```
Include "siteTemplate" (a typical site's internal LAN chain — router, switch, PCs, add printer/AP/server if
relevant): it is drawn inside EVERY site card, linked and labelled, so it shows how each site connects to its
own devices. A site that differs may carry its own "devices" list (max 5), e.g.
{"label": "Site 3", "sub": "Branch Office", "devices": ["router", "switch", "Wi-Fi AP", "IP camera", "VoIP phone"]}.
Device/icon names are PLAIN ENGLISH — the real device, whatever it is (router, switch, firewall, load
balancer, server, NAS, Wi-Fi AP, IP camera, VoIP phone, DNS server, PCs, printer, IoT sensor, cloud, …);
each is drawn with a fitting icon, so never force a device into a short wrong word.
Emit VALID JSON only inside the block — no comments, no trailing commas, double quotes only. Explanation
goes in prose before or after, never inside.

LOW-LEVEL DESIGN — when they say "LLD", "low-level", "detailed design", "VLAN and IP plan", "addressing
scheme", or ask for the detail. Do NOT draw one big diagram — detail belongs in TABLES. Produce a
structured document:
1. one or two sentences of context;
2. an IP addressing / subnet plan — a Markdown table (Site/Area | Subnet | VLAN | Gateway | Purpose);
3. a VLAN plan — a table (VLAN ID | Name | Purpose | Scope);
4. a device inventory — a table (Role | Device / qty | Location);
5. redundancy & routing notes (WAN primary/backup, HA, failover) as short bullets;
6. a per-site template (what every branch office has), as a short list or small table;
7. optionally ONE or TWO SMALL, valid diagrams of a SINGLE area only (a per-site template, or the HQ core),
   each ≤7 nodes using the emoji flowchart or architecture-beta rules above.

NEVER draw a single Mermaid flowchart that holds all the sites, many subgraphs, or every VLAN at once — it
auto-lays-out into unreadable spaghetti. Big designs = tables (+ small focused diagrams), never one giant
diagram."""
