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
