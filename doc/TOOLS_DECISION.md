# Can the assistant *do* things? — execution tool decision

**Status:** ✅ **decided 2026-10-17 — Option A built** (`src/net_scan.py`); more networking/security
tools to follow the same pattern · written 2026-10-17
**Trigger:** "i need to check all the ip nad at the end i only the result in table format" → the
assistant answered with **instructions** (script, Angry IP Scanner, `arp -a`), not with a result.
**Recommendation:** build the narrow **`network_scan` tool** (Option A) — ~half a session, safe by
construction, doubles as the first L2 (IT specialist) tool.

Related: [ISSUES_LOG.md](ISSUES_LOG.md) (O7) · [FULL_PROJECT_PLAN.md](FULL_PROJECT_PLAN.md) §10
(why v1 has no shell tool) · [BACKLOG.md](BACKLOG.md) (L2 specialists)

---

## 1. The issue

### What we saw (screenshot, 2026-10-17)

The user asked for a **result** — check every IP on the LAN, end with a table. The answer (Web
search, 73.7 s) was a good *guide*: three options (their own `test.py`, Angry IP Scanner, `arp -a`),
steps, and 2 cited sources. But it could only **advise**. Nothing was scanned, no table exists, and
the user would have to run the script and paste results back — then ask again to format them.

### Why — three separate facts

| # | Fact | Evidence |
|---|------|----------|
| 1 | **The app has no way to execute anything.** There is no tool that runs code or commands. | Deliberate, locked decision: FULL_PROJECT_PLAN §10 — *"Why no shell tool in v1: shared team server + unrestricted shell = high risk. Add later only with hard sandboxing."* |
| 2 | **Web search can only read, never act.** It fetches pages and writes an answer from them (`src/web_search.py`). Correct behaviour, wrong tool for "do it for me". | The screenshot's answer: instructions + links |
| 3 | **The message doesn't even route to a useful intent.** Classified `diagram` (0.678, nearest = *"show the network topology of a small office"*), because no intent describes "run a local scan". | `python -m src.llm.semantic_router "i need to check all the ip nad at the end i only the result in table format"` — it only reached Web search because the **Web** button was pressed (`force_intent`) |

### What this really is

Not a router bug and not a search bug — it is a **missing capability**: the loop
*ask → act → return structured result* cannot close. Every task that needs the machine to *do*
something ends in manual steps.

---

## 2. What exists today (the tool layer is built and waiting)

Agentic mode already has an allowlisted tool system — adding a tool touches exactly three places:

| Touch point | File | What it holds |
|---|---|---|
| Planner menu (the model sees this list) | `src/agentic/prompts.py` (L23–34) | `read_repo_file`, `write_xlsx`, `http_get`, `list_models`, `search_logs`, … |
| Dispatch (arg validation + execution) | `src/agentic/tools.py` → `run_tool()` if-chain | one branch per tool |
| Allowlist (which run may use it) | `src/agentic/runtime.py` (L198) | tool names per run |
| Router teaching | `data/router/intents.yaml` | one example line = right routing, no code |

Already useful for "table format": **`write_xlsx`** produces a downloadable `.xlsx`, and markdown
tables render in the chat. The only missing piece is **getting the data in**.

---

## 3. The three options

### Option C — No tool (status quo)

The assistant knows, you act: it writes the script, you run it, you paste results back.

- **Usage cases:** guides and how-to ("how do I sort IPs in Excel"), one-off personal scripts,
  questions where the answer itself is the deliverable.
- **Pros:** zero build, zero risk, nothing can go wrong on a shared server.
- **Cons:** the loop never closes. Ask → copy → run → paste → ask again to format. The app is an
  advisor, never an operator.
- **Verdict:** fine for questions; can never satisfy "just do it for me".

### Option A — Narrow `network_scan` tool (recommended)

One purpose-built tool in the existing agentic layer:

```text
network_scan {"subnet": "192.168.88.0/24"}      (or no args = auto-detect the server's subnet)
    → parallel ping sweep + TCP probe fallback + reverse DNS / NetBIOS names
    → [{ip, status, name, open_ports}, …]
    → worker prints a markdown table · write_xlsx → downloadable .xlsx
```

**Usage cases (an IT team's day):**
- "Check all the IPs and give me the table" ← the triggering message
- "Find the new camera's IP" · "Is the NAS and printer up?"
- DHCP audit: unknown devices on the office LAN
- First tool of the **L2 IT specialist** — specialists = instructions + own tools + own collections

**Safety — safe by construction, not by policy:**

| Guard | Effect |
|---|---|
| Private ranges only (RFC1918); refuse public IPs | Cannot be aimed at outside targets |
| Max `/22` (~1024 hosts), hard ~60 s timeout, Stop honoured | Cannot flood the network |
| Fixed probe list, `ping` as an argument array (never `shell=True`), read-only | No code execution at all |
| `NETWORK_SCAN_ENABLED=false` kill-switch, every run in the job log | Off switch per deployment |

**Key property:** even if a retrieved page or brain passage tries prompt injection
(ISSUES_LOG #31 lesson: *treat model output like user input*), the tool **physically cannot** do
anything except a small scan of your own private network. Blast radius ≈ zero.

**Cons:** one-trick — each new capability needs another tool (the treadmill). Names are best-effort
(shy IoT boxes often show IP only). **The scan runs where the server runs**: it sees the *server's*
LAN, not a remote teammate's home network.

**Effort:** ~half a session (module + 3 touch points + 1 router example + tests). **Risk:** low.

### Option B — Sandboxed code runner

`run_python {"code": "..."}` — the model writes scripts like `test.py` and runs them in a restricted
container: pinned Python image, read-only filesystem, memory/CPU/pids caps, timeout + kill, output
size cap, no secrets in env, job temp dir only, egress locked down, per-user quotas.

- **Usage cases:** the scanner *and every variation* (ports, filters, formats) — no tool treadmill;
  one-off automation: parse logs, wrangle CSVs, batch work in `artifacts/`, Excel math, ping X.
- **Pros:** general; covers Option A completely; the biggest leap in real "agentic" capability.

**The honest cost:**
1. **It is your own deferred decision** — FULL_PROJECT_PLAN §10. This *is* the "later, with hard
   sandboxing" project.
2. **The injection problem changes class.** With A, injected text can only trigger a bounded scan.
   With B, a malicious passage → arbitrary code on the team server: read `.env` (JWT secret, DB
   passwords), scan the public internet from your IP, fork-bombs, mining. The sandbox is the *only*
   wall between untrusted web text and execution — and sandboxes leak.
3. **Engineering that never ends:** image pinning/patching, caps, egress policy, quotas for 8–15
   users, a run-confirmation gate for anything influenced by retrieved content.
4. **Reliability:** model-written code fails often; users will file bugs against *your* runner for
   *its* scripts.
5. Windows/Docker specifics: feasible (Docker is there), but a real build + permanent upkeep.

**Effort:** several sessions + ongoing. **Risk:** high-consequence.

---

## 4. Side by side

| | **C — no tool** | **A — network_scan** | **B — code runner** |
|---|---|---|---|
| "Do it for me" | ❌ never | ✅ for LAN scans | ✅ for anything |
| Build cost | 0 | ~half a session | several sessions + upkeep |
| Security risk | none | low (safe by construction) | high (sandbox is the only wall) |
| Injection-proof | n/a | yes (bounded actions) | only as good as the sandbox |
| Generality | — | one task | unlimited |
| Team-server friendly | yes | yes | must be engineered for 8–15 users |
| Roadmap fit | — | first **L2** tool | its own project, deferred by plan |
| When it breaks | — | ~300 lines of yours | the model's script inside a container |

---

## 5. Recommendation

**Build Option A now.**

1. It closes the exact triggering case end-to-end **this week**: ask → scan → table in chat →
   downloadable `.xlsx`.
2. Everything it uses already exists (agentic tools, `write_xlsx`, `intents.yaml`, eval) — it is an
   addition, not a redesign.
3. It is **not throwaway**: the tool menu, router teaching and tests transfer 1:1 to Option B if it
   ever happens, and it is literally the first brick of **L2 (IT specialist)**.
4. Option B should wait for a *second, unrelated* "run this for me" use case — and then start with a
   sandbox design doc + confirmation gate, not code.

---

## 6. If we say yes — the build plan (Option A)

1. **`src/net_scan.py`** — RFC1918 validation, host cap, ThreadPool sweep
   (subprocess `ping -n 1 -w 400` arg-array; TCP probe fallback on a fixed port list;
   `gethostbyaddr` + `nbtstat -A` for names), hard timeout, `should_continue` honoured.
2. **`src/agentic/tools.py`** — `network_scan` branch → rows (cap output size).
3. **`src/agentic/prompts.py`** — menu line: `- network_scan {"subnet":"192.168.1.0/24"}`
   (omit subnet = auto-detect).
4. **`src/agentic/runtime.py`** — add to the allowlist.
5. **`data/router/intents.yaml`** — example under `agentic`: *"check all the IPs on my network and
   show the results in a table"* (+ one Hinglish), **`data/router/eval.yaml`** — matching cases;
   re-run `python -m src.llm.semantic_router --eval` (must stay 0 wrong).
6. **Config** — `NETWORK_SCAN_ENABLED` (default true), `NETWORK_SCAN_MAX_HOSTS` (1024).
7. **Tests** — private-range refusal, `/16` refusal, cap, timeout, table/xlsx output shape
   (mocked ping; no real network in unit tests).
8. **Docs** — ISSUES_LOG O7 → solved, BACKLOG progress row.

**Decision:** A now · B later (with a design doc) · C as the fallback. Record the pick here and in
[BACKLOG.md](BACKLOG.md) when made.

## 7. Built (2026-10-17)

- **Two ways in, one core:** a direct route (`network_scan` intent → `run_network_scan`, no LLM
  planning — exact table) **and** an agentic tool (`network_scan` on the worker's menu) for multi-step tasks.
- Guards as designed: RFC1918 only, ≤ `NETWORK_SCAN_MAX_HOSTS` (1024, hard ceiling 4096), 60 s,
  Stop, `NETWORK_SCAN_ENABLED`, fixed `ping`/`arp` argument lists, fixed port list.
- Output: summary + markdown table (IP, name, MAC, open ports with service names, found by) +
  `network-scan-<subnet>.xlsx` (sheet "Devices" + sheet "Scan").
- Real run: the triggering message → `network_scan` (similarity 0.92) → 192.168.88.0/24, 254 addresses,
  **25 devices in 9.6 s**. Router eval 55/61 decided, 6 to the LLM, **0 wrong** (61 cases, incl.
  "how does an IP scanner work" → stays a normal answer). 18 tests (`tests/test_net_scan.py`).

**Compared with Advanced IP Scanner (same LAN, 2026-10-17):** first version 26 devices vs 37 — the 11
missing were firewalled Dell/HP PCs that drop ping. Now: devices that only answer **ARP** count too (the
sweep's own pings fill the ARP table), names via **NetBIOS** (like Windows), and the **manufacturer** from
the MAC (IEEE OUI list, downloaded once to `data/net/oui.csv`). Result: **37/37 devices**, manufacturer for
all 37 (cameras identified as Hikvision, where Advanced IP Scanner shows nothing), ~29 s.

**Pattern for the next tools** (networking, security, …): one module with guards built in →
direct intent in `data/router/intents.yaml` + eval cases → optional agentic menu line → tests.
