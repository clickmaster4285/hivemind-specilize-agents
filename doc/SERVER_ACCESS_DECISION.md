# Logging in to servers and doing work (SSH, RDP)

**Status:** to discuss → decide (2026-10-21).
**Related:** `src/server_vault.py` (session logins), `src/linux_audit.py` (read-only SSH), `doc/SECURITY_TOOLS_DECISION.md`.

---

## 1. The need

You administer your own servers and PCs. You want to log in and **do work** — run commands on a Linux
server, and reach a Windows machine over RDP. Today the tool only *audits* a Linux server (read-only).
This document is how we add "doing work" without making the system dangerous or leaky.

## 2. The rules that stay

- **Logins are never typed in the chat.** The chat is saved and sent to the model. Logins go in
  **Settings → Servers**, kept in the session's memory only (`src/server_vault.py`): never on disk, in
  the database, a log, or the model.
- **Your own machines only** (private network, or servers you add yourself).
- **Nothing the model decides on its own changes a machine.** A change happens only when *you* confirm it.

## 3. Three separate things — each needs its own design

| Need | What it is | Hard part |
|---|---|---|
| A. Run a **read-only** command on a Linux server | "show me disk usage on web01" | already safe — just more commands |
| B. **Make a change** on a Linux server | "restart nginx on web01", "add a firewall rule" | a change must be shown and confirmed first |
| C. **RDP** to a Windows machine | open its desktop and work by hand | the web app cannot *be* an RDP client |

---

## 4. A — Read-only commands (small step)

The audit already logs in and runs a fixed, read-only list. We add a **"look" tool**: the IT specialist
may run read-only commands from an allow-list (disk, memory, services status, logs, process list,
`systemctl status`, `df`, `ls`, `cat` of config files). No command that writes, deletes, installs or
restarts. Same session login, same SSH connection code.

- **Safety:** a fixed allow-list of read-only commands; anything not on it is refused.
- **Effort:** small — it reuses the audit's SSH layer.

## 5. B — Making changes (the careful one)

This is where a mistake, or a bad instruction hidden in a web page, could break a server. So:

1. The assistant **proposes** the exact command(s) — it never runs them from its own decision.
2. You see them in a **confirm box**: "Run `systemctl restart nginx` on web01? [Run] [Cancel]".
3. Only your click runs it. Every run is written to an **audit log** (who, when, which server, which
   command, the output) — this log is on the server side, for review.
4. **Admin only.** A per-server switch: "this server is read-only" vs "changes allowed (with confirm)".
5. Optional guard rails: a **deny-list** of obviously destructive commands (`rm -rf /`, `mkfs`,
   `dd`, `shutdown`, user/password changes) that are refused even with confirm, unless an admin turns
   that off for one command.

- **Safety:** nothing runs without a human click; full audit trail; destructive commands blocked by default.
- **Effort:** medium — the confirm step is a new event type the chat waits on, plus the audit log table.

## 6. C — RDP (a different shape of problem)

RDP is a live screen you operate by hand. A chat assistant cannot type into a Windows desktop for you,
and the web app is not an RDP client. Three honest options:

| Option | How it works | Trade-off |
|---|---|---|
| **C1. Launch file** | The tool checks the host is up (port 3389) and gives you a ready `.rdp` file / a `mstsc` line to open in **your own** Remote Desktop app. You log in there. | Simplest. You still use the normal Windows RDP window — nothing new to trust. The app never handles the RDP password. |
| **C2. Browser gateway (Guacamole)** | Run **Apache Guacamole** (open-source, Apache-2.0) in Docker next to MulteAgent. It gives a real RDP/SSH desktop **inside the browser**, which you operate by hand. MulteAgent links to it. | A full remote desktop in the browser, with its own session recording. More to run (one more container) and to secure. The login lives in Guacamole, not in the chat. |
| **C3. Nothing** | Keep today's behaviour: the tool pings the host and says "RDP is open on 3389, connect with your own client." | No new surface at all. |

RDP is for sitting at the machine yourself; the assistant's job is to get you there (host is up, port
open, launch file ready), not to drive the desktop.

---

## 7. Recommendation

1. **A now** — read-only commands over the existing SSH. Low risk, immediately useful.
2. **C1 now** — a ping + `.rdp` launch file, so "log in to tomy rdp" gives you a working connection in
   your own RDP client instead of a refusal.
3. **B next** — run-with-confirm + audit log, admin only, per-server opt-in. This is the one that needs
   care, so it gets its own build and its own testing.
4. **C2 later, only if you want a full browser desktop** — add Guacamole as a separate service.

## 8. How the agent actually does the work (the real use cases)

Two kinds of request, both handled by one **remote-run tool** plus the existing agentic loop
(`src/agentic/`). The tool is `run_on_server(server, command)` — it uses the session login, runs the
command over SSH, and streams the output back to the agent and the chat.

### Case 1 — "log in to web01 and run this file / this command" (developer, explicit)

You already said exactly what to run. The flow:

1. The agent picks the server (from the ones you added) and the command **you gave**.
2. It runs it over SSH and streams stdout/stderr into the chat, live.
3. The exit code and output come back. Done in one step — no guessing.

Because *you* named the command, this is a direct instruction, not the model inventing one. It still goes
through the trust gate below (a write command on a confirm-mode server shows the confirm box first).

### Case 2 — "log in to web01 and check for this issue" (IT, diagnose)

Here the agent has to investigate: run a read-only command, read the output, decide the next one, repeat,
then report the cause (and propose a fix you can approve). The flow:

1. The agentic loop calls `run_on_server` with a **read-only** command (e.g. `systemctl status nginx`).
2. It reads the output, decides the next command (`journalctl -u nginx --since -1h`, `df -h`, …).
3. It loops until it can explain the issue, then writes a diagnosis + a suggested fix.
4. The fix is a **proposed change** → it goes through the confirm box (section 5) before anything runs.

Diagnosing is safe to let the agent do freely, because every command in the loop is read-only. Only the
*fix* needs your click.

### The trust gate (one place, both cases)

Each server you add has a mode (set in Settings → Servers):

| Mode | Read-only commands | Commands that change the server |
|---|---|---|
| **Audit** (today's default) | agent runs them freely | refused |
| **Operate — confirm** | agent runs them freely | **shown, you click [Run]**, then it runs; logged |
| **Operate — trusted** | agent runs them freely | agent runs them and logs them (no click) — for a lab server you accept the risk on |

- Every command, in every mode, is written to the **audit log** (server, command, output, who, when).
- Obviously destructive commands (`rm -rf /`, `mkfs`, `shutdown`, user/password changes) are refused even
  in "trusted" mode unless you clear that one command.

### Who may operate (DECIDED 2026-10-21)

**Authorization = holding working credentials.** Whoever can add a login for a server (its username +
password or key, in Settings → Servers) may operate that server. There is **no separate MulteAgent
admin-only gate** on top — we are not building that.

The real limit is the **server's own account**: if that SSH user has no `sudo`, the agent cannot change
anything there either; it can only do what that account can do. So you grant access per server by choosing
which OS account's credentials you put in — a read-only account for audit, a fuller account to operate.

(The local-network tools that need **no** credential — `network_scan`, `security_check` of the LAN — are
a different case and stay as they are; this decision is about logging in to a named server.)

### Windows (RDP machines)

RDP itself stays as section 6 (a launch file, or Guacamole) — it is a hand-operated desktop. To let the
agent *run commands* on a **Windows** server the way it does on Linux, we add **WinRM** (PowerShell
remoting) as a second connector; the same remote-run tool, trust gate and audit log apply. "Run this file
on the Windows box" then works like Case 1.

---

## 9. Recommended build order

1. ✅ **`diagnose_server` read-only** + the agentic diagnose loop (Case 2, Audit mode) — **built 2026-10-21**.
2. ✅ **Trust gate + confirm box + audit log** (`operate_server`) — **built 2026-10-21**.
3. **WinRM connector** → the same for Windows servers.
4. **RDP**: C1 launch file now; Guacamole (C2) later if you want the browser desktop.

### Built 2026-10-21 — `diagnose_server` (step 1)

`src/server_ops.py`, tool `diagnose_server` (IT specialist). The chat model logs in to a server added in
Settings → Servers and runs its **own** read-only commands in a loop (native tool calling): it reads each
output, decides the next command, and stops when it can explain the issue — then reports the cause and the
fix (as commands for the user to run; it does not run them).

**The safety gate is deterministic, not the model.** `classify_command()` decides read-only vs change:
a strict allow-list of read-only programs, per-program sub-action lists (`systemctl status|show|…`, not
`restart|stop`), write-flag blocks (`sed -i`, `find -exec`, `iptables -A`, `ip … add`), and handling of
pipes, `;`/`&&`/`||`, command substitution and `>` redirection. Anything not understood is refused. A
command the model proposes that would change the server is refused and fed back, so the model continues
read-only. Tested against 26 read-only and 38 changing/unknown commands (`tests/test_server_ops.py`).

Login is credential-gated per §8 (anyone who added the server login). Max 12 commands per question; Stop
honoured. **Not yet real-world tested** — the user runs the first live diagnosis.

### Built 2026-10-21 — `operate_server` (step 2: changes, confirm, audit log)

`src/server_ops.py` (`operate()` / `run_server_operate`), tool `operate_server`. The agent may now change a
server; each changing command passes a gate before it runs:

1. **Deny-list** (`is_destructive`): `rm -rf /`, `mkfs`, `dd of=/dev/…`, `reboot`/`shutdown`, fork bomb,
   `passwd root`, `chmod -R 000 /` … are refused **even in trusted mode and even if confirmed**.
2. **Server mode** (`src/server_vault.py`, set in Settings → Servers, per server):
   - **audit** — every change refused (read-only; diagnose only).
   - **confirm** — a `confirm_command` event → the chat shows the command with **Run / Cancel**; the worker
     blocks on `live.wait_confirm` (5-min timeout) until the user clicks; then it runs or is skipped.
   - **trusted** — changes run automatically (deny-list still applies).
3. **Audit log** (`src/command_audit.py` → `app.command_log`): every executed, confirmed, cancelled or
   refused change is recorded (user, job, server, host, command, decision, exit code, output) — never the
   password. Read-only commands are not logged.

Round-trip pieces: `live.py` (`wait_confirm`/`answer_confirm`), `POST /api/jobs/{id}/confirm`,
`PUT /api/servers/{id}/mode`, the `ConfirmCard` component. Tested with a fake SSH + fake model across
audit / confirm / cancel / trusted / deny paths and the confirm round-trip (`tests/test_server_ops.py`).
Only the in-process worker (`main.py --gui`) can serve confirmations. Credential-gated per §8.
**Not yet real-world tested.**

This completes **P0–P2** of `doc/requirment.md` (validate audit → read-only diagnosis → approval + audit).

## 10. Questions

1. **Build order above** — does it match what you need first? (I'd start with the read-only diagnose loop.)
2. **Trust mode:** is per-server Audit / Operate-confirm / Operate-trusted the right control, or simpler?
3. **RDP:** launch file (C1) enough, or the browser desktop (C2, Guacamole)?
4. **Windows command-running (WinRM):** needed, or is RDP enough for the Windows machines?
5. ~~Who may operate~~ — **decided: whoever holds the credentials; the server's own account is the limit.**
