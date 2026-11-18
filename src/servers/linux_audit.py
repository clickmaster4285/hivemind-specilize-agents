"""server_audit — read-only security audit of a Linux server over SSH (doc/SECURITY_TOOLS_DECISION.md, option B).

The login comes from the session store (src/server_vault.py) — typed in Settings → Devices, never in
the chat, never on disk. Close to the machine: the tool logs in and runs a FIXED list of read-only
commands (below — no text from the user or the model ever becomes part of a command), then applies
fixed rules to what they print:

  firewall      ufw status verbose · iptables -S · nft list ruleset
  open ports    ss -tulnpH (what listens, on which address, which program)
  SSH settings  sshd -T (root login, password login, empty passwords, tries, X11)
  updates       apt list --upgradable / dnf check-update (+ security updates) · reboot-required
  attacks       failed SSH logins of the last 24 h (journalctl / auth.log) · fail2ban status
  packages      dpkg-query / rpm -qa → known vulnerabilities by Trivy on THIS PC (package_scan.py, V3b)

Commands that need root use `sudo -n` (passwordless sudo) or, with a password login, `sudo -S` with the
password written to sudo's input — never into the command line. Nothing on the server is changed.
"""

from __future__ import annotations

import base64
import hashlib
import io
import logging
import re
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from src.core.config import Config
from src.servers import package_scan
from src.servers.image_scan import SEVERITY_LABEL, ImageReport, xlsx_bytes
from src.servers.package_scan import PackageGroup, PackageScanError
from src.servers.security_scan import HIGH, LOW, MEDIUM, PORTS, RISK_LABEL, Finding, _order, _table
from src.servers.server_vault import ServerLogin, VaultError, check_host
from src.servers.win_commands import ps_wrap
from src.tools.tool_run import ToolRun

logger = logging.getLogger(__name__)

CONNECT_TIMEOUT = 10
COMMAND_TIMEOUT = 25            # s a command may go without any output before we stop waiting for it
TIMED_OUT = 124                 # the exit code timeout(1) gives a command it stopped


class CommandTimeout(TimeoutError):
    """A command sent no output for COMMAND_TIMEOUT seconds (ISSUES_LOG O64). Its channel is closed; the remote
    process may still be running."""
TEST_LOGIN_TIMEOUT = 22         # hard cap on "Add & test login" so the request never hangs the browser
FAILED_LOGINS_ALERT = 20        # failed SSH logins in 24 h that count as an ongoing guessing attack
LOCAL_ADDRESSES = ("127.", "::1", "[::1]", "localhost")
NEEDS_ROOT_NOTE = "needs root — give the account passwordless sudo for this command, or log in as root"

# key → (command, needs root). FIXED strings: never built from user or model text.
COMMANDS: dict[str, tuple[str, bool]] = {
    "whoami": ("id -un", False),
    "os": ("cat /etc/os-release", False),
    "kernel": ("uname -r", False),
    "hostname": ("hostname", False),
    "ports": ("ss -tulnpH", True),
    "ufw": ("ufw status verbose", True),
    "iptables": ("iptables -S", True),
    "nft": ("nft list ruleset", True),
    "sshd": ("sshd -T", True),
    "apt": ("apt list --upgradable", False),
    "dnf": ("dnf -q check-update", False),
    "dnf_security": ("dnf -q updateinfo list --security", False),
    "reboot": ("test -f /var/run/reboot-required && echo yes || echo no", False),
    "journal": ("journalctl -q --no-pager -u ssh -u sshd --since -24h -o short-iso", True),
    "authlog": ("grep -hE 'Failed password|Invalid user' /var/log/auth.log /var/log/secure", True),
    "fail2ban": ("fail2ban-client status", True),
}
FAILED_RE = re.compile(r"Failed password|Invalid user|authentication failure", re.I)
FROM_IP_RE = re.compile(r"\bfrom (\d{1,3}(?:\.\d{1,3}){3}|[0-9a-f:]{3,})", re.I)


class AuditError(RuntimeError):
    """Could not log in, or the server's identity changed."""


# ── connecting ───────────────────────────────────────────────────────────
def fingerprint(key: Any) -> str:
    digest = base64.b64encode(hashlib.sha256(key.asbytes()).digest()).decode().rstrip("=")
    return f"{key.get_name()} SHA256:{digest}"


def _load_key(text: str, passphrase: str | None) -> Any:
    import paramiko

    last: Exception | None = None
    for kind in (paramiko.Ed25519Key, paramiko.ECDSAKey, paramiko.RSAKey):
        try:
            return kind.from_private_key(io.StringIO(text.strip() + "\n"), password=passphrase or None)
        except (paramiko.SSHException, ValueError) as exc:
            last = exc
    raise AuditError(f"the private key could not be read ({type(last).__name__}) — "
                     "is it complete, and the passphrase right?")


def connect(login: ServerLogin) -> tuple[Any, str]:
    """→ (paramiko client, host key fingerprint). With login.host_key set, any other key is refused."""
    import paramiko

    seen: dict[str, str] = {}

    class _Remember(paramiko.MissingHostKeyPolicy):
        def missing_host_key(self, client, hostname, key):
            seen["fp"] = fingerprint(key)
            if login.host_key and seen["fp"] != login.host_key:
                raise AuditError(f"the server's identity changed (was {login.host_key}, now {seen['fp']}) — "
                                 "refusing to log in. Remove and re-add the server if it was reinstalled.")

    address = check_host(login.host)
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(_Remember())   # no known_hosts file: every key goes through the check above
    started = time.monotonic()
    logger.info("SSH connect → %s:%s as %s", address, login.port, login.username)
    try:
        client.connect(address, port=login.port, username=login.username, password=login.password,
                       pkey=_load_key(login.private_key, login.key_passphrase) if login.private_key else None,
                       look_for_keys=False, allow_agent=False, timeout=CONNECT_TIMEOUT,
                       banner_timeout=CONNECT_TIMEOUT, auth_timeout=CONNECT_TIMEOUT)
        logger.info("SSH connected to %s:%s in %.1fs", address, login.port, time.monotonic() - started)
    except paramiko.AuthenticationException as exc:
        client.close()
        raise AuditError("login refused — check the user name and password / key") from exc
    except AuditError:
        client.close()
        raise
    except (paramiko.SSHException, OSError) as exc:
        client.close()
        raise AuditError(f"could not connect to {login.shown} ({type(exc).__name__})") from exc
    return client, seen.get("fp", "")


def detect_os(client: Any) -> tuple[str, str]:
    """(os_family, os_name): try Linux first (uname), then a Windows PowerShell probe. Defaults to linux
    so a shell we cannot identify still goes down the well-tested Linux path."""
    try:
        _in, out, _err = client.exec_command("uname -s -r", timeout=CONNECT_TIMEOUT)
        text = out.read().decode("utf-8", "replace").strip()
        linux = out.channel.recv_exit_status() == 0 and bool(re.search(r"linux|darwin|bsd", text, re.I))
    except Exception:  # noqa: BLE001 — fall through to the Windows probe
        linux = False
    if linux:
        return "linux", ""
    try:
        probe = 'powershell -NoProfile -NonInteractive -Command "(Get-CimInstance Win32_OperatingSystem).Caption"'
        _in, out, _err = client.exec_command(probe, timeout=CONNECT_TIMEOUT)
        caption = out.read().decode("utf-8", "replace").strip()
        if out.channel.recv_exit_status() == 0 and caption:
            return "windows", caption
    except Exception:  # noqa: BLE001
        pass
    return "linux", ""


@dataclass
class Shell:
    """Runs commands on one SSH connection. Linux: a bash command with a fixed PATH, with/without sudo.
    Windows: the command is PowerShell, run via -EncodedCommand (see win_commands.ps_wrap)."""
    client: Any
    password: str | None = field(default=None, repr=False)
    root: bool = False
    sudo: str = "none"                  # none | passwordless | password (Linux)
    os_family: str = "linux"            # linux | windows
    is_admin: bool = False              # Windows: the account is an Administrator

    def raw(self, command: str, stdin_text: str | None = None, silence: int = COMMAND_TIMEOUT) -> tuple[int, str, str]:
        """silence: seconds the command may send nothing before CommandTimeout (an install or build: longer)."""
        full = ps_wrap(command) if self.os_family == "windows" else f"LC_ALL=C PATH=$PATH:/usr/sbin:/sbin {command}"
        stdin, stdout, stderr = self.client.exec_command(full, timeout=silence)
        if stdin_text is not None:
            stdin.write(stdin_text)
            stdin.flush()
        stdin.channel.shutdown_write()
        try:
            out = stdout.read().decode("utf-8", "replace")
            err = stderr.read().decode("utf-8", "replace")
        except TimeoutError as exc:                 # socket.timeout: silent for COMMAND_TIMEOUT
            stdout.channel.close()                  # stop waiting; the session stays usable for the next command
            raise CommandTimeout(f"no output for {silence} s") from exc
        return stdout.channel.recv_exit_status(), out, err

    def detect_sudo(self) -> None:
        code, out, _ = self.raw("id -u")
        self.root = code == 0 and out.strip() == "0"
        if self.root:
            return
        if self.raw("sudo -n true")[0] == 0:
            self.sudo = "passwordless"
        elif self.password and self.raw("sudo -S -p '' true", stdin_text=self.password + "\n")[0] == 0:
            self.sudo = "password"

    def detect_admin(self) -> None:
        code, out, _ = self.raw("[bool](([Security.Principal.WindowsPrincipal]"
                                "[Security.Principal.WindowsIdentity]::GetCurrent())"
                                ".IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator))")
        self.is_admin = code == 0 and out.strip().lower() == "true"

    def detect_access(self) -> str:
        """Learn what the account may do, and return a short label for the report."""
        if self.os_family == "windows":
            self.detect_admin()
            return "administrator" if self.is_admin else "standard user"
        self.detect_sudo()
        return "root" if self.root else f"sudo: {self.sudo}"

    def run(self, key: str) -> tuple[int, str, str]:
        command, needs_root = COMMANDS[key]
        if needs_root and not self.root and self.sudo == "passwordless":
            return self.raw(f"sudo -n {command}")
        if needs_root and not self.root and self.sudo == "password":
            return self.raw(f"sudo -S -p '' {command}", stdin_text=(self.password or "") + "\n")
        return self.raw(command)


def _test_login(login: ServerLogin) -> dict[str, Any]:
    client, fp = connect(login)
    try:
        family, win_name = detect_os(client)
        shell = Shell(client, password=login.password, os_family=family)
        access = shell.detect_access()
        os_name = (win_name or "Windows") if family == "windows" else parse_os(shell.run("os")[1]).get("PRETTY_NAME", "Linux")
        return {"host_key": fp, "os": os_name, "access": access, "os_family": family}
    finally:
        client.close()


def test_login(login: ServerLogin) -> dict[str, Any]:
    """Settings → Devices → Add: log in once, learn the host key and what the account may read.

    A hard wall-clock cap so the request can never hang: paramiko's own timeouts bound each phase, but
    a stuck key exchange or a server that stalls auth could still block — if that happens, fail cleanly
    well before the browser gives up, and say so. A dropped session ("SSH session not active") is usually
    the server rate-limiting repeated connections (sshd MaxStartups), so retry once after a short pause."""
    import concurrent.futures as cf

    import paramiko

    last: Exception | None = None
    for attempt in range(2):
        with cf.ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(_test_login, login)
            try:
                return future.result(timeout=TEST_LOGIN_TIMEOUT)
            except cf.TimeoutError as exc:
                raise AuditError(
                    f"timed out logging in to {login.shown} after {TEST_LOGIN_TIMEOUT}s — "
                    "the port may be firewalled, or the server is slow to answer SSH. Check the address and port, "
                    "and that this machine can reach it (try `ssh` to it from the same machine)."
                ) from exc
            except AuditError:
                raise
            except paramiko.SSHException as exc:
                last = exc
                logger.info("test_login attempt %d dropped (%s); retrying", attempt + 1, exc)
                if attempt == 0:
                    time.sleep(2.0)             # let the server's connection rate-limit cool down
                    continue
                raise AuditError(
                    f"the server dropped the SSH session ({exc}) — this usually means it is rate-limiting "
                    "repeated logins. Wait ~30 seconds and try again; if it keeps happening, check the server's "
                    "sshd settings (MaxStartups) or any fail2ban / firewall rule."
                ) from exc
            except Exception as exc:
                raise AuditError(f"could not log in to {login.shown}: {type(exc).__name__}: {exc}") from exc
    raise AuditError(f"could not log in to {login.shown}: {last}")


# ── reading what the commands print ──────────────────────────────────────
def parse_os(text: str) -> dict[str, str]:
    out = {}
    for line in text.splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            out[key.strip()] = value.strip().strip('"')
    return out


@dataclass
class Port:
    proto: str
    address: str
    port: int
    program: str

    @property
    def network(self) -> bool:
        return not self.address.startswith(LOCAL_ADDRESSES)


def parse_ss(text: str) -> list[Port]:
    """`ss -tulnpH` → listening ports (tcp LISTEN, udp UNCONN), one per (proto, address, port)."""
    ports: dict[tuple[str, str, int], Port] = {}
    for line in text.splitlines():
        cols = line.split()
        if len(cols) < 5 or cols[0] not in ("tcp", "udp") or cols[1] not in ("LISTEN", "UNCONN"):
            continue
        address, _, port = cols[4].rpartition(":")
        if not port.isdigit():
            continue
        address = address.split("%")[0] or "*"
        prog = m.group(1) if (m := re.search(r'users:\(\("([^"]+)"', line)) else "—"
        ports.setdefault((cols[0], address, int(port)), Port(cols[0], address, int(port), prog))
    return sorted(ports.values(), key=lambda p: (not p.network, p.port, p.proto))


def parse_ufw(text: str) -> dict[str, Any]:
    status = re.search(r"Status:\s*(\w+)", text)
    default = re.search(r"Default:\s*(\w+)\s*\(incoming\)", text)
    rules = [line for line in text.splitlines() if re.search(r"\b(ALLOW|DENY|REJECT|LIMIT)\b", line)]
    return {"active": (status.group(1).lower() == "active") if status else None,
            "incoming": default.group(1).lower() if default else "", "rules": rules}


def parse_iptables(text: str) -> dict[str, Any]:
    policy = re.search(r"^-P INPUT (\w+)", text, re.M)
    return {"input_policy": policy.group(1) if policy else "",
            "drop_rules": len(re.findall(r"^-A INPUT .*-j (?:DROP|REJECT)", text, re.M)),
            "rules": len(re.findall(r"^-A ", text, re.M))}


def nft_filters_input(text: str) -> bool:
    """An nftables input chain that drops or rejects something."""
    return bool(re.search(r"hook input.*?(policy drop|\bdrop\b|\breject\b)", text, re.S))


def parse_sshd(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in text.splitlines():
        parts = line.strip().split(None, 1)
        if len(parts) == 2:
            out.setdefault(parts[0].lower(), parts[1].strip())
    return out


def parse_apt(text: str) -> tuple[int, int]:
    """→ (updates, security updates) from `apt list --upgradable`."""
    lines = [line for line in text.splitlines() if "[upgradable from" in line]
    return len(lines), sum(1 for line in lines if "-security" in line)


def parse_dnf(text: str) -> int:
    return sum(1 for line in text.splitlines()
               if len(line.split()) == 3 and "." in line.split()[0] and not line.startswith(("Last", "Obsoleting")))


def parse_failed_logins(text: str) -> tuple[int, list[tuple[str, int]]]:
    lines = [line for line in text.splitlines() if FAILED_RE.search(line)]
    sources = Counter(m.group(1) for line in lines if (m := FROM_IP_RE.search(line)))
    return len(lines), sources.most_common(5)


def parse_fail2ban(text: str, code: int) -> bool | None:
    """True = running with jails, False = not installed / not running, None = could not tell (no root)."""
    if code == 0:
        return "Jail list" in text
    if code == 127 or "not found" in text:
        return False
    if "denied" in text.lower() or "sudo" in text.lower():
        return None
    return False


# ── the audit ────────────────────────────────────────────────────────────
@dataclass
class Audit:
    label: str
    host: str
    user: str = ""
    access: str = ""                    # root | sudo: passwordless | sudo: password | sudo: none
    os_name: str = ""
    kernel: str = ""
    hostname: str = ""
    ports: list[Port] = field(default_factory=list)
    ufw: dict[str, Any] = field(default_factory=dict)
    iptables: dict[str, Any] = field(default_factory=dict)
    nft_input: bool = False
    sshd: dict[str, str] = field(default_factory=dict)
    updates: int | None = None
    security_updates: int | None = None
    reboot_required: bool = False
    failed_logins: int | None = None
    top_sources: list[tuple[str, int]] = field(default_factory=list)
    fail2ban: bool | None = None
    distro: str = ""                    # Trivy's name for the OS ("ubuntu"); "" = packages not checked
    os_version: str = ""                # as Trivy matches it ("24.04", "12.11")
    package_count: int = 0
    vulnerable: ImageReport | None = None                    # Trivy's rows, one per CVE and package (→ .xlsx)
    package_groups: list[PackageGroup] = field(default_factory=list)
    unread: list[str] = field(default_factory=list)          # what could not be read, and why
    findings: list[Finding] = field(default_factory=list)
    seconds: float = 0.0


def collect(shell: Shell, audit: Audit, should_continue: Callable[[], bool], status: Callable[[str], None]) -> None:
    silent: list[str] = []

    def run(key: str) -> tuple[int, str, str]:
        if not should_continue():
            raise InterruptedError
        try:
            return shell.run(key)
        except CommandTimeout:
            # one silent command is skipped and named; the uk server's audit was lost whole to one (ISSUES_LOG O93)
            logger.warning("server audit: `%s` sent no output for %d s — skipped", COMMANDS[key][0], COMMAND_TIMEOUT)
            silent.append(f"`{COMMANDS[key][0]}`")
            return TIMED_OUT, "", f"no output for {COMMAND_TIMEOUT} s"

    try:
        _collect(run, shell, audit, status)
    finally:
        if silent:
            audit.unread.append(f"what these commands show — no output for {COMMAND_TIMEOUT} s, skipped: "
                                + ", ".join(silent))


def _collect(run: Callable[[str], tuple[int, str, str]], shell: Shell, audit: Audit,
             status: Callable[[str], None]) -> None:

    status("Reading the system…")
    audit.user = run("whoami")[1].strip()
    audit.access = "root" if shell.root else f"sudo: {shell.sudo}"
    audit.os_name = parse_os(run("os")[1]).get("PRETTY_NAME", "Linux")
    audit.kernel = run("kernel")[1].strip()
    audit.hostname = run("hostname")[1].strip()

    status("Reading open ports…")
    code, out, err = run("ports")
    audit.ports = parse_ss(out)
    if code != 0 and not audit.ports:
        audit.unread.append(f"open ports ({err.strip()[:80] or 'ss failed'})")
    elif audit.ports and all(p.program == "—" for p in audit.ports):
        audit.unread.append(f"the programs behind the ports: {NEEDS_ROOT_NOTE}")

    status("Reading the firewall…")
    code, out, _ = run("ufw")
    if "Status:" in out:
        audit.ufw = parse_ufw(out)
    code, out, _ = run("iptables")
    if code == 0:
        audit.iptables = parse_iptables(out)
    code, out, _ = run("nft")
    audit.nft_input = code == 0 and nft_filters_input(out)
    if not audit.ufw and not audit.iptables and code != 0:
        audit.unread.append(f"the firewall (ufw / iptables / nftables): {NEEDS_ROOT_NOTE}")

    status("Reading SSH settings…")
    code, out, err = run("sshd")
    audit.sshd = parse_sshd(out) if code == 0 else {}
    if not audit.sshd:
        audit.unread.append(f"SSH server settings: {NEEDS_ROOT_NOTE}" if not shell.root and shell.sudo == "none"
                            else f"SSH server settings ({err.strip()[:80] or 'sshd -T failed'})")

    status("Checking for updates…")
    code, out, _ = run("apt")
    if code == 0 and ("Listing" in out or "[upgradable from" in out):
        audit.updates, audit.security_updates = parse_apt(out)
    else:
        code, out, _ = run("dnf")
        if code in (0, 100):                               # dnf: 100 = updates available
            audit.updates = parse_dnf(out)
            code, out, _ = run("dnf_security")
            audit.security_updates = parse_dnf(out) if code in (0, 100) else None
        else:
            audit.unread.append("updates (neither apt nor dnf answered)")
    audit.reboot_required = run("reboot")[1].strip() == "yes"

    status("Reading failed logins (24 h)…")
    code, out, _ = run("journal")
    if code == 0 and out.strip():
        audit.failed_logins, audit.top_sources = parse_failed_logins(out)
    else:
        code, out, _ = run("authlog")
        if code in (0, 1):                                 # grep: 1 = nothing found
            audit.failed_logins, audit.top_sources = parse_failed_logins(out)
        else:
            audit.unread.append(f"failed logins: {NEEDS_ROOT_NOTE}")
    code, out, err = run("fail2ban")
    audit.fail2ban = parse_fail2ban(out + err, code)


def _packages(groups: list) -> str:
    """["linux"] → "linux"; 5 groups → "5 packages (linux, openssl, …)"."""
    names = [g.source for g in groups]
    if len(names) <= 3:
        return ", ".join(names)
    return f"{len(names)} packages ({', '.join(names[:3])}, …)"


def audit_findings(a: Audit) -> list[Finding]:
    out: list[Finding] = []

    def add(risk: str, port: int | None, title: str, why: str, fix: str) -> None:
        out.append(Finding(risk, a.label, port, title, why, fix))

    # firewall
    ufw_on = a.ufw.get("active") is True
    ipt_filters = a.iptables.get("input_policy") in ("DROP", "REJECT") or a.iptables.get("drop_rules", 0) > 0
    if ufw_on and a.ufw.get("incoming") not in ("deny", "reject"):
        add(MEDIUM, None, f"ufw lets incoming traffic in by default ({a.ufw.get('incoming') or 'unknown'})",
            "Every port that is not explicitly blocked is reachable", "sudo ufw default deny incoming (allow SSH first)")
    elif not ufw_on and not ipt_filters and not a.nft_input and (a.ufw or a.iptables):
        add(HIGH, None, "No firewall filters incoming traffic", "Every listening service is reachable from the network",
            "sudo ufw allow OpenSSH && sudo ufw default deny incoming && sudo ufw enable")
    # SSH
    s = a.sshd
    if s.get("permitrootlogin") == "yes":
        add(HIGH, 22, "SSH allows root login with a password", "One guessed password = full control",
            "PermitRootLogin prohibit-password (or no) in /etc/ssh/sshd_config, then restart sshd")
    if s.get("permitemptypasswords") == "yes":
        add(HIGH, 22, "SSH allows empty passwords", "Accounts without a password can log in", "PermitEmptyPasswords no")
    if s.get("passwordauthentication") == "yes":
        add(MEDIUM, 22, "SSH accepts passwords", "Passwords can be guessed; keys cannot",
            "Use SSH keys, then PasswordAuthentication no")
    if s.get("maxauthtries", "").isdigit() and int(s["maxauthtries"]) > 6:
        add(LOW, 22, f"SSH allows {s['maxauthtries']} tries per connection", "More guesses per connection",
            "MaxAuthTries 3–4")
    if s.get("x11forwarding") == "yes":
        add(LOW, 22, "SSH X11 forwarding is on", "Rarely needed on servers; widens what a session can reach",
            "X11Forwarding no")
    # open ports
    for p in a.ports:
        rule = PORTS.get(p.port)
        if p.network and p.proto == "tcp" and rule and rule.risk in (HIGH, MEDIUM):
            add(rule.risk, p.port, f"{rule.name} listening on the network ({p.program})",
                f"{rule.why}; the firewall decides whether other devices get through", rule.fix)
    # updates
    if a.security_updates:
        add(HIGH if a.security_updates >= 10 else MEDIUM, None, f"{a.security_updates} security updates waiting",
            "Known holes stay open until they are installed",
            "sudo apt update && sudo apt upgrade (Debian/Ubuntu) or sudo dnf upgrade --security")
    elif a.updates:
        add(LOW, None, f"{a.updates} updates waiting", "Bug and security fixes not installed yet", "Plan an update window")
    # known vulnerabilities in installed packages (Trivy, V3b): only those with a fix are something to do
    running = [g for g in a.package_groups if not g.old_kernel and g.fixable]
    serious = {c for g in running for c in g.serious_fixable}
    if serious:
        add(HIGH, None, f"{len(serious)} critical/high known vulnerabilities with a fix, in "
            f"{_packages([g for g in running if g.serious_fixable])}",
            "Published holes with published fixes — the first thing attackers try",
            package_scan.upgrade_command(a.distro))
    elif running:
        add(MEDIUM, None, f"{len({c for g in running for c in g.fixable})} known vulnerabilities with a fix, in "
            f"{_packages(running)}", "Medium/low holes with published fixes", package_scan.upgrade_command(a.distro))
    old = [g for g in a.package_groups if g.old_kernel]
    if old:
        add(LOW, None, f"Old kernels still installed ({len({c for g in old for c in g.cves})} known vulnerabilities)",
            "Not running, but kept on disk and bootable", package_scan.old_kernel_command(a.distro))
    if a.reboot_required:
        add(MEDIUM, None, "Reboot required", "Installed security fixes (often the kernel) are not active yet",
            "Reboot in a maintenance window")
    # attacks
    if a.failed_logins is not None and a.failed_logins >= FAILED_LOGINS_ALERT:
        sources = ", ".join(f"{ip} ×{n}" for ip, n in a.top_sources) or "—"
        add(MEDIUM, 22, f"{a.failed_logins} failed SSH logins in 24 h", f"Password guessing — from {sources}",
            "Use keys only; " + ("check the fail2ban jail" if a.fail2ban else "install fail2ban (sudo apt install fail2ban)"))
    if a.fail2ban is False and s.get("passwordauthentication") == "yes":
        add(LOW, 22, "No fail2ban", "Password guessing is never blocked", "sudo apt install fail2ban")
    return sorted(out, key=_order)


def read_packages(shell: Shell, audit: Audit, should_continue: Callable[[], bool],
                  status: Callable[[str], None]) -> list[package_scan.Package]:
    """The installed packages, read over the audit's login (no root needed), for Trivy on this PC; [] (with a
    note) when they cannot be checked."""
    if not package_scan.available():
        audit.unread.append("known vulnerabilities in packages (Trivy is not installed on the MulteAgent PC — "
                            "doc/SECURITY_TOOLS_DECISION.md §12)")
        return []
    if not should_continue():
        raise InterruptedError
    status("Reading installed packages…")
    try:
        audit.distro, audit.os_version, _, packages = package_scan.read_packages(
            lambda key: shell.raw(package_scan.COMMANDS[key]))
    except (PackageScanError, CommandTimeout) as exc:
        audit.unread.append(f"known vulnerabilities in packages ({exc})")
        return []
    audit.package_count = len(packages)
    return packages


def run_audit(login: ServerLogin, *, should_continue: Callable[[], bool] = lambda: True,
              status: Callable[[str], None] = lambda message: None) -> Audit:
    started = time.monotonic()
    audit = Audit(label=login.shown, host=login.host)
    status(f"Logging in to {login.shown}…")
    client, _ = connect(login)
    packages: list[package_scan.Package] = []
    try:
        shell = Shell(client, password=login.password)
        try:
            shell.detect_sudo()
        except CommandTimeout:          # a hanging sudo check: audit as the login itself (the notes say root-only parts)
            logger.warning("server audit: the sudo check sent no output for %d s — going on without sudo",
                           COMMAND_TIMEOUT)
        try:
            collect(shell, audit, should_continue, status)
            packages = read_packages(shell, audit, should_continue, status)
        except InterruptedError:
            audit.unread.append("the rest — stopped before the audit finished")
    finally:
        client.close()
    if packages and should_continue():
        status(f"Checking {len(packages)} packages for known vulnerabilities (Trivy)…")
        audit.vulnerable = package_scan.check_packages(audit.label, audit.distro, audit.os_version, packages)
        if audit.vulnerable.error:
            audit.unread.append(f"known vulnerabilities in packages ({audit.vulnerable.error})")
        audit.package_groups = package_scan.group(audit.vulnerable, packages, audit.kernel)
    audit.findings = audit_findings(audit)
    audit.seconds = round(time.monotonic() - started, 1)
    return audit


# ── presentation ─────────────────────────────────────────────────────────
def _yes(value: str | None) -> str:
    return {"yes": "**yes**", "no": "no"}.get((value or "").lower(), value or "—")


def markdown(a: Audit) -> str:
    counts = {r: sum(1 for f in a.findings if f.risk == r) for r in (HIGH, MEDIUM, LOW)}
    parts = [f"Audit of **{a.label}** — {a.os_name or 'Linux'}, kernel {a.kernel or '?'}, as `{a.user or '?'}` "
             f"({a.access}) in {a.seconds:g} s: **🔴 {counts[HIGH]} high · 🟠 {counts[MEDIUM]} medium · "
             f"🟡 {counts[LOW]} low**."]
    if a.findings:
        parts.append("### Findings\n" + _table(["Risk", "Port", "Finding", "Why it matters", "What to do"],
                                                [[RISK_LABEL[f.risk], str(f.port or "—"), f.title, f.why, f.fix]
                                                 for f in a.findings]))
    else:
        parts.append("No risky settings found by the rules.")
    fw = []
    if a.ufw:
        fw.append(f"ufw **{'active' if a.ufw.get('active') else 'inactive'}**, default incoming: "
                  f"{a.ufw.get('incoming') or '—'}, {len(a.ufw.get('rules', []))} rules")
    if a.iptables:
        fw.append(f"iptables INPUT policy {a.iptables.get('input_policy') or '—'}, {a.iptables.get('rules', 0)} rules")
    if a.nft_input:
        fw.append("nftables filters incoming traffic")
    parts.append("### Firewall\n" + ("; ".join(fw) if fw else "not read"))
    if a.sshd:
        keys = ["port", "permitrootlogin", "passwordauthentication", "pubkeyauthentication", "permitemptypasswords",
                "maxauthtries", "x11forwarding"]
        parts.append("### SSH settings\n" + _table(["Setting", "Value"], [[k, _yes(a.sshd.get(k))] for k in keys]))
    exposed = [p for p in a.ports if p.network]
    if exposed:
        parts.append("### Listening on the network\n" + _table(
            ["Proto", "Address", "Port", "Program", "Known as"],
            [[p.proto, p.address, str(p.port), p.program, PORTS[p.port].name if p.port in PORTS else "—"]
             for p in exposed]) + f"\n\n{len(a.ports) - len(exposed)} more listen on this server only (127.0.0.1).")
    updates = ("not read" if a.updates is None else
               f"{a.updates} waiting" + (f", **{a.security_updates} security**" if a.security_updates else "")
               + (" · **reboot required**" if a.reboot_required else ""))
    logins = ("not read" if a.failed_logins is None else
              f"{a.failed_logins} failed SSH logins in 24 h"
              + (" — top sources: " + ", ".join(f"{ip} ×{n}" for ip, n in a.top_sources) if a.top_sources else ""))
    f2b = {True: "running", False: "not installed / not running", None: "not read"}[a.fail2ban]
    parts.append(f"### Updates and attacks\nUpdates: {updates}\n\nLogins: {logins}\n\nfail2ban: {f2b}")
    if a.vulnerable is not None and not a.vulnerable.error:
        parts.append(packages_markdown(a))
    if a.unread:
        parts.append("**Not read:** " + "; ".join(a.unread) + ".")
    parts.append("_Read-only audit: fixed commands only, nothing on the server was changed or installed (the "
                 "package list is checked by Trivy on this PC). The fixes are advice "
                 "— run them yourself. The login stays in this session's memory only._")
    return "\n\n".join(parts)


PACKAGES_SHOWN = 10        # source packages in the chat table; every row is in the Excel file


def packages_markdown(a: Audit) -> str:
    """Counts are distinct CVEs; old kernels (installed, not running) are counted apart, so the numbers add up."""
    current = [g for g in a.package_groups if not g.old_kernel]
    old = [g for g in a.package_groups if g.old_kernel]
    cves = {c for g in current for c in g.cves}
    fixable = {c for g in current for c in g.fixable}
    head = (f"### Known vulnerabilities in installed packages\n{a.package_count} packages checked by Trivy against "
            f"{a.distro.title()} {a.os_version}'s own security lists: **{len(cves)} known vulnerabilities** "
            f"(each CVE counted once), **{len(fixable)} with a fix released**"
            + (f", {len(cves) - len(fixable)} with no fix yet (the vendor decides; often low impact on a server)"
               if len(cves) > len(fixable) else "") + ".")
    shown = [g for g in current if g.fixable][:PACKAGES_SHOWN]
    notes = []
    if shown:
        notes.append(_table(["Package", "Installed", "Worst", "With a fix (critical/high)", "Fixed in"],
                            [[g.source, g.installed, SEVERITY_LABEL.get(g.worst, g.worst.title()),
                              f"{len(g.fixable)} ({len(g.serious_fixable)})", g.fixed] for g in shown]))
        more = sum(1 for g in current if g.fixable) - len(shown)
        notes.append(f"**Fix:** `{package_scan.upgrade_command(a.distro)}` installs every fix"
                     + (f" ({more} more packages in the Excel file)." if more > 0 else "."))
    if old:
        notes.append(f"Old kernels that are installed but not running carry "
                     f"{len({c for g in old for c in g.cves})} more: `{package_scan.old_kernel_command(a.distro)}` "
                     "removes them.")
    return "\n\n".join([head, *notes])


# ── as a chat job ────────────────────────────────────────────────────────
def run_server_audit(
    query: str,
    *,
    job_id: str,
    config: Config,
    on_progress: Callable[[dict], None] | None = None,
    server: str | None = None,
    user_id: int | None = None,
    **_: object,
) -> tuple[str, list[str], float]:
    """'audit my web server' → log in (session login) → fixed read-only commands → findings. No model."""
    from src.jobs import live
    from src.servers import server_vault

    emit = on_progress or (lambda event: None)
    run = ToolRun(on_progress, query=query, job_id=job_id, role="Server audit", model="server_audit", accent="#c45c5c")
    errors: list[str] = []

    status = run.status
    try:
        if not config.security_scan_enabled:
            raise AuditError("security tools are switched off (SECURITY_SCAN_ENABLED=false)")
        login, ask_first = server_vault.login_for(user_id, server, query, question="Which server should I audit?")
        if login is None:
            output = ask_first
        else:
            audit = run_audit(login, should_continue=lambda: not live.is_stopped(job_id), status=status)
            output = markdown(audit)
            if audit.vulnerable is not None and audit.vulnerable.vulns:
                from src.tools.artifacts import artifact_event, write_bytes

                name = re.sub(r"[^\w.-]+", "_", login.shown).strip("_") or "server"
                meta = write_bytes(config, job_id, f"vulnerable-packages-{name}.xlsx",
                                   xlsx_bytes([audit.vulnerable], "Server"), kind="xlsx")
                emit(artifact_event(meta, job_id=job_id))
    except (AuditError, VaultError) as exc:
        output = f"The audit was not run: {exc}."
        errors.append(str(exc))
    except Exception as exc:  # noqa: BLE001 — report, never crash the worker; type only (no secrets in messages)
        logger.error("server audit failed: %s", type(exc).__name__)
        output = f"The audit failed ({type(exc).__name__})."
        errors.append(type(exc).__name__)
    return run.finish(output, errors)
