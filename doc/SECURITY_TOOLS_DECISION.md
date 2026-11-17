# Security tools — which machines should they look at?

**Status:** decided 2026-10-19 — **D + A first, then B** (the user has Linux servers). First tool built: see §8.
**Related:** [TOOLS_DECISION.md](TOOLS_DECISION.md) (how `network_scan` was decided and built), BACKLOG L2 (IT specialist).

---

## 1. The question

You asked for these IT / security tools: IoT devices, firewall monitoring, server security (open ports,
ufw, firewall …), attack prevention, high- and low-level network diagrams, vulnerability finding,
testing and profiling.

Every one of them needs **information from somewhere**. There are four places it can come from, and
they are very different in what they can see, what they need from you, and what could go wrong:

| Option | Where the information comes from | Needs from you |
|---|---|---|
| **A. This Windows PC** | The computer MulteAgent runs on — asks Windows directly | Nothing |
| **B. Linux servers via SSH** | Logs in to each Linux server and runs read-only commands | One SSH login (key) per server |
| **C. Network devices** | Asks the router / firewall box itself through its API | An API key per device, and which brand |
| **D. Only from outside** | Knocks on ports from this PC, like any device on the network could | Nothing |

They are **not exclusive** — they are layers. The decision is which layers to build, and in which order.

---

## 2. Safety rules (the same for every option)

These come from how `network_scan` was built and stay for every new tool:

- **Read-only.** The tools look; they never change a setting, close a port, add a firewall rule or
  restart anything. Any fix is *advice* the assistant writes; you run it yourself.
- **Your own network only.** Private addresses (10.x, 172.16–31.x, 192.168.x) or machines you list
  yourself in `.env`. No internet targets.
- **No attacks.** No exploits, no password guessing, no flooding. "Vulnerability testing" here means:
  read the version of a service and compare it to a list of known problems (CVEs). That is how
  professional scanners work in their "safe" mode.
- **Admin only** for active scans and for anything that uses a stored login.
- **Hard limits:** time limit, maximum number of hosts, Stop button, an off switch in `.env`.
- **Passwords and keys** only in `.env` / a key file, never in the chat, never in logs, never sent to a model.

---

## 3. The options in detail

### Option A — This Windows PC (the MulteAgent computer)

**What it is:** the tool asks Windows itself — no network traffic, no login. Checked today on this PC
(Windows 11 Pro), without administrator rights:

| What it can read | Checked here | Used by tool |
|---|---|---|
| Ports this PC listens on, and which program owns each | ✅ 35 listening ports | 1 Ports & server security |
| Windows Firewall: profiles on/off, every rule (allow/block, port, program) | ✅ 3 profiles on, 360 rules | 2 Firewall status |
| Remote Desktop (RDP) on or off | ✅ off | 1, 7 |
| SMB version 1 (old, unsafe file sharing) on or off | ✅ off | 1, 7 |
| Security log: failed logins (event 4625), new accounts, lockouts | ✅ readable here* | 6 Attack monitoring |
| Firewall log (blocked connections) | ⚠ off by default in Windows; turning it on needs admin | 6 |
| Windows Defender status, missing updates | ⚠ needs admin | 7 Security report |

\* readable with the rights this account has; on another PC it may need the API to run as administrator.

**Benefits**
- Works **today, with zero setup** — nothing to install, no passwords.
- The most **exact** information about this machine: real firewall rules, not guesses from outside.
- **Safest**: no traffic on the network, no stored login.
- Attack monitoring (failed logins, brute force) is real here — the logs are on this machine.

**Limits**
- Sees **one machine only** — the one MulteAgent runs on. Your other servers stay invisible.
- No ufw / iptables — those exist only on Linux (Option B).

**Effort:** small. **Risk:** very low.

---

### Option B — Linux servers via SSH

**What it is:** MulteAgent logs in to each Linux server with its own SSH key, runs a fixed list of
read-only commands, and logs out. This is where **ufw** and **iptables** live.

| What it can read | Command (read-only) | Used by tool |
|---|---|---|
| Listening ports and their programs | `ss -tulpn` | 1 |
| ufw rules and default policy | `ufw status verbose` | 2 |
| iptables / nftables rules | `iptables -S`, `nft list ruleset` | 2 |
| SSH server settings (root login allowed? passwords allowed?) | `sshd -T` | 1, 7 |
| OS version, missing security updates | `/etc/os-release`, `apt list --upgradable` / `dnf check-update` | 5, 7 |
| Failed logins, brute-force attempts | `journalctl -u ssh`, `/var/log/auth.log`, `lastb` | 6 |
| fail2ban status (attack prevention already running?) | `fail2ban-client status` | 6 |

Several of these (ufw, iptables, sshd -T, auth logs) need root. The safe way: a dedicated account,
e.g. `multeagent-audit`, with a **sudo allow-list of exactly these commands** and nothing else —
it cannot change anything even if the key leaked.

**Benefits**
- The only way to see **ufw / iptables** and the server's **own view** — including services that the
  firewall hides from the outside (Option D cannot see those).
- Patch level and SSH settings — the two most common causes of real server break-ins.
- Real attack monitoring for Linux (failed SSH logins, fail2ban bans).

**Limits / costs**
- **Setup per server:** create the audit account, add the sudo allow-list, put the public key on it,
  add the server to `.env`. About 10 minutes per server; I would write the exact steps.
- A stored key is a **secret to protect** (in `.env`, file permissions, admin-only tool).
- New library: `paramiko` (SSH for Python, LGPL-2.1 licence — fine to use).
- Only Linux. Windows servers would need WinRM (a separate step, later).

**Effort:** medium. **Risk:** low with the allow-list; medium without it.

---

### Option C — Network devices (router / firewall appliance)

**What it is:** the edge of your network — router or firewall box — is asked through its own API.
**Each brand is different**, so this is built per brand:

| Brand | How | Read-only possible |
|---|---|---|
| MikroTik (RouterOS 7) | REST API | ✅ read-only user group |
| OPNsense | REST API with key | ✅ |
| pfSense | needs an API package | ✅ with the package |
| FortiGate | REST API token | ✅ read-only admin profile |
| Ubiquiti UniFi | controller API | ⚠ unofficial API |
| Any brand | SNMP v3 | ✅ but only interfaces / ARP / traffic, **no firewall rules** |

**What it can read:** the firewall rules that protect the **whole** network, port forwards (which
inside device is reachable from the internet — the most important question), VPN users, the
device's own log of blocked attacks, the ARP / DHCP tables (every device, even ones that don't answer
a scan), and the device's firmware version.

**Benefits**
- The **perimeter** — where internet attacks are actually stopped. Port forwards and the edge
  firewall are the #1 thing to audit.
- Complete device list from DHCP / ARP (better input for IoT finding and diagrams).

**Limits / costs**
- **Brand-specific:** one adapter per brand; I need to know which device you have.
- An API key for the most important box on the network — must be read-only and well protected.

**Effort:** medium per brand. **Risk:** low with a read-only key.

---

### Option D — Only from outside (scanning)

**What it is:** what `network_scan` does today, made deeper. From this PC, the tool connects to
ports on your hosts and reads what they answer — **the same view an intruder on your network has**.
No login anywhere.

| What it can read | How | Used by tool |
|---|---|---|
| Which ports are open on each host (about 40 instead of today's 10) | TCP connect | 1 |
| What runs on them and which **version** ("OpenSSH 8.9", "nginx 1.18", "IIS 10") | reads the greeting/banner the service sends | 1, 5 |
| HTTPS certificates: expired? self-signed? which names? | TLS handshake | 1, 7 |
| Device type hints: camera (RTSP 554), printer (9100), MQTT (1883), UPnP | port + manufacturer | 3 IoT |
| Known vulnerabilities for those versions | version → CVE list (downloaded, offline) | 5 |

**Benefits**
- Works on **every device** — Linux, Windows, printers, cameras, IoT, routers — with **no setup**.
- Shows what is really **exposed**, which is exactly what an attacker sees first.
- Builds directly on `network_scan` (already proven: 37 devices in ~29 s).

**Limits**
- Cannot see **why**: firewall rules, local-only services, missing patches that don't show in a banner,
  failed logins. A closed port looks the same whether a firewall blocks it or nothing listens.
- Versions from banners are sometimes hidden or vague → some vulnerability matches are "possible".
- Active scanning is noticeable on the network (some security software logs it) — that is why it stays
  admin-only, private-range-only, rate-limited.

**Effort:** small–medium. **Risk:** low.

---

## 4. Side by side

| | A. This PC | B. Linux via SSH | C. Network devices | D. From outside |
|---|---|---|---|---|
| **Setup needed** | none | audit account + key per server | API key per device | none |
| **Machines covered** | 1 (this PC) | each Linux server you add | the router/firewall | every device on the LAN |
| **Firewall rules** | ✅ Windows Firewall | ✅ ufw / iptables | ✅ the network's edge | ❌ only guesses |
| **Open ports** | ✅ exact, with program | ✅ exact, with program | — | ✅ as seen from outside |
| **Service versions / CVEs** | partly | ✅ exact packages | firmware only | ✅ from banners (approximate) |
| **Attack monitoring (logs)** | ✅ failed logins | ✅ SSH / fail2ban | ✅ blocked attacks | ❌ |
| **IoT devices** | ❌ | ❌ | ✅ via DHCP/ARP | ✅ |
| **Network diagrams** | ❌ | ❌ | ✅ topology | ✅ devices + ports |
| **Stored secret** | none | SSH key | API key | none |
| **Effort to build** | small | medium | medium per brand | small–medium |

**Which of your tools each option enables:**

| Tool | A | B | C | D |
|---|---|---|---|---|
| 1 Ports & server security | ✅ this PC | ✅ Linux | — | ✅ every host |
| 2 Firewall status / monitoring | ✅ Windows | ✅ ufw / iptables | ✅ edge | — |
| 3 IoT device finder | — | — | ✅ | ✅ |
| 4 HLD / LLD diagrams | — | — | ✅ | ✅ |
| 5 Vulnerability finding | partly | ✅ | firmware | ✅ |
| 6 Attack monitoring / prevention advice | ✅ | ✅ | ✅ | — |
| 7 Security report | ✅ | ✅ | ✅ | ✅ |

---

## 5. Recommendation

Build in layers, cheapest and safest first — each layer is useful on its own:

1. **D + A first (no logins, no setup).** D covers every device from outside; A gives the exact inside
   view of this PC. Together they already deliver tools 1, 3, 4, 5 (approximate) and the Windows parts
   of 2 and 6. Nothing to configure, nothing secret to store.
2. **B next — if you have Linux servers.** This is the only way to get ufw / iptables, patch levels
   and SSH hardening, which matter most for real servers.
3. **C last — once you tell me the router / firewall brand.** The perimeter view; one adapter per brand.

**For the first tool (Ports & server security)** this means: scan hosts from outside (D) for open
ports + service versions + certificates, and read this PC's own listening ports and settings (A), then
flag the risky ones. When B arrives, the same tool simply gains the inside view of each Linux server.

---

## 6. The first tool — Ports & server security (plan, with D + A)

**You ask:** "check the security of 192.168.1.10", "which servers have risky ports open?",
"is this PC secure?" — the IT specialist calls the tool; no new routes.

**It does:**
1. **Ports** — scans a host (or the whole subnet, reusing `network_scan`) for ~40 well-known ports
   instead of 10 (SSH, Telnet, FTP, SMTP, DNS, HTTP(S), SMB, RDP, databases, MQTT, VNC, …).
2. **Services** — reads each open port's banner → product + version; HTTPS → certificate expiry and names.
3. **This PC** (when the host is this PC) — listening ports with the owning program, firewall
   profiles, RDP, SMBv1 (from Windows directly).
4. **Flags** — a fixed, explainable rule list, e.g.:
   - 🔴 Telnet (23), FTP (21), SMBv1, RDP (3389) or databases (3306/5432/1433/27017/6379) reachable
   - 🟠 HTTP without HTTPS on a login page, expired / self-signed certificate, VNC (5900)
   - 🟡 SSH on 22 with an old version, many open ports on one host
5. **Answer** — a table per host (port, service, version, risk, why), a short summary, the fix for each
   red flag as *advice*, and an .xlsx like `network_scan`.

**Safety:** private ranges only, ≤1024 hosts, 60–120 s limit, Stop, admin-only, `SECURITY_SCAN_ENABLED`
off switch, no exploits, banners read once per port.

**Measured before we call it done:** a test against this PC and 2–3 known devices on your LAN, checked
by hand (does every flag match reality?), and a speed number.

---

## 7. What I need from you to decide

1. **Which layers:** A + D first (recommended) — and do you want B and/or C later?
2. **Linux servers:** do you have any? How many (roughly)? Is a read-only `multeagent-audit` account with a
   sudo allow-list acceptable?
3. **Router / firewall:** which brand and model is the edge of your network?
4. **Who may use these tools:** admins only (recommended), or also normal users for read-only results?

---

## 8. Built (2026-10-19) — `security_check` (D + A)

`src/security_scan.py`, tool `security_check` (IT specialist, admins only, `SECURITY_SCAN_ENABLED`).
Close to the machine, no model in the check: raw TCP connects to 43 ports, the banner each service sends,
Python's own TLS check for certificates, the OS connection table (psutil) for this PC, the registry (RDP)
and fixed read-only PowerShell commands (firewall profiles, SMBv1). Every finding comes from one rule table
with the reason and the fix as advice.

Measured on the real LAN (192.168.88.0/24):

| Check | Result |
|---|---|
| This PC | 2.1 s — firewall 3/3 profiles on, RDP off, SMBv1 off; 3 medium findings (RPC, NetBIOS, SMB listening on the network); 11 ports correctly local-only |
| One host (router 192.168.88.1) | 0.6 s — only SSH; the router hides its SSH software name (shown as "unknown software") |
| Whole network | 36 s, 42 hosts, 105 open services — 8 high (RDP ×4, Telnet, PostgreSQL ×2, Redis), 48 medium, 36 low; shown grouped (20 rows), every detail in the .xlsx |

Lessons: Windows lists each port per IPv4 / IPv6 / adapter → merged per port (7 duplicate findings → 3);
scanning this PC's own address would bypass Windows Firewall → this PC is always checked from the inside.

**Next:** B — Linux servers via SSH. Credentials go in `.env` (never in the chat); read-only commands only,
as in §2 — changing a server (close a port, edit ufw) is a later, separate step with a confirm button per command.

---

## 9. Built (2026-10-19) — `server_audit` (B, Linux over SSH)

**Logins — session only (user's decision):** typed in **Settings → Servers** (admins), never in the chat and
never in `.env`. `src/server_vault.py` keeps them in the API's memory only: not on disk, not in the database,
a log or a job, never sent to the model; per admin; forgotten at logout, API restart or after 8 hours.
Adding a server logs in once and remembers its SSH host key; a different key later is refused.
Only the in-process worker (`main.py --gui`) can use them — a separate worker process has its own memory.

**The audit** (`src/linux_audit.py`, tool `server_audit`, IT specialist, admins only): a fixed list of read-only
commands — `ss -tulnpH`, `ufw status verbose`, `iptables -S`, `nft list ruleset`, `sshd -T`,
`apt list --upgradable` / `dnf check-update`, reboot-required, failed SSH logins of 24 h (journalctl / auth.log),
`fail2ban-client status`. No user or model text ever becomes part of a command. Root-only commands use
`sudo -n`, or `sudo -S` with the password written to sudo's input (never on the command line). Rules flag:
no firewall, ufw default allow, root/password/empty-password SSH login, databases listening on the network,
security updates, reboot required, ≥20 failed logins (with the top source IPs), no fail2ban.

**Tested** with a fake SSH client and real command outputs (19 tests: the password never appears in a command,
an answer, an error or an API response). **Not yet tested against a real server** — first run by the user.

---

## 10. Vulnerability finders — what four open-source tools do (explained 2026-11-17, not decided)

The user's ask (2026-11-17): "we will understand what each do and then do these one by one — first we understand,
next make a plan, and then later decide what to do". Nothing below is built. ISSUES_LOG O87, BACKLOG L25.

**The gap today.** `security_check` reads each open port's greeting ("SSH-2.0-OpenSSH_8.9p1") and flags risky
ports, but never asks *"does this version have known holes?"* — the "version → CVE list" line of §3 Option D was
never built. `network_scan` names devices from its own small set of probes (SNMP, UPnP, ONVIF, HTTP titles).

### 10.1 The four tools in one picture

| | **V1 nmap -sV + offline CVE list** | **V2 Nuclei** | **V3 Trivy** | **V4 OpenVAS (Greenbone)** |
|---|---|---|---|---|
| **What it is** | nmap (already installed, 7.98) names the software and version behind each open port; we match that against the US government's CVE list (NVD), downloaded to this PC | one program + ~12,000 small check files ("templates"), each testing one known problem, mostly on web services | reads a machine's **installed package list** or a Docker image and compares it with the Linux distributions' own security lists | a complete vulnerability scanner with ~100,000 tests, its own web screen, schedules and reports |
| **Licence** | nmap: its own (free to use, we only run it); NVD data: public domain | MIT | Apache-2.0 | AGPL / GPL (we run it in Docker, we don't copy its code) |
| **Looks from** | outside (the network) | outside (the network) | inside (a server's package list, an image) | outside, or inside with a login |
| **Finds, for example** | "nginx 1.18.0 on 192.168.88.20 — 7 known CVEs, worst 9.8"; old OpenSSH, IIS, router web servers | an open `.git` folder, an exposed Grafana / Jenkins / phpMyAdmin, a specific CVE *confirmed* by the reply | "openssl 3.0.2-0ubuntu1.10 on uk server: CVE-…, fixed in 1.12" — exact, per package | all of the left, plus Windows and printer checks, weak TLS, default settings |
| **Needs installed** | nothing new; the CVE list is ~400 MB compressed once, then a 6 MB update every 2 h (checked 2026-11-17: `nvdcve-2.0-modified.json.gz` is 6 MB, one year ~26 MB) | one binary in `~/.local/bin` or the `projectdiscovery/nuclei` Docker image; no sudo | one binary or Docker image + its database (downloads itself, can run offline) | a dozen Docker containers; minimum 2 CPU / 4 GB RAM / 20 GB, recommended 4 / 8 GB / 60 GB (this PC: 45 GB RAM, 283 GB free); the first feed download takes a long time |
| **Effort for us** | medium (1–2 sessions) | medium | small (Docker images here), medium (servers) | large |

### 10.2 Each one, a little deeper

**V1 — nmap -sV + offline CVE list.** nmap sends each open port a few harmless questions and recognises the
answer from its database of ~12,000 service signatures, giving product, version and a standard product id
(CPE, e.g. `cpe:/a:openbsd:openssh:8.9p1`). We keep the NVD CVE list in Postgres and look up which CVEs list that
product and a version range that contains it, with their severity score (CVSS).
- *Good:* works on every device with no login; the same view an intruder has; no data leaves this PC at scan
  time; also makes `network_scan`'s device names better (nmap knows far more services than our probes).
- *Limits:* **backports** — Ubuntu fixes holes without changing the version number, so "OpenSSH 8.9p1" may be
  patched; these are shown as *possible*, not *confirmed* (V3 gives the exact answer for servers). Devices that
  hide their version give no match. Unprivileged nmap uses normal connections (no root, no OS fingerprint).
- *Safety:* fixed argument list, no NSE scripts, private ranges, admin only, Stop and time limit as today.

**V2 — Nuclei.** Each template says: send this request, and if the reply looks like *that*, report *this*
problem. Strong where V1 is blind: misconfigurations and exposed admin pages, and it can *confirm* a CVE instead
of guessing from a version.
- *Limits:* mostly web (HTTP); **some templates actually try the exploit** to see if it works, which breaks §2
  "no attacks" — so only an allow-list: exclude the `intrusive`, `dos`, `fuzz` and login-guessing tags,
  `-rate-limit` low, no `-dast`; templates pinned to a version (`-disable-update-check`), updated by an admin.
  Each template we allow must be readable as read-only.
- *Output:* `-jsonl` per finding → our table and .xlsx.

**V3 — Trivy.** Exact, because it uses the distributions' own fix lists (it knows the backports V1 cannot).
- *Docker images on this PC* (our Postgres, SearXNG …): `trivy image` — simple, no server touched.
- *Linux servers:* we would read the package list over the existing read-only SSH audit and hand Trivy an SBOM
  (`trivy sbom`). Trivy warns that SBOMs made by other tools may scan less accurately — **must be measured on the
  user's servers first**. The other way (copy Trivy to the server and run `trivy rootfs /`) writes a file on the
  server, which §2 forbids without the user's OK.

**V4 — OpenVAS.** The "big" scanner professionals use for recurring audits. It overlaps V1 + V2 and adds many
checks, but it is heavy, slow to set up, has its own UI and user accounts, and is driven through its GMP protocol
(e.g. the `gvm-cli` container). Its default scan settings keep "safe checks" on; we would use only those.

### 10.3 Proposed order (for the user to decide)

1. **V1** — fills the exact gap in `security_check` and improves `network_scan`; nothing to install.
2. **V3 for Docker images** — small; then V3 for servers once the SBOM accuracy is measured.
3. **V2** with a reviewed template allow-list — the web-layer checks V1 cannot do.
4. **V4** only if 1–3 leave real gaps; it needs the most setup and machine.

Questions before building: (a) start with V1? (b) may the CVE list download from nvd.nist.gov daily? (c) for V3 on
servers, SBOM over SSH only (read-only), or may Trivy be copied to a server? (d) who sees results — admins only, as
today?

---

## 11. Built (2026-11-17) — V1: exact versions and known CVEs in `security_check`

The user's choice (2026-11-17): "start with V1 nmap".

- **Versions** ([nmap_versions.py](../src/servers/nmap_versions.py)): after the port check, nmap -sV reads each host's
  open ports — `nmap -sT -sV --version-intensity 7 -Pn -n --host-timeout 60s -p <open ports> -oX - <ip>`, a fixed
  list, no NSE scripts, no root, 8 hosts at once, 180 s for this step. Only software CPEs (`cpe:/a:`) are kept: an OS
  CPE without a version would match every kernel CVE.
- **The CVE list** ([cve_db.py](../src/servers/cve_db.py)): the NVD JSON 2.0 feeds in one SQLite file
  (`CVE_DB`, default `data/cve/nvd.sqlite`, not in git). `python -m src.servers.cve_db --update`: every year's
  feed the first time (~400 MB), then only the "modified" feed (8 days of changes); `--status` shows the date.
  Rejected CVEs are removed. Nothing leaves the PC during a check.
- **Matching**: a CVE's software criteria give an exact version (2.3's update field joined: `8.9` + `p1`) or a range
  (start/end, included/excluded); versions compare part by part (numbers as numbers). A software CPE with no
  version (`cpe:/a:postgresql:postgresql`) is never guessed. A renamed vendor is followed: nmap says
  `igor_sysoev:nginx`, the NVD files nginx as `f5:nginx` (703 of 705 entries) — when the NVD has nothing under
  nmap's vendor, the vendor with ≥ 90 % of the product's entries is used; a name several vendors share stays
  unmatched (found 2026-11-17 on the real list: nginx 1.18.0 had 0 hits, now 6 incl. CVE-2021-23017).
- **Report**: "Known vulnerabilities (possible, matched by software version)" — per service the number of CVEs and
  the worst; a finding rated by the worst CVSS (7.0+ high, 4.0+ medium, else low) whose fix says OS packages may
  be patched without a new number; the Excel file gets a Vulnerabilities sheet with every CVE. Without nmap or the
  list, the answer says so and the check works as before.
- **Checked**: 8 offline tests (sample nmap XML, a small feed); the real modified feed (10,866 CVEs, 3.8 s, 4 MB);
  nmap on this PC's own ports (Uvicorn, PostgreSQL — no version, so no CVEs).

**Still to do:** the user's first `--update` and a check on the LAN; nmap's names for `network_scan`'s devices.

### 11.1 Fixes from the first runs on the user's LAN (2026-11-17)

| Seen | Cause | Fix |
|---|---|---|
| "check the security of my network 192.168.88.0/24" ran a network scan (device list) | the router reads only a tool's first sentence; `security_check`'s ended before "not for listing devices", and `network_scan`'s did not say "inventory" | both first sentences tell the tools apart on their own (assistant.py); live router check: 5/5 right |
| 192.168.88.10 (no device there) → "No risky ports found" | nothing answered, and the report read as "safe" | "No port answered … nothing to judge" |
| NAS: no versions after 64 s | nmap drops every answer of a host that hits `--host-timeout`; the NAS's web ports gave nothing in 62 s while SMB / NFS were named in 6 s | one nmap per open port, 16 at once, 30 s each — the NAS: 34 s, Samba named |
| NAS "Version: Strict-Transport-Security: max-age=0" | an empty `Server:` header; the regex's `\s*` jumped to the next line | `[ \t]*` |
| Samba "4" → 45 CVEs back to 2017 | a bare major number is a release family | at least major.minor before matching |
| "OpenSSH for_Windows_9.5" → 80 CVEs from 2003 | the leading words ranked the version below every number | a version starts at its first number |
| CVE-2008-3844 on OpenSSH 9.5 | NVD rows naming no version or range (0.6 %) matched everything | such rows match nothing |
| nginx 1.18.0 → 0 CVEs | nmap's vendor `igor_sysoev`, the NVD's `f5` | renamed-vendor fallback (≥ 90 % share) |

Whole network after the fixes: 28 hosts in 130 s; real finds include Telnet on .3, Redis 3.0.504 and PostgreSQL
reachable on .66, nginx 1.26.1 (CVE-2025-23419) on .53.

---

## 12. V3 — Trivy for the Docker images on this PC (built 2026-11-17, needs Trivy installed)

The user's choice: "now start V3 trivy". Tool `image_scan` ([image_scan.py](../src/servers/image_scan.py)), IT
specialist, admins only, offered only when `trivy` and `docker` are found: `docker image ls` lists the images (the
user's words only pick among them), `trivy image --quiet --format json --scanners vuln <image>` checks each,
read-only. The answer: a table per image (critical / high / medium / low, how many have a fix), the worst five per
image with "fixed in", every vulnerability in an .xlsx. Servers (an SBOM read over SSH) come next, after measuring.

**Supply-chain warning.** In March 2026 attackers published a malicious Trivy **v0.69.4** (GitHub, deb, rpm, GHCR,
ECR, Docker Hub) and malicious Docker Hub images **v0.69.5 and v0.69.6** that stole secrets
(GHSA-69fq-xp46-6x23, CVE-2026-33634). Never install those; never install "latest" blindly. On 2026-11-17 the
current release is **v0.76.2** (5 Nov 2026); each release carries a checksums file and Sigstore bundles.

**Install (the user runs it; no sudo, into ~/.local/bin):**

```bash
V=0.76.2; D=$(mktemp -d); cd "$D"
base=https://github.com/aquasecurity/trivy/releases/download/v$V
curl -fsSLO $base/trivy_${V}_Linux-64bit.tar.gz
curl -fsSLO $base/trivy_${V}_checksums.txt
curl -fsSLO $base/trivy_${V}_checksums.txt.sigstore.json
sha256sum --ignore-missing -c trivy_${V}_checksums.txt        # must print: trivy_…_Linux-64bit.tar.gz: OK
# the signature proves the checksums came from Aqua's own release workflow (needs cosign, see below)
cosign verify-blob --bundle trivy_${V}_checksums.txt.sigstore.json \
  --certificate-identity-regexp 'https://github\.com/aquasecurity/' \
  --certificate-oidc-issuer 'https://token.actions.githubusercontent.com' trivy_${V}_checksums.txt
mkdir -p ~/.local/bin && tar -xzf trivy_${V}_Linux-64bit.tar.gz -C ~/.local/bin trivy
trivy --version                                                # Version: 0.76.2
trivy image --download-db-only                                 # its database, once (then it refreshes itself)
```

The checksum alone only proves the download is the file GitHub serves; the cosign step proves who built it. cosign
is not on this PC: install it from its own release (github.com/sigstore/cosign) and check its checksum, or skip
the step knowingly. ~/.local/bin must be on the API's PATH (restart the API after installing).

---

## 13. V3b — known vulnerabilities in a Linux server's packages (built 2026-11-17)

The user's choice: "trivy installed, now start V3b servers". Part of `server_audit` (no new tool: "audit my server"
and "check my server for vulnerable packages" both route there — its first sentence now says so).

**How.** Over the audit's existing SSH login, two fixed commands that need no root: `dpkg-query -W -f=…` (Debian,
Ubuntu) or `rpm -qa --qf …` (RHEL, Rocky, Alma, CentOS, Fedora, Amazon, Oracle), plus /etc/os-release and
/etc/debian_version. Nothing is installed or written on the server (the §10 alternative, `trivy rootfs` on the
server, would copy a program there). On this PC, [package_scan.py](../src/servers/package_scan.py) writes the list as
a CycloneDX SBOM **in the exact shape Trivy writes for an OS**: one `operating-system` component (`ubuntu` / `24.04`),
and per package the purl (`pkg:deb/ubuntu/libssl3@…?arch=amd64&distro=ubuntu-24.04&epoch=…`) with Trivy's own
properties — `SrcName`, `SrcVersion`, `SrcRelease`, `SrcEpoch` — because Debian's and Ubuntu's fix lists are keyed by
the *source* package (`openssl`, not `libssl3`). Then `trivy sbom` in a temporary folder.

**Measured first (as decided in §10).** Trivy warns that SBOMs from other tools may scan less accurately, so ours was
compared with Trivy's own scan of the same system, CVE by CVE and package by package:

| System | Packages | Trivy's own scan | Ours | Same CVE + package | Fix version / severity differ |
|---|---|---|---|---|---|
| Debian 12.11 (the pgvector image) | 147 | 747 | 747 | 747 | 0 |
| Ubuntu 26.04 (this PC, its dpkg status) | 2,528 | 6,200 | 6,200 | 6,200 | 0 |
| AlmaLinux 9.2 (`almalinux:9.2-minimal`) | 97 | 374 | 374 | 374 | 0 |
| Rocky Linux 8.7 (`rockylinux:8.7-minimal`) | 95 | 262 | 262 | 262 | 0 |

The first try found **0 of 747**: the SBOM's top component was also typed `operating-system`, and Trivy takes the
first one as the OS (with no version, nothing matches). Now `application`; a test keeps it so. The measurement images
were pulled for the check and removed after it.

**What the chat shows.** Trivy answers one row per CVE *and binary package* (a kernel CVE repeats in a dozen
`linux-*` packages: this PC had 6,200 rows for 876 CVEs). The audit groups per source package and counts each CVE
once; a finding "N critical/high known vulnerabilities with a fix" (high) or "N with a fix" (medium) with
`sudo apt update && sudo apt upgrade` / `sudo dnf upgrade`; installed kernels that are not the running one are set
apart (low, `sudo apt autoremove --purge` / `sudo dnf remove --oldinstallonly`). The table shows the 10 worst source
packages with the version that fixes them; every row is in `vulnerable-packages-<server>.xlsx`. On this PC: 497 CVEs,
3 with a fix, 494 with no fix yet; 626 more only in the old kernel 7.0.0-42.

**Privacy.** Both Trivy commands (`image_scan` too) run with `--disable-telemetry --skip-version-check
--offline-scan`: Trivy otherwise sends usage data to Aqua and a version check on every run (seen in its debug log),
and may send Java file checksums to Maven Central. Only the vulnerability database is downloaded (ghcr.io, daily).

**Limits.** Packages only: software installed by hand, from a tarball or in a language package manager (pip, npm)
is not in the list, and containers on the server are not looked into. Arch, Alpine and SUSE servers are reported as
"cannot check by its package list". "No fix yet" is the vendor's status (many are low impact on a server).

---

## 14. V2 — Nuclei web checks on one device (built 2026-11-17, needs Nuclei installed)

The user's choice: "now start V2 nuclei". Part of `security_check` — no new tool. When ONE device is checked
("check the security of 192.168.88.53"), Nuclei runs on its open web ports (80, 443, 8080 … from the port check,
at most 8 URLs) after the version step; its matches join the Findings table (an exposed `.git` folder, a debug page,
a CVE the page gives away; admin login pages as ⚪ Info) and the Excel file gets a "Web checks" sheet with the exact
URL and template. A whole-network check does not run it (minutes per device) and says how to ask for one device.
Code: [web_scan.py](../src/servers/web_scan.py).

**§2 "no exploits" — the allow-list.** Nuclei never gets the template folder, only `data/nuclei/allowlist.txt`, made
by `python -m src.servers.web_scan --build` from each template's own content. A template may run only if:
- it is **signed** (`# digest:` line — an edited template is refused, and `-dut` refuses it again at run time);
- its protocol is `http` or `ssl` only — no `flow`, `code`, `javascript`, `headless`, `network`, `dns`, `file`,
  workflows; and not `self-contained` (its own target);
- **every request is a plain GET/HEAD to the device itself** (`{{BaseURL}}`, `{{Hostname}}` …): no `payloads`,
  `fuzzing`, `attack`, request body, `race`/`unsafe`/`pipeline`, redirects (could leave the device), callback server
  (`{{interactsh-url}}`), and no attack-looking path (`../`, `%2e%2e`, `/etc/passwd`, `cmd=`, `${`, `<script` …);
- no attack tag (rce, sqli, lfi, traversal, ssrf, xss, injection, default-login, brute-force, intrusive, dos, fuzz,
  takeover, osint …) and not in those folders;
- severity low or above — info only for `http/exposed-panels/` (an admin page reachable).
`allowlist-review.md` beside it counts what was kept by severity and what was refused, by reason — the admin reads it
before the first run, and again after each template update (then `--build` again).

**Run.** `nuclei -l <targets> -t allowlist.txt -pt http,ssl -etags <attack tags> -dut -ni -duc -rl 30 -c 10 -bs 4
-timeout 5 -retries 0 -mhe 10 -jsonl -silent -nc -or`: signed templates only, no interactsh (nothing sent to an
outside callback server), no update check, 30 requests/s at most, no raw request/response kept; targets are the
device's private address only; 300 s limit; Stop kills it. Not used: `-fr`, `-headless`, `-code`, `-lfa`, `-ev`,
`-dast`, `-dashboard`, `-ai` (the last two send data to ProjectDiscovery).

**Install (the user, without sudo).** Nuclei **v3.11.1** (8 Aug 2026) — it fixes the 2026 template-trust CVEs
(signature bypass by a replaced template, CVE-2026-92718: 3.7.0–3.11.0; unsigned code templates in DAST,
CVE-2026-76802). Checksum of `nuclei_3.11.1_linux_amd64.zip` from the release's checksums file:
`ea63d4ae232808cd7c6bc00d0142428e231fab59dae01042246097d195835ab6`.

```bash
V=3.11.1; D=$(mktemp -d); cd "$D"; b=https://github.com/projectdiscovery/nuclei/releases/download/v$V
curl -fsSLO $b/nuclei_${V}_linux_amd64.zip; curl -fsSLO $b/nuclei_${V}_checksums.txt
sha256sum --ignore-missing -c nuclei_${V}_checksums.txt          # must say OK
unzip -o nuclei_${V}_linux_amd64.zip nuclei -d ~/.local/bin && nuclei -version -duc
cd ~/projects/multeagent
nuclei -duc -update-templates -update-template-dir data/nuclei/templates     # official, signed templates
.venv/bin/python -m src.servers.web_scan --build                           # writes the allow-list + its review
```
Then restart the API. Templates at the time of writing: v10.5.2 (12 Nov 2026).

**Not measured yet** (the download was not allowed from the assistant's session): how many templates the allow-list
keeps, how long one device takes, and whether a known case is found. First run on the user's PC: read
`allowlist-review.md`, then check one device with web pages (e.g. 192.168.88.53, nginx 1.26.1).
