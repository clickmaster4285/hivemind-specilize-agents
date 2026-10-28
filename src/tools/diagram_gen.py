"""Diagram requests (architecture, flowchart, schematic, sequence, ER, mind map …) → Mermaid.

SDXL draws pixels and cannot write readable labels or exact boxes and arrows (doc/ISSUES_LOG.md O1).
Here the chat model writes Mermaid text instead; the UI renders it as a sharp SVG with exact labels.

Small models often write labels Mermaid cannot parse (`A[Web (Nginx)]`), so `fix_mermaid` quotes
them in code, and `check_mermaid` catches what is still wrong for one retry.
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable
from typing import Any

from src.core.config import Config
from src.core.conversation import format_conversation_block, normalize_conversation

ProgressCallback = Callable[[dict[str, Any]], None]

DIAGRAM_SYSTEM = """You draw diagrams by writing Mermaid code (https://mermaid.js.org).

Pick the diagram type that fits the request:
- architecture, system design, network, schematic, data flow, process, steps → flowchart LR (or TD for top-down)
- things inside things / a hierarchy (school → classes → teachers → students, company → departments
  → teams, org chart, folder tree) → flowchart TD with an arrow from each parent to its own children
- who calls whom in order, request/response, login flow → sequenceDiagram
- database tables and relations → erDiagram
- classes and objects → classDiagram
- states and transitions → stateDiagram-v2
- brainstorming ideas around one topic → mindmap (children indented under their parent)
- project plan with dates → gantt; history or milestones → timeline

"Each X has Y" means every X gets its own Y: draw a small, complete example (e.g. 3 classes, each
with its own teacher, each teacher with 2 students), not one shared list.

Rules for flowcharts:
- Node ids are short letters/words without spaces (a1, svc). Put every label in double quotes:
  svc["Order Service (v2)"], store[("Orders store")], q{"Valid?"}
- Group related nodes with subgraph:  subgraph zone["Zone A"] ... end
  A subgraph is only a frame: never draw an arrow between a subgraph and a node inside it. If the
  group itself is a thing (the School), make it a normal node and draw arrows from it.
- Label arrows when it helps:  a1 -->|"request"| svc
- A decision node {"…?"} has labelled exits, e.g. -->|"Yes"| and -->|"No"|; each exit goes where
  that answer really leads (a failure never leads to success).
- Never use the word end as a node id. No HTML, no style lines, no comments.
- At most 30 nodes.
- Draw ONLY the parts the user names, with the names and technologies the user gives. Do not add
  products, databases, caches, apps or technologies the user did not mention. You may add the user
  or the internet as the starting point.
- Many identical items (10 PCs): draw one node labelled "10 × PC", not ten nodes.

The user may write in Hinglish or another language: diagram labels in English (keep technical terms).
Ignore picture-style words (isometric, blueprint, 3D, 8k, colours, background): every diagram uses
the app's standard look, so do not add styling or mention it in the notes.

Reply in exactly this format:
TITLE: <short title>
```mermaid
<the diagram>
```
NOTES:
- <2 to 4 short bullets: what the diagram shows and how the parts connect. No self-praise
  such as "correct", "precise" or "clean".>"""

REVIEW_SYSTEM = """You check a Mermaid diagram against the user's request before it is shown.

Look for:
1. parts, products or technologies in the diagram that the user did NOT ask for
2. parts the user asked for that are missing
3. wrong arrows: a step leading to the wrong next step, a failure path that ends in success,
   a decision {"…?"} whose Yes/No exits are missing or swapped
4. a diagram type that does not fit the request

5. a hierarchy drawn flat: every item attached directly to the top instead of parent → child → grandchild

If the diagram is correct, reply with exactly: OK
Otherwise reply with the corrected diagram in exactly this format, nothing else:
TITLE: <short title>
```mermaid
<the corrected diagram>
```
NOTES:
- <2 to 4 short bullets that explain the diagram to the user — NOT what you changed or fixed>"""

DIAGRAM_TYPES = (
    "flowchart", "graph", "sequenceDiagram", "classDiagram", "stateDiagram-v2", "stateDiagram",
    "erDiagram", "mindmap", "gantt", "timeline", "journey", "pie", "quadrantChart", "block-beta",
)
_BLOCK_RE = re.compile(r"```\s*mermaid\s*\n(.*?)```", re.S | re.I)
_TITLE_RE = re.compile(r"^\s*TITLE:\s*(.+)$", re.M)
_NOTES_RE = re.compile(r"NOTES:\s*\n(.*)$", re.S)
# a node id followed by a shape opener: web[ … ], db[( … )], q{ … }, r(( … ))
_NODE_START_RE = re.compile(r"(?:^|(?<=[\s>&|-]))([A-Za-z_][\w-]*)(\[\(|\(\(|\[\[|\{\{|\[|\(|\{)")
_OPEN_CLOSE = {"[(": ")]", "((": "))", "[[": "]]", "{{": "}}", "[": "]", "(": ")", "{": "}"}
# a label ends at the closer that is followed by the end of the node (space, arrow, &, :::, end of line)
_AFTER_NODE = re.compile(r"$|[\s&;]|-|=|\.|:::")
_EDGE_LABEL_RE = re.compile(r"\|(?P<label>[^\"|\n]+)\|")
_RISKY = re.compile(r"[()\[\]{}:;,/&<>#@'`]")


def extract(raw: str) -> tuple[str, str | None, str]:
    """(title, mermaid code or None, notes) from the model's reply."""
    block = _BLOCK_RE.search(raw or "")
    title = _TITLE_RE.search(raw or "")
    notes = _NOTES_RE.search(raw or "")
    return (
        title.group(1).strip() if title else "Diagram",
        block.group(1).strip() if block else None,
        notes.group(1).strip() if notes else "",
    )


def _is_flowchart(code: str) -> bool:
    first = _first_line(code)
    return first.startswith(("flowchart", "graph"))


def _first_line(code: str) -> str:
    for line in code.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("%%"):
            return stripped
    return ""


def _quote_nodes(line: str) -> str:
    """web[Web (Next.js)] → web["Web (Next.js)"]; already-quoted or plain labels stay as they are."""
    out, pos = [], 0
    while (m := _NODE_START_RE.search(line, pos)) is not None:
        opener = m.group(2)
        closer = _OPEN_CLOSE[opener]
        start = m.end()
        if line[start:start + 1] == '"':                 # already quoted
            out.append(line[pos:start])
            pos = start
            continue
        end = start
        while (end := line.find(closer, end)) != -1 and not _AFTER_NODE.match(line, end + len(closer)):
            end += 1                                      # a bracket inside the label: keep looking
        if end == -1:
            break
        label = line[start:end].strip()
        out.append(line[pos:start])
        out.append(f'"{label.replace(chr(34), chr(39))}"' if _RISKY.search(label) else line[start:end])
        out.append(closer)
        pos = end + len(closer)
    out.append(line[pos:])
    return "".join(out)


_STYLE_LINE_RE = re.compile(r"^\s*(style|linkStyle|classDef)\s", re.M)


def fix_mermaid(code: str) -> str:
    """Drop colour lines; quote flowchart labels that contain characters Mermaid cannot parse unquoted."""
    # colour lines: they clash with the diagram card, and in a mindmap every line is a node, so
    # "style School fill:#f9f9f9" was drawn as a box
    code = "\n".join(line for line in code.splitlines() if not _STYLE_LINE_RE.match(line))
    if not _is_flowchart(code):
        return code.strip()

    def edge(m: re.Match[str]) -> str:
        label = m.group("label").strip()
        return f'|"{label}"|' if _RISKY.search(label) else m.group(0)

    lines = []
    for line in code.splitlines():
        if line.strip().startswith(("subgraph", "%%", "class ", "click ")):
            lines.append(line)
            continue
        lines.append(_EDGE_LABEL_RE.sub(edge, _quote_nodes(line)))
    return "\n".join(lines).strip()


def check_mermaid(code: str | None) -> list[str]:
    """Problems that would stop Mermaid from rendering (empty list = looks fine)."""
    if not code:
        return ["no ```mermaid code block in the reply"]
    first = _first_line(code)
    if not first.startswith(DIAGRAM_TYPES):
        return [f"the first line must be a diagram type (flowchart LR, sequenceDiagram, …), not {first[:40]!r}"]
    problems: list[str] = []
    if first.startswith("mindmap"):
        indents = [len(line) - len(line.lstrip()) for line in code.splitlines()[1:] if line.strip()]
        if len(indents) > 6 and len(set(indents[1:])) == 1:
            problems.append("the mindmap is flat (every item directly under the root); draw the hierarchy "
                            "as flowchart TD with an arrow from each parent to its own children")
    if _is_flowchart(code):
        opened = sum(1 for line in code.splitlines() if line.strip().startswith("subgraph"))
        closed = sum(1 for line in code.splitlines() if line.strip() == "end")
        if opened != closed:
            problems.append(f"{opened} subgraph but {closed} end lines")
        if re.search(r"(^|[\s>-])end\s*(\[|\(|-->|---)", code, re.M):
            problems.append('"end" is used as a node id')
        for n, line in enumerate(code.splitlines(), 1):
            if line.count('"') % 2:
                problems.append(f"line {n} has an unclosed quote")
        problems.extend(_arrows_into_own_subgraph(code))
    return problems[:5]


_ARROW_SPLIT_RE = re.compile(r"\s*(?:<-->|-->|---|-\.->|-\.-|==>|<-\.->)\s*(?:\|[^|]*\|\s*)?")
_ID_RE = re.compile(r"[A-Za-z_][\w-]*")


def _arrows_into_own_subgraph(code: str) -> list[str]:
    """`school --> classA` where classA sits inside subgraph school: Mermaid draws stray arrowheads."""
    members: dict[str, set[str]] = {}
    stack: list[str] = []
    edges: list[tuple[str, str]] = []
    for line in code.splitlines():
        stripped = line.strip()
        if stripped.startswith("subgraph"):
            sg = _ID_RE.search(stripped[len("subgraph"):])
            stack.append(sg.group(0) if sg else "")
            members.setdefault(stack[-1], set())
            continue
        if stripped == "end":
            if stack:
                stack.pop()
            continue
        parts = [p for p in _ARROW_SPLIT_RE.split(stripped) if p]
        ids = [m.group(0) for p in parts if (m := _ID_RE.match(p.strip()))]
        for sg in stack:
            members[sg].update(ids)
        if len(parts) > 1:
            edges.extend(zip(ids, ids[1:]))
    problems = []
    for a, b in edges:
        for parent, child in ((a, b), (b, a)):
            if parent in members and child in members[parent] and child != parent:
                problems.append(f'arrow between subgraph "{parent}" and "{child}" inside it; make '
                                f'"{parent}" a normal node outside, or drop the arrow')
    return list(dict.fromkeys(problems))[:2]


def write_diagram(query: str, config: Config, conversation: list[dict[str, str]] | None = None,
                  chat: Callable[..., str] | None = None) -> tuple[str, str | None, str, list[str]]:
    """Ask the model for a diagram; fix and check it; retry once. → (title, code, notes, problems)."""
    if chat is None:
        from src.llm.llm_client import LLMClient

        chat = LLMClient(config).chat
    prior = format_conversation_block(normalize_conversation(conversation or []))
    user = f"{prior}\n\n## Request\n{query}" if prior else query
    raw = chat(system=DIAGRAM_SYSTEM, user=user, temperature=0.2)
    title, code, notes = extract(raw)
    code = fix_mermaid(code) if code else None
    problems = check_mermaid(code)
    if problems:
        retry = (f"{user}\n\n## Your previous diagram had problems\n- " + "\n- ".join(problems)
                 + f"\n\nPrevious code:\n```mermaid\n{code or ''}\n```\nWrite it again, corrected, in the same format.")
        title2, code2, notes2 = extract(chat(system=DIAGRAM_SYSTEM, user=retry, temperature=0.1))
        code2 = fix_mermaid(code2) if code2 else None
        problems2 = check_mermaid(code2)
        if len(problems2) < len(problems) or (code2 and not code):
            title, code, notes, problems = title2, code2, notes2 or notes, problems2
    if code and not problems:
        title, code, notes = review_diagram(query, title, code, notes, chat)
    return title, code, notes, problems


def review_diagram(query: str, title: str, code: str, notes: str,
                   chat: Callable[..., str]) -> tuple[str, str, str]:
    """Second look by the model: invented parts, missing parts, wrong arrows. A correction is only
    taken when it still passes check_mermaid, so the review can never break a working diagram."""
    user = f"## Request\n{query}\n\n## Diagram\n```mermaid\n{code}\n```"
    try:
        raw = chat(system=REVIEW_SYSTEM, user=user, temperature=0.1)
    except Exception:  # noqa: BLE001 — the first diagram is still good to show
        return title, code, notes
    if raw.strip().upper().startswith("OK"):
        return title, code, notes
    title2, code2, notes2 = extract(raw)
    if not code2:
        return title, code, notes
    code2 = fix_mermaid(code2)
    if check_mermaid(code2):
        return title, code, notes
    return (title2 if title2 != "Diagram" else title), code2, notes2 or notes


# ── Network diagram (HLD) → a designed netdiagram (NetworkDiagram.tsx), not Mermaid ─────────────────
_NUM_SITES_RE = re.compile(r"\b\d{1,3}\s*(?:sites?|branch(?:es)?|offices?|locations?|stores?)\b", re.I)
_SITES_RE = re.compile(r"\b(?:sites?|branch(?:es)?|offices?|locations?|stores?)\b", re.I)
_HUB_RE = re.compile(r"\b(?:hq|h\.?o\.?|head\s*office|headquarters?|data\s*cent(?:er|re)|main\s*office)\b", re.I)
_LLD_RE = re.compile(r"\b(?:lld|low[\s-]*level|ip\s*(?:address\w*|plan|scheme)|subnet\w*|vlan)\b", re.I)
# a network diagram/topology in general (campus, LAN, DMZ, data-centre, office/home network, …)
_NETDIAG_RE = re.compile(
    r"network\s*(?:diagram|topology|map|design|layout|architecture)|\btopolog|\bLAN\b|\bWAN\b|\bDMZ\b|"
    r"(?:campus|office|home|enterprise|data\s*cent(?:er|re)|branch)\s*network",
    re.I,
)


def _is_network_diagram(query: str) -> bool:
    """A network diagram / topology (HLD) — drawn as the designed netdiagram, not Mermaid. Covers multi-site
    hub-and-spoke AND single-site topologies (campus core/dist/access, DMZ, data-centre). A low-level (LLD)
    ask (VLAN/IP plan) is NOT this: the chat path answers that with tables."""
    q = (query or "")
    if _LLD_RE.search(q):
        return False
    if _NETDIAG_RE.search(q):
        return True
    ql = q.lower()
    multi = bool(_NUM_SITES_RE.search(q)) or (bool(_SITES_RE.search(q)) and ("connect" in ql or bool(_HUB_RE.search(q))))
    return multi and ("network" in ql or "connect" in ql or bool(_HUB_RE.search(q)))


NETDIAGRAM_GEN_SYSTEM = """Output ONLY a JSON object describing a multi-site network, for a diagram. No
prose, no markdown, no code fences, no comments — just the JSON object. Use this exact shape:
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
List EVERY site the user mentions (10 sites -> 10 entries in "sites"). Always include "siteTemplate" — a
typical branch site's internal LAN as a left-to-right device chain (usually router -> switch -> PCs, add
printer/AP/server if relevant). It is drawn INSIDE every site card, linked and labelled, so the diagram shows
HOW each site connects to its own devices. If one site differs, give just that site its own "devices" list:
{"label": "Site 3", "sub": "Branch Office", "devices": ["router", "switch", "Wi-Fi AP", "IP camera", "VoIP phone"]}
(at most 5 devices).
Device/icon names ("icon" on HQ nodes, "devices" lists) are PLAIN ENGLISH — use the real device, whatever it
is: router, switch, firewall, load balancer, server, NAS, database, Wi-Fi AP, IP camera, VoIP phone, DNS
server, PCs, laptop, printer, IoT sensor, UPS, cloud, … — any device is drawn with a fitting icon, so never
force it into a short wrong word.

Use the shape ABOVE (sites + hq) ONLY for several sites/branches connecting to a head office. For ANY OTHER
network topology — a single-site campus (core / distribution / access), a DMZ, a data-centre, a home or
office LAN — use a GRAPH instead, laid out in tiers top-to-bottom:
{"title": "Campus Network", "subtitle": "Core / distribution / access",
 "nodes": [{"id": "net", "icon": "internet", "label": "Internet", "tier": 0},
   {"id": "fw", "icon": "firewall", "label": "Edge Firewall", "tier": 1},
   {"id": "core", "icon": "core switch", "label": "Core Switch", "tier": 2},
   {"id": "acc1", "icon": "access switch", "label": "Access Switch", "tier": 3},
   {"id": "pcs", "icon": "PCs", "label": "Workstations", "tier": 4}],
 "links": [{"from": "net", "to": "fw", "kind": "internet"}, {"from": "fw", "to": "core", "kind": "wan"},
   {"from": "core", "to": "acc1", "kind": "lan"}, {"from": "acc1", "to": "pcs", "kind": "lan"}],
 "groups": [{"label": "Core Layer", "nodes": ["core"]}],
 "legend": [...], "features": [...], "notes": [...]}
Every node has a unique "id", an "icon" (plain-English device) and a "tier" (0 at the top, increasing down).
"links" connect node ids; "kind" is one of lan, wan, vpn, wireless, internet. "groups" (optional) draw a
labelled zone around the listed node ids. Give at most 40 nodes.

Double quotes only, no trailing commas. JSON only."""


def _extract_json_object(raw: str) -> str | None:
    m = re.search(r"\{.*\}", raw or "", re.S)
    return m.group(0) if m else None


def generate_netdiagram(query: str, chat: Callable[..., str],
                        conversation: list[dict[str, str]] | None = None) -> str | None:
    """Dedicated call: a multi-site network as the netdiagram JSON (rendered by NetworkDiagram.tsx). The 9B
    won't reliably emit a netdiagram block inline, so we ask for ONLY JSON and validate it here. Returns a
    compact JSON string, or None when the reply isn't usable (caller then falls back to Mermaid)."""
    prior = format_conversation_block(normalize_conversation(conversation or []))
    user = f"{prior}\n\n## Request\n{query}" if prior else query
    raw = chat(system=NETDIAGRAM_GEN_SYSTEM, user=user, temperature=0.1)  # LLMStopped handled by caller
    js = _extract_json_object(raw)
    if not js:
        return None
    try:
        data = json.loads(js)
    except (ValueError, TypeError):
        return None
    if not isinstance(data, dict):
        return None
    # accept EITHER shape: hub-spoke (sites + hq.nodes) or a generic topology graph (nodes + links)
    sites, hq = data.get("sites"), data.get("hq")
    hubspoke = isinstance(sites, list) and sites and isinstance(hq, dict) and isinstance(hq.get("nodes"), list) and hq["nodes"]
    graph = isinstance(data.get("nodes"), list) and len(data["nodes"]) >= 2 and isinstance(data.get("links"), list)
    if not (hubspoke or graph):
        return None
    return json.dumps(data, ensure_ascii=False)


def _emit(cb: ProgressCallback | None, event: dict[str, Any]) -> None:
    if cb:
        cb(event)


def run_diagram(
    query: str,
    *,
    job_id: str,
    config: Config | None = None,
    on_progress: ProgressCallback | None = None,
    conversation: list[dict[str, str]] | None = None,
    **_: object,
) -> tuple[str, list[str], float]:
    """Return (markdown reply with a ```mermaid block, errors, seconds)."""
    config = config or Config.from_env()
    started = time.perf_counter()
    errors: list[str] = []
    agent = {"agent_id": "assistant", "name": "Assistant", "role": "Diagram", "stage": "chat",
             "accent": "#5a8dc4", "job_id": job_id}
    _emit(on_progress, {"type": "session_start", "mode": "chat", "query": query})
    _emit(on_progress, {"type": "agent_start", **agent, "model": config.llm_model})
    _emit(on_progress, {"type": "status", "message": "Drawing the diagram…"})
    from functools import partial

    from src.jobs import live
    from src.llm.llm_client import LLMClient, LLMStopped

    # Stop works here too: every model call (draft, retry, review) ends at its next word
    chat = partial(LLMClient(config).chat, should_continue=lambda: not live.is_stopped(job_id))
    try:
        # A multi-site network overview renders far better as the designed netdiagram infographic than as a
        # sprawling Mermaid flowchart. Dedicated JSON-only call, validated; on any failure fall back to Mermaid.
        spec = None
        if _is_network_diagram(query):
            _emit(on_progress, {"type": "status", "message": "Designing the network diagram…"})
            try:
                spec = generate_netdiagram(query, chat, conversation=conversation)
            except LLMStopped:
                raise
            except Exception:  # noqa: BLE001 — fall back to a Mermaid diagram below
                spec = None
        if spec:
            output = f"**Network Diagram**\n\n```netdiagram\n{spec}\n```"
        else:
            title, code, notes, problems = write_diagram(query, config, conversation, chat=chat)
            if code is None:
                errors.append("the model did not return a diagram")
                output = "I could not draw this diagram. Please describe the parts and how they connect, and try again."
            else:
                output = f"**{title}**\n\n```mermaid\n{code}\n```"
                if notes:
                    output += f"\n\n{notes}"
                if problems:
                    output += ("\n\n_The diagram may not render: " + "; ".join(problems)
                               + ". Ask me to fix it, or copy the code into https://mermaid.live._")
    except LLMStopped:
        output = "_Stopped before the diagram was finished._"
        agent["role"] = "Stopped"
    except Exception as exc:  # noqa: BLE001 — report instead of failing the job silently
        errors.append(str(exc))
        output = f"Diagram failed: {exc}"
    elapsed = time.perf_counter() - started
    _emit(on_progress, {"type": "agent_done", **agent, "elapsed_seconds": elapsed, "output": output})
    _emit(on_progress, {"type": "session_done", "total_seconds": elapsed, "errors": errors, "mode": "chat"})
    return output, errors, elapsed
