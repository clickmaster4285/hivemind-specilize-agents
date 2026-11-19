"""diagnose_server — the agent logs in to a Linux server and investigates, read-only (SERVER_ACCESS_DECISION.md §8).

"log in to web01 and check why nginx keeps dying" → the chat model drives a loop: it calls run_command,
reads the output, decides the next command, and repeats until it can explain the issue. Every command is
checked by a DETERMINISTIC read-only classifier (classify_command) before it runs — a command that would
change the server is refused, whatever the model asks. This is step 1 of SERVER_ACCESS_DECISION.md:
making changes (confirm + audit log) comes next and is not in this module.

Login: the session store (src/server_vault.py). SSH: reused from src/linux_audit.py. No model is trusted
to decide what is safe — the classifier is.
"""

from __future__ import annotations

import hashlib
import logging
import re
import shlex
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from src.core.config import Config
from src.core.loops import start_loop
from src.servers.linux_audit import COMMAND_TIMEOUT, AuditError, CommandTimeout, Shell, connect
from src.llm.llm_client import LLMClient
from src.llm.prompt_filter import mask_text
from src.servers.server_vault import ServerLogin, VaultError
from src.servers.win_commands import classify_windows_command, is_destructive_windows
from src.tools.tool_run import ToolRun

logger = logging.getLogger(__name__)

MAX_ROUNDS = 12                 # how many commands the agent may run per question
MAX_OUTPUT_CHARS = 4000         # of one command's output kept for the model
NO_OUTPUT = "(no output)"       # what an empty output reads as (for the model); code that parses output: blank
LOG_COMMAND_CHARS = 200         # of a timed-out command written to the API log (secrets masked first)
TIMED_OUT_NOTE = (f"TIMED OUT: no output for {COMMAND_TIMEOUT} s, so I stopped waiting (it may still be running on "
                  "the server). Do not run it again as it is; use a command that finishes on its own (no follow, "
                  "no pager, no prompt), or say what the user should run themselves.")

# Programs that only read. A command may only run if EVERY program in it is here (classify_command).
READ_ONLY: frozenset[str] = frozenset({
    # files / text
    "cat", "head", "tail", "less", "more", "ls", "ll", "dir", "stat", "file", "wc", "grep", "egrep",
    "fgrep", "zgrep", "zcat", "cut", "sort", "uniq", "tr", "awk", "nl", "tac", "readlink", "realpath",
    "basename", "dirname", "find", "locate", "md5sum", "sha256sum", "cmp", "diff", "column", "fold",
    "strings", "xxd", "hexdump", "od", "pwd", "echo", "printf", "true", "false", "test", "seq", "jq", "base64",
    "cd",       # changes only the folder this one command runs in: each command is a new shell (O67, O68)
    # system / processes
    "uname", "hostname", "hostnamectl", "uptime", "whoami", "id", "groups", "who", "w", "last", "lastlog",
    "date", "cal", "env", "printenv", "ps", "top", "htop", "pgrep", "pidof", "free", "vmstat", "iostat",
    "mpstat", "sar", "lscpu", "lsmem", "lsblk", "lsusb", "lspci", "lsof", "dmesg", "nproc", "getconf",
    "getent", "timedatectl", "localectl", "loginctl", "needrestart",
    # disk
    "df", "du", "mount", "findmnt", "blkid", "fdisk", "parted", "smartctl",
    # services / logs (subcommands checked below)
    "systemctl", "service", "journalctl", "rc-status", "supervisorctl",
    # packages (query only, checked below)
    "dpkg", "dpkg-query", "apt", "apt-cache", "rpm", "yum", "dnf", "snap", "pip", "pip3", "npm",
    # network
    "ip", "ifconfig", "ss", "netstat", "route", "arp", "ping", "ping6", "traceroute", "tracepath",
    "mtr", "dig", "host", "nslookup", "ssh-keygen", "ufw", "iptables", "nft", "firewall-cmd", "fail2ban-client",
    # containers / web / db (subcommands checked below)
    "docker", "podman", "kubectl", "git", "crontab", "nginx", "apache2ctl", "apachectl", "httpd",
})

# For programs that can also write, the only sub-actions allowed (first meaningful argument).
SUBCOMMANDS: dict[str, frozenset[str]] = {
    "systemctl": frozenset({"status", "show", "list-units", "list-unit-files", "list-timers", "list-sockets",
                            "list-dependencies", "is-active", "is-enabled", "is-failed", "is-system-running",
                            "cat", "get-default", "show-environment"}),
    "service": frozenset({"status", "--status-all"}),
    "docker": frozenset({"ps", "logs", "inspect", "images", "stats", "top", "port", "version", "info", "df",
                         "events", "history", "system"}),
    "podman": frozenset({"ps", "logs", "inspect", "images", "stats", "top", "port", "version", "info"}),
    "kubectl": frozenset({"get", "describe", "logs", "top", "version", "explain", "api-resources", "cluster-info"}),
    "git": frozenset({"status", "log", "diff", "show", "branch", "remote", "config", "rev-parse", "describe",
                      "shortlog", "blame", "ls-files", "tag", "reflog", "ls-remote"}),
    "crontab": frozenset({"-l"}),
    "apt": frozenset({"list", "show", "policy", "search"}),
    "apt-cache": frozenset({"policy", "show", "search", "showpkg", "depends", "madison"}),
    "dnf": frozenset({"list", "info", "search", "repolist", "check-update", "updateinfo", "history", "repoquery"}),
    "yum": frozenset({"list", "info", "search", "repolist", "check-update", "history"}),
    "snap": frozenset({"list", "info", "find", "changes", "version"}),
    "dpkg": frozenset({"-l", "-L", "-s", "-S", "--list", "--status", "--listfiles", "--search"}),
    "pip": frozenset({"list", "show", "freeze", "--version", "check"}),
    "pip3": frozenset({"list", "show", "freeze", "--version", "check"}),
    "npm": frozenset({"list", "ls", "view", "outdated", "--version", "config"}),
    "nginx": frozenset({"-t", "-T", "-v", "-V"}),
    "apache2ctl": frozenset({"-t", "-S", "-v", "-V", "configtest", "status"}),
    "apachectl": frozenset({"-t", "-S", "-v", "-V", "configtest", "status"}),
    "httpd": frozenset({"-t", "-S", "-v", "-V"}),
    "ufw": frozenset({"status"}),
    "firewall-cmd": frozenset({"--list-all", "--list-all-zones", "--state", "--get-active-zones", "--get-zones",
                              "--list-services", "--list-ports"}),
    "fail2ban-client": frozenset({"status", "version", "ping", "get"}),
    "supervisorctl": frozenset({"status", "avail"}),
}
# Flags/words that turn an otherwise read-only program into a writer — reject the whole command.
WRITE_FLAGS: dict[str, frozenset[str]] = {
    "sed": frozenset({"-i", "--in-place"}),
    "find": frozenset({"-delete", "-exec", "-execdir", "-ok", "-okdir", "-fprint", "-fprintf", "-fls"}),
    "iptables": frozenset({"-A", "-I", "-D", "-R", "-F", "-X", "-N", "-P", "-Z"}),
    "nft": frozenset({"add", "delete", "flush", "insert", "replace", "create"}),
    "ip": frozenset({"add", "del", "delete", "set", "change", "replace", "flush"}),
}
# Between two commands. Split by the shell's own rules, so a `|` inside quotes (grep -E "a|b") is not a pipe (O67)
_SEPARATORS = frozenset({"|", "||", "&&", ";", "&", ";;"})
_SUBSHELL = re.compile(r"\$\([^)]*\)|`[^`]*`|<\([^)]*\)")
# redirections that only discard output or merge it; any other `>` writes a file
_HARMLESS_REDIRECTS = ("2>&1", "2>/dev/null", ">/dev/null")


def _first_program(tokens: list[str]) -> tuple[str, list[str]] | None:
    """Drop leading env assignments / sudo / nice / etc., return (program, its args)."""
    i = 0
    while i < len(tokens):
        t = tokens[i]
        if re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", t):                # VAR=value prefix
            i += 1
            continue
        if t in ("sudo", "command", "nice", "ionice", "nohup", "stdbuf", "env", "time", "exec", "\\"):
            i += 1
            while i < len(tokens) and tokens[i].startswith("-"):    # sudo -u user, nice -n 5
                i += 2 if tokens[i] in ("-u", "-g", "-n", "-p") else 1
            continue
        return t.split("/")[-1], tokens[i + 1:]
    return None


def _commands_in(command: str) -> list[list[str]] | str:
    """The simple commands in a command line, as token lists, split at | || && ; & outside quotes; or the
    reason it is NOT read-only (a write redirection, or it cannot be parsed)."""
    for harmless in _HARMLESS_REDIRECTS:
        command = command.replace(harmless, " ")
    lexer = shlex.shlex(command.replace("\n", " ; "), posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    try:
        tokens = list(lexer)
    except ValueError:
        return "it could not be parsed safely"
    if any(">" in t for t in tokens if set(t) <= set(lexer.punctuation_chars)):
        return "it writes to a file (>)"
    parts: list[list[str]] = [[]]
    for token in tokens:
        if token in _SEPARATORS:
            parts.append([])
        else:
            parts[-1].append(token)
    return [p for p in parts if p]


def _check_simple(tokens: list[str]) -> str | None:
    """A single command's tokens: reason it is NOT read-only, or None if it is."""
    found = _first_program(tokens)
    if found is None:
        return None
    program, args = found
    if program not in READ_ONLY:
        return f"'{program}' is not a read-only command"
    bad = WRITE_FLAGS.get(program)
    if bad and any(a in bad for a in args):
        return f"'{program}' here would change the server"
    allowed = SUBCOMMANDS.get(program)
    if allowed is not None and not any(a in allowed for a in args):
        first = next((a for a in args if not a.startswith("-")), args[0] if args else "")
        return f"'{program} {first}'.strip() is not a read-only action".replace("'.strip()", "'")
    return None


def classify_command(command: str) -> str | None:
    """Reason the command is NOT read-only, or None when it is safe. Conservative: anything not understood
    is refused. Handles pipes, ; && ||, command substitution and redirection."""
    command = command.strip()
    if not command:
        return "empty command"
    if len(command) > 2000:
        return "the command is too long"
    for inner in _SUBSHELL.findall(command):          # $(...) / `...` / <(...)
        reason = classify_command(inner.strip("$()`<> "))
        if reason:
            return f"a sub-command is not read-only ({reason})"
    parts = _commands_in(_SUBSHELL.sub(" echo INNER ", command))
    if isinstance(parts, str):
        return parts
    for part in parts:
        reason = _check_simple(part)
        if reason:
            return reason
    return None


# ── one server connection for a diagnose session ──────────────────────────
@dataclass
class CommandRun:
    command: str
    code: int | None = None
    output: str = ""
    refused: str = ""           # set when the classifier blocked it
    timed_out: bool = False     # no output for COMMAND_TIMEOUT s (O64): stopped waiting, the job goes on

    @property
    def outcome(self) -> str:
        """For the Commands list."""
        return "refused" if self.refused else "timed out" if self.timed_out else f"exit {self.code}"

    @property
    def observation(self) -> str:
        """What the model is told after the command ran."""
        return TIMED_OUT_NOTE if self.timed_out else f"exit {self.code}\n{self.output}"


# Commands refused even in "trusted" mode and even if you click Run — the catastrophes a confirm box
# should never let through by a mis-click. The confirm box is the main protection; this is the floor.
DENY: list[tuple[re.Pattern, str]] = [
    (re.compile(r":\s*\(\s*\)\s*\{"), "looks like a fork bomb"),
    (re.compile(r"\bmkfs\b|\bmke2fs\b"), "formats a filesystem"),
    (re.compile(r"\bdd\b[^|;&]*\bof=/dev/"), "overwrites a disk device"),
    (re.compile(r">\s*/dev/sd|>\s*/dev/nvme|>\s*/dev/vd"), "overwrites a disk device"),
    (re.compile(r"\b(shutdown|reboot|halt|poweroff)\b"), "shuts down or reboots the server"),
    (re.compile(r"\binit\s+[06]\b"), "shuts down or reboots the server"),
    (re.compile(r"\brm\b\s+(?:-\S+\s+)*-\S*[rR]\S*f|\brm\b\s+(?:-\S+\s+)*-\S*f\S*[rR]"), None),  # rm -rf, checked below
    (re.compile(r"\buserdel\b[^;]*\broot\b|\bpasswd\s+root\b|\bchpasswd\b"), "changes the root account"),
    (re.compile(r"\bchmod\b\s+-R\s+0*0{3}\s+/(?:\s|$)"), "strips all permissions from the whole filesystem"),
    (re.compile(r"\bchown\b\s+-R\s+\S+\s+/(?:\s|$)"), "re-owns the whole filesystem"),
]
# rm -rf is only denied outright when it targets a top-level / dangerous path (a normal subdir is fine via confirm)
_RM_TOP = re.compile(r"\brm\b[^;&|]*\s(/|/\*|~|/\*\s|\$HOME|\.|\.\.)(\s|$)")


def is_destructive(command: str) -> str | None:
    """Reason a command must never run (even trusted / even confirmed), or None."""
    for pattern, reason in DENY:
        if pattern.search(command):
            if reason is None:                       # the rm -rf rule: only top-level targets
                if _RM_TOP.search(command):
                    return "a recursive delete of a top-level path"
                continue
            return reason
    return None


# ── pick the classifier for the server's OS (Linux = bash, Windows = PowerShell) ───────────────────
def classify_for(command: str, os_family: str) -> str | None:
    """Reason the command is NOT read-only (or None if it is), using the right classifier for the OS."""
    return classify_windows_command(command) if os_family == "windows" else classify_command(command)


def destructive_for(command: str, os_family: str) -> str | None:
    """Reason the command must never run (or None), using the right deny-list for the OS."""
    return is_destructive_windows(command) if os_family == "windows" else is_destructive(command)


def _access_line(shell: Shell) -> str:
    """One sentence telling the model what the account may do, for the system prompt."""
    if shell.os_family == "windows":
        return "You are an Administrator." if shell.is_admin else "You run as a standard user (not an Administrator)."
    return "You have root." if shell.root else f"You run as a normal user (sudo: {shell.sudo})."


class RemoteSession:
    """A logged-in SSH connection. `run` enforces read-only; `execute` runs anything (used after approval)."""

    def __init__(self, login: ServerLogin):
        self.login = login
        self._log_in()
        self.runs: list[CommandRun] = []

    def _log_in(self) -> None:
        """Connect and learn what the account may do (root, sudo). A server that logs us in but then does not
        answer that first check is reported in plain words, not as a bare TimeoutError (O64)."""
        self.client, _ = connect(self.login)
        self.shell = Shell(self.client, password=self.login.password, os_family=self.login.os_family)
        try:
            self.shell.detect_access()
        except CommandTimeout as exc:
            self.client.close()
            logger.warning("server_ops: %s logged in but the access check got %s", self.login.host, exc)
            raise AuditError(f"logged in to {self.login.shown}, but it did not answer the first check (who am I, "
                             f"may I use sudo) within {COMMAND_TIMEOUT} s. The server may be very busy; "
                             "try again in a minute") from exc

    def _reconnect(self) -> None:
        """A long operate/diagnose session can be dropped by the server (idle, MaxStartups). Log back in
        so the next command still runs instead of failing the whole operation."""
        try:
            self.client.close()
        except Exception:  # noqa: BLE001 — already broken; closing is best-effort
            pass
        logger.info("server_ops reconnecting to %s after a dropped session", self.login.host)
        self._log_in()

    def _raw(self, command: str, silence: int) -> tuple[int, str, str]:
        import paramiko

        try:
            return self.shell.raw(command, silence=silence)
        except paramiko.SSHException:
            self._reconnect()                 # one retry on a dropped session
            return self.shell.raw(command, silence=silence)

    def execute(self, command: str, *, silence: int = COMMAND_TIMEOUT, keep_end: bool = False,
                max_chars: int = MAX_OUTPUT_CHARS) -> CommandRun:
        """Run the command with no read-only check (the caller has already decided it may run). A command that
        goes silent comes back as a timed-out run, so the model can go on instead of the job failing (O64).
        silence: seconds it may send nothing (an install or a build: minutes); keep_end: a long output keeps its
        end (where a build says what failed) instead of its start; max_chars: output kept (a JSON list read by
        code needs all of it — a cut one cannot be parsed)."""
        try:
            code, out, err = self._raw(command, silence)
        except CommandTimeout:
            logger.warning("server_ops: no output for %d s on %s, stopped waiting: %s", silence,
                           self.login.host, mask_text(command)[0][:LOG_COMMAND_CHARS])
            run = CommandRun(command=command, timed_out=True)
            self.runs.append(run)
            return run
        text = (out + (("\n" + err) if err.strip() else "")).strip()
        if len(text) > max_chars:
            text = (f"… (start cut, last {max_chars} characters)\n" + text[-max_chars:] if keep_end
                    else text[:max_chars] + f"\n… (output cut at {max_chars} characters)")
        run = CommandRun(command=command, code=code, output=text or NO_OUTPUT)
        self.runs.append(run)
        return run

    def run(self, command: str) -> CommandRun:
        """Read-only only (diagnose): a changing command is refused, never sent to the server."""
        reason = classify_for(command, self.login.os_family)
        if reason:
            run = CommandRun(command=command, refused=reason)
            self.runs.append(run)
            logger.info("server_ops refused: %s (%s)", command, reason)
            return run
        return self.execute(command)

    def close(self) -> None:
        self.client.close()

    @property
    def alive(self) -> bool:
        transport = getattr(self.client, "get_transport", lambda: None)()
        return bool(transport and transport.is_active())


# ── logins kept open between messages (ISSUES_LOG O96) ─────────────────────
# Every server message used to log in again (~1.2 s with password auth, more with a slow DNS or a busy sshd).
# A finished job hands its session back; the next job for the same login within KEEP_OPEN_SECONDS takes it
# instead of logging in. One job at a time per session (taken out while in use). Changed credentials make a new
# key, so a stale connection is never reused; removing a server closes its connection at once (forget).
KEEP_OPEN_SECONDS = 600         # a follow-up usually comes within minutes; idle root logins should not linger
KEEPALIVE_SECONDS = 30          # keeps a NAT or firewall from dropping the idle connection
SWEEP_SECONDS = 60
_idle: dict[tuple[str, ...], tuple[RemoteSession, float]] = {}
_idle_lock = threading.Lock()
_sweeper: threading.Thread | None = None


def _pool_key(login: ServerLogin) -> tuple[str, ...]:
    secret = (login.password or "") + "\0" + (login.private_key or "") + "\0" + (login.key_passphrase or "")
    return (login.id, login.host, str(login.port), login.username, login.host_key,
            hashlib.sha256(secret.encode()).hexdigest())


def _close_quietly(session: RemoteSession) -> None:
    try:
        session.close()
    except Exception:  # noqa: BLE001 — closing a dead connection is best-effort
        pass


def _expired(now: float) -> list[RemoteSession]:
    """Take the sessions idle too long out of the pool (the caller closes them, outside the lock)."""
    with _idle_lock:
        old = [key for key, (_s, since) in _idle.items() if now - since > KEEP_OPEN_SECONDS]
        return [_idle.pop(key)[0] for key in old]


def open_session(login: ServerLogin) -> RemoteSession:
    """A logged-in session for this login: the one kept from the last job when it is still connected, else a
    new login."""
    for stale in _expired(time.monotonic()):
        _close_quietly(stale)
    with _idle_lock:
        kept = _idle.pop(_pool_key(login), None)
    if kept is not None:
        if kept[0].alive:
            kept[0].login, kept[0].runs = login, []        # the mode may have changed in Settings since
            logger.info("server_ops: reusing the open login to %s", login.host)
            return kept[0]
        _close_quietly(kept[0])
    fresh = RemoteSession(login)
    transport = getattr(fresh.client, "get_transport", lambda: None)()
    if transport is not None:
        transport.set_keepalive(KEEPALIVE_SECONDS)
    return fresh


def release_session(session: RemoteSession) -> None:
    """The job is done with it: keep it for the next message, or close it when the connection is gone."""
    if not session.alive:
        _close_quietly(session)
        return
    key = _pool_key(session.login)
    with _idle_lock:
        previous = _idle.pop(key, None)
        _idle[key] = (session, time.monotonic())
    if previous is not None:
        _close_quietly(previous[0])
    _start_sweeper()


def forget(login_ids: set[str]) -> None:
    """These logins were removed (Settings → Devices, or access taken away): close their kept connections now."""
    with _idle_lock:
        gone = [_idle.pop(key)[0] for key in [k for k in _idle if k[0] in login_ids]]
    for session in gone:
        _close_quietly(session)


def _start_sweeper() -> None:
    """Closes idle sessions on time even when no new message comes (open_session also sweeps)."""
    global _sweeper
    with _idle_lock:
        if _sweeper is not None and _sweeper.is_alive():
            return

        def sweep() -> None:
            for stale in _expired(time.monotonic()):
                _close_quietly(stale)

        _sweeper = start_loop("ssh-idle-sweeper", sweep, every_seconds=SWEEP_SECONDS, first_delay=SWEEP_SECONDS,
                              log=logger)


# ── the diagnose loop (the model drives, the classifier guards) ────────────
RUN_TOOL = {
    "type": "function",
    "function": {
        "name": "run_command",
        "description": ("Run ONE read-only shell command on the Linux server and get its output. Use this to "
                        "investigate: check services, logs, disk, config, processes. Read-only only — a command "
                        "that would change the server is refused. Run commands one at a time and read each result "
                        "before the next. When you understand the issue, stop calling tools and write your findings."),
        "parameters": {"type": "object", "required": ["command"],
                       "properties": {"command": {"type": "string",
                                                  "description": "the command, e.g. systemctl status nginx"}}},
    },
}

RUN_TOOL_WIN = {
    "type": "function",
    "function": {
        "name": "run_command",
        "description": ("Run ONE read-only PowerShell command on the Windows server and get its output. Use this to "
                        "investigate: services, event logs, disk, config, processes, network. Read-only only — a "
                        "command that would change the server is refused. One at a time; read each result before the "
                        "next. When you understand the issue, stop calling tools and write your findings."),
        "parameters": {"type": "object", "required": ["command"],
                       "properties": {"command": {"type": "string",
                                                  "description": "the PowerShell command, e.g. Get-Service -Name W3SVC"}}},
    },
}


def run_tool_for(os_family: str) -> dict:
    return RUN_TOOL_WIN if os_family == "windows" else RUN_TOOL


DIAGNOSE_SYSTEM = """You are an IT engineer logged in to a Linux server over SSH. {access}
Investigate the user's request by running READ-ONLY commands with run_command, one at a time, reading
each output before the next. Typical tools: systemctl status, journalctl, df -h, free -m, ss -tulpn,
ps aux, cat/tail of config and log files, ip a, docker ps/logs.

Rules:
- Read-only only. You cannot change anything on this server (commands that would are refused). If the fix
  needs a change, do NOT try to run it — describe it instead at the end.
- One command per step. Base the next command on what you just saw.
- Stop when you can answer the question or explain the issue (or confirm all is well). A question that only
  asks to see something (which projects, folders or services there are) gets the list, not a diagnosis. For a
  problem write a short report: what you checked, what you found, the likely cause, and the fix to apply (as
  commands the user can run).
- Be concrete and use the real values from the output. Do not invent.
- An exit code other than 0 means the command failed or found nothing (`systemctl status sshd` exit 4: there is
  no unit of that name — on Ubuntu it is `ssh`). Never report a failed command as a success.
- Never run the same command twice: its output will not change. Run the checks yourself; do not hand the user
  read-only commands to run.
- A question about earlier work in this chat (a deploy: "did we pull?", "why can't I see the update?") is about
  the project and branch named above: check its git state (`git -C <folder> log -1`, `git -C <folder> status`),
  its build and the app that serves it (pm2 list, systemctl status), not git servers or other folders.
- `git status` compares with the last fetch only, not with the remote now. Before saying a branch is up to date,
  compare `git -C <folder> rev-parse HEAD` with `git -C <folder> ls-remote origin <branch>` (read-only)."""

DIAGNOSE_SYSTEM_WIN = """You are an IT engineer logged in to a Windows server over SSH (PowerShell). {access}
Investigate the user's request by running READ-ONLY PowerShell commands with run_command, one at a time,
reading each output before the next. Typical cmdlets: Get-Service, Get-Process, Get-WinEvent / Get-EventLog,
Get-Disk / Get-Volume, Get-NetTCPConnection, Get-ChildItem, Get-Content, Test-Path, Test-Connection, plus
ipconfig, systeminfo, tasklist.

Rules:
- Read-only only. You cannot change anything (commands that would are refused). If the fix needs a change,
  do NOT run it — describe it instead at the end.
- One command per step. Base the next command on what you just saw.
- Use PowerShell cmdlets (Verb-Noun), not bash. Stop when you can explain the issue (or confirm all is well),
  then write a short report: what you checked, what you found, the likely cause, and the fix (as PowerShell
  the user can run).
- Be concrete and use the real values from the output. Do not invent."""


@dataclass
class DiagnoseResult:
    report: str
    runs: list[CommandRun] = field(default_factory=list)
    rounds: int = 0
    stopped: bool = False


# Each round sent every earlier output again: 12 commands → ~46k tokens in for one answer (ISSUES_LOG O52, O70).
KEEP_FULL_OUTPUTS = 3      # the latest outputs the model sees whole; it already read the older ones in their round
OLD_OUTPUT_CHARS = 800     # of an older output kept: its start (exit code, first lines), enough to recall what it showed


def _compact(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The conversation for the next round, with all but the last KEEP_FULL_OUTPUTS command outputs cut to their
    start. The stored conversation stays whole (the forced final report reads it all)."""
    outputs = [i for i, m in enumerate(messages) if m["role"] == "tool"]
    old = set(outputs[:-KEEP_FULL_OUTPUTS])
    return [{**m, "content": m["content"][:OLD_OUTPUT_CHARS] + "\n… (earlier output, shortened)"}
            if i in old and len(m["content"]) > OLD_OUTPUT_CHARS else m
            for i, m in enumerate(messages)]


def _assistant_call(text: str, command: str | None) -> dict[str, Any]:
    args = {"command": command} if command is not None else {}
    return {"role": "assistant", "content": text,
            "tool_calls": [{"function": {"name": "run_command", "arguments": args}}]}


REPEAT_LIMIT = 2            # repeated commands before the agent is told to write its findings (ISSUES_LOG O106)
REPEATED_NOTE = ("ALREADY RAN: this exact command ran in step {step}; its output does not change, so it was not "
                 "run again. Use a different command, or write your findings now.")


class _Seen:
    """The commands an agent ran this job: the 9B model ran `ls -la /var/lib/git/` 8 times in a row (O106)."""

    def __init__(self) -> None:
        self.steps: dict[str, int] = {}
        self.repeats = 0

    def repeated(self, command: str, step: int) -> str:
        """The note for a command already run (and the repeat counted), or "" for a new one (remembered)."""
        key = " ".join(command.split())
        if key in self.steps:
            self.repeats += 1
            return REPEATED_NOTE.format(step=self.steps[key])
        self.steps[key] = step
        return ""


def _context_line(context: str) -> str:
    """The chat's working context for the agent: use the known folder instead of searching the disk (O107)."""
    return (f"{context} Use that folder when the request is about this project; do not search the disk for it. "
            if context else "")


def _write_findings(client: Any, messages: list[dict[str, Any]], run_tool: dict, model: str) -> str:
    """No more commands: the findings from what was seen (the command limit, or the same command again)."""
    text, _ = client.chat_tools(
        messages=[*messages, {"role": "user", "content": "Stop running commands. Write your findings now "
                              "from what you have seen: what you checked, the likely cause, and the fix."}],
        tools=[run_tool], model=model, temperature=0.1)
    return text or "Could not finish diagnosing within the command limit."


def diagnose(
    login: ServerLogin,
    question: str,
    *,
    config: Config,
    model: str | None = None,
    should_continue: Callable[[], bool] = lambda: True,
    on_command: Callable[[CommandRun], None] = lambda run: None,
    context: str = "",
) -> DiagnoseResult:
    """The model runs read-only commands until it can answer `question`, then reports. Never changes the server.
    context: what the chat works on (chat_work.describe): the project folder, branch and last deploy (O106, O107)."""
    model = model or config.model_fast or config.llm_model
    session = open_session(login)
    access = _access_line(session.shell)
    prompt = DIAGNOSE_SYSTEM_WIN if login.os_family == "windows" else DIAGNOSE_SYSTEM
    run_tool = run_tool_for(login.os_family)
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": prompt.format(access=access)},
        {"role": "user", "content": f"Server: {login.shown}. {_context_line(context)}Request: {question}"},
    ]
    client = LLMClient(config)
    result = DiagnoseResult(report="")
    seen = _Seen()
    try:
        for round_no in range(1, MAX_ROUNDS + 1):
            result.rounds = round_no
            if not should_continue():
                result.stopped = True
                break
            text, calls = client.chat_tools(messages=_compact(messages), tools=[run_tool], model=model,
                                            temperature=0.1)
            if not calls:
                result.report = text
                break
            command = str((calls[0].get("arguments") or {}).get("command") or "").strip()
            if not command:
                messages.append(_assistant_call(text, None))
                messages.append({"role": "tool", "content": "No command given. Provide a command, or write your findings."})
                continue
            note = seen.repeated(command, round_no)
            if note:
                messages.append(_assistant_call(text, command))
                messages.append({"role": "tool", "content": note})
                if seen.repeats >= REPEAT_LIMIT:
                    result.report = _write_findings(client, messages, run_tool, model)
                    break
                continue
            run = session.run(command)
            on_command(run)
            result.runs.append(run)
            observation = (f"REFUSED: {run.refused}. This server is read-only; do not try to change it. "
                           "Use another read-only command, or write your findings." if run.refused
                           else run.observation)
            messages.append(_assistant_call(text, command))
            messages.append({"role": "tool", "content": observation})
        else:
            result.report = _write_findings(client, messages, run_tool, model)
    finally:
        release_session(session)
    return result


# ── as a chat job ──────────────────────────────────────────────────────────
def _commands_block(runs: list[CommandRun]) -> str:
    """The commands under a report, as Markdown: the chat does not render HTML such as <details> (O65)."""
    if not runs:
        return ""
    lines = "\n".join(f"- `{run.command}` — {run.outcome}" for run in runs)
    return f"\n\n**Commands run ({len(runs)})**\n\n{lines}"


def run_server_diagnose(
    query: str,
    *,
    job_id: str,
    config: Config,
    on_progress: Callable[[dict], None] | None = None,
    server: str | None = None,
    user_id: int | None = None,
    chat_work: str = "",
    **_: object,
) -> tuple[str, list[str], float]:
    """'log in to web01 and check why it is slow' → read-only investigation + findings. Never changes the server."""
    from src.jobs import live
    from src.servers import server_vault

    run = ToolRun(on_progress, query=query, job_id=job_id, role="Server diagnosis", model="diagnose_server", accent="#c45c5c")
    errors: list[str] = []

    status = run.status
    try:
        if not config.security_scan_enabled:
            raise AuditError("security tools are switched off (SECURITY_SCAN_ENABLED=false)")
        login, ask_first = server_vault.login_for(user_id, server, query)
        if login is None:
            output = ask_first
        else:
            status(f"Logging in to {login.shown}…")

            def on_command(run: CommandRun) -> None:
                status(("Refused: " if run.refused else "Ran: ") + run.command[:70])

            result = diagnose(login, query, config=config, should_continue=lambda: not live.is_stopped(job_id),
                              on_command=on_command, context=chat_work)
            note = "\n\n_Read-only diagnosis: the server was not changed._"
            output = (result.report or "No findings.") + _commands_block(result.runs) + note
    except (AuditError, VaultError) as exc:
        output = f"Not run: {exc}."
        errors.append(str(exc))
    except Exception as exc:  # noqa: BLE001 — report, never crash the worker; type only (no secrets in messages)
        logger.error("server diagnose failed: %s", type(exc).__name__)
        output = f"The diagnosis failed ({type(exc).__name__})."
        errors.append(type(exc).__name__)
    return run.finish(output, errors)


# ── operate: changes allowed, gated by the server's mode (SERVER_ACCESS_DECISION.md §8) ────────────
# Shared by the Linux and Windows prompts (ISSUES_LOG O70): asked "if you run sudo systemctl restart nginx", the model
# ran 12 checks, called the restart "not necessary" and printed it instead; it said "Nginx is serving the static
# files" (the app ran under PM2) and called `ls -la` dates "created". No braces: the prompts go through .format().
OPERATE_RULES = """- A command or change the user asks for, run it: this server's mode decides, and the user approves or cancels.
  Never skip it because you think it is not needed; say that in your report instead, after running it.
- Report only what the command output showed. Do not guess how things are set up beyond it (which program serves a
  site, what a date means: `ls -l` shows when a file last changed, not when it was created).
- For a status or "did it work" question, stop as soon as the outputs answer it."""

OPERATE_SYSTEM = """You are an IT engineer working on a Linux server over SSH. {access}
Do the user's task with run_command, one command at a time, reading each output before the next.
You MAY change the server (install, edit config, restart services) when the task needs it.

How changes are handled on this server: {mode_note}
Rules:
- Read-only checks run freely. A change may need the user's approval first (you will see the result).
- Every command starts in a new shell in the home folder: a `cd` does not carry to the next command. To work in a
  folder, put both in one command: `cd /var/www/app && npm run build`.
- If a change is refused or cancelled, do not retry it blindly — explain and stop, or try another way.
- Make the smallest change that does the job. Check your work afterwards (e.g. systemctl status).
""" + OPERATE_RULES + """
- At the end, write what you did, what changed, and how to verify or undo it.
- Use real values from the output. Never invent."""

OPERATE_SYSTEM_WIN = """You are an IT engineer working on a Windows server over SSH (PowerShell). {access}
Do the user's task with run_command, one PowerShell command at a time, reading each output before the next.
You MAY change the server (create files, edit config, install features, restart services) when the task needs it.

How changes are handled on this server: {mode_note}
Rules:
- Read-only checks run freely. A change may need the user's approval first (you will see the result).
- Use PowerShell cmdlets (Verb-Noun): New-Item, Set-Content, Copy-Item, Restart-Service, Install-WindowsFeature,
  etc. Do NOT use Invoke-Expression, download-and-run, or bash — they are blocked.
- Every command starts in a new session in the home folder: Set-Location does not carry to the next command, so
  use full paths (C:\\inetpub\\site\\index.html).
- If a change is refused or cancelled, do not retry it blindly — explain and stop, or try another way.
- Make the smallest change that does the job. Check your work afterwards (e.g. Get-Service, Test-Path).
""" + OPERATE_RULES + """
- At the end, write what you did, what changed, and how to verify or undo it. Use real values; never invent."""

MODE_NOTE = {
    "audit": "this server is READ-ONLY (audit mode) — every change is refused. Diagnose and describe the fix only.",
    "confirm": "each changing command is shown to the user, who clicks Run or Cancel before it executes.",
    "trusted": "changing commands run automatically (this server is trusted); truly destructive ones are still blocked.",
}
CONFIRM_TIMEOUT = 300       # seconds to wait for the user's click before treating a change as cancelled


@dataclass
class OperateResult:
    report: str
    runs: list[CommandRun] = field(default_factory=list)
    changes: int = 0            # changing commands that actually ran
    rounds: int = 0
    stopped: bool = False


def operate(
    login: ServerLogin,
    task: str,
    *,
    config: Config,
    user_id: int | None = None,
    job_id: str | None = None,
    model: str | None = None,
    should_continue: Callable[[], bool] = lambda: True,
    confirm: Callable[[str, str], bool | None] = lambda command_id, command: False,
    on_command: Callable[[CommandRun], None] = lambda run: None,
    context: str = "",
) -> OperateResult:
    """The model does `task`, running read-only commands freely and changing commands through the server's
    trust gate (audit → refused, confirm → the `confirm` callback, trusted → auto). Every change is logged."""
    from src.servers.command_audit import log_command

    model = model or config.model_fast or config.llm_model
    session = open_session(login)
    access = _access_line(session.shell)
    prompt = OPERATE_SYSTEM_WIN if login.os_family == "windows" else OPERATE_SYSTEM
    run_tool = run_tool_for(login.os_family)
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": prompt.format(access=access, mode_note=MODE_NOTE[login.mode])},
        {"role": "user", "content": f"Server: {login.shown}. {_context_line(context)}Task: {task}"},
    ]
    client = LLMClient(config)
    result = OperateResult(report="")
    seen = _Seen()

    def audit(command: str, decision: str, run: CommandRun | None) -> None:
        log_command(config=config, user_id=user_id, job_id=job_id, server_label=login.label, host=login.host,
                    command=command, decision=decision,
                    exit_code=run.code if run else None, output=run.output if run else None)

    try:
        for round_no in range(1, MAX_ROUNDS + 1):
            result.rounds = round_no
            if not should_continue():
                result.stopped = True
                break
            text, calls = client.chat_tools(messages=_compact(messages), tools=[run_tool], model=model,
                                            temperature=0.1)
            if not calls:
                result.report = text
                break
            command = str((calls[0].get("arguments") or {}).get("command") or "").strip()
            if not command:
                messages.append(_assistant_call(text, None))
                messages.append({"role": "tool", "content": "No command given. Provide a command, or write your findings."})
                continue

            if classify_for(command, login.os_family) is None:
                note = seen.repeated(command, round_no)     # a read-only repeat (a change may be retried)
                if note:
                    messages.append(_assistant_call(text, command))
                    messages.append({"role": "tool", "content": note})
                    continue
                run = session.execute(command)              # read-only: runs freely
                observation = run.observation
            else:
                observation, run = _handle_change(session, command, login, confirm, audit, result)
            run_for_ui = run or CommandRun(command=command, refused=observation.split(".")[0])
            on_command(run_for_ui)
            result.runs.append(run_for_ui)
            messages.append(_assistant_call(text, command))
            messages.append({"role": "tool", "content": observation})
        else:
            text, _ = client.chat_tools(
                messages=[*messages, {"role": "user", "content": "Stop now. Write what you did, what changed, "
                                      "and how to verify or undo it."}],
                tools=[run_tool], model=model, temperature=0.1)
            result.report = text or "Reached the command limit."
    finally:
        release_session(session)
    return result


def _handle_change(session, command, login, confirm, audit, result) -> tuple[str, CommandRun | None]:
    """A changing command: deny-list → mode gate → run or refuse. Returns (observation for the model, run)."""
    import uuid as _uuid

    deny = destructive_for(command, login.os_family)
    if deny:
        audit(command, "refused", None)
        return (f"REFUSED: this command {deny}; it is blocked for safety and will not run. Do not retry it.", None)
    if login.mode == "audit":
        return ("This server is read-only (audit mode). I cannot change it. Describe the change for the user "
                "to run, or ask them to switch this server to 'confirm' in Settings → Devices.", None)
    if login.mode == "trusted":
        run = session.execute(command)
        audit(command, "auto", run)
        result.changes += 1
        return (run.observation, run)
    # confirm mode
    approved = confirm(_uuid.uuid4().hex[:12], command)
    if approved is None:
        audit(command, "cancelled", None)
        return ("No answer within the time limit — the command was NOT run. Explain and stop.", None)
    if not approved:
        audit(command, "cancelled", None)
        return ("The user CANCELLED this command — it was not run. Do not retry it; try another approach or stop.", None)
    run = session.execute(command)
    audit(command, "confirmed", run)
    result.changes += 1
    return (run.observation, run)


def run_server_operate(
    query: str,
    *,
    job_id: str,
    config: Config,
    on_progress: Callable[[dict], None] | None = None,
    server: str | None = None,
    user_id: int | None = None,
    chat_work: str = "",
    **_: object,
) -> tuple[str, list[str], float]:
    """'log in to web01 and restart nginx' / 'run deploy.sh on web01' → the agent does the work, changing
    commands gated by the server's mode (audit/confirm/trusted). Every change is logged."""
    from src.jobs import live
    from src.servers import server_vault

    emit = on_progress or (lambda event: None)
    run = ToolRun(on_progress, query=query, job_id=job_id, role="Server operate", model="operate_server", accent="#c45c5c")
    errors: list[str] = []

    status = run.status
    def confirm(command_id: str, command: str) -> bool | None:
        emit({"type": "confirm_command", "agent_id": "assistant", "command_id": command_id, "command": command,
              "server": login.shown})
        status("Waiting for you to approve a command…")
        approved = live.wait_confirm(job_id, command_id, CONFIRM_TIMEOUT)
        # saved with the job's events, so a reloaded chat shows the card answered, not with live buttons (O66)
        emit({"type": "confirm_resolved", "agent_id": "assistant", "command_id": command_id,
              "answer": "timeout" if approved is None else "run" if approved else "cancel"})
        return approved

    try:
        if not config.security_scan_enabled:
            raise AuditError("security tools are switched off (SECURITY_SCAN_ENABLED=false)")
        login, ask_first = server_vault.login_for(user_id, server, query)
        if login is None:
            output = ask_first
        else:
            status(f"Logging in to {login.shown}…")

            def on_command(run: CommandRun) -> None:
                status(("Refused: " if run.refused else "Ran: ") + run.command[:70])

            result = operate(login, query, config=config, user_id=user_id, job_id=job_id,
                             should_continue=lambda: not live.is_stopped(job_id), confirm=confirm,
                             on_command=on_command, context=chat_work)
            note = (f"\n\n_Operated on **{login.label}** ({login.mode} mode): {result.changes} change(s) made. "
                    "Every change is in the command log._")
            output = (result.report or "Done.") + _commands_block(result.runs) + note
    except (AuditError, VaultError) as exc:
        output = f"Not run: {exc}."
        errors.append(str(exc))
    except Exception as exc:  # noqa: BLE001 — report, never crash the worker; type only (no secrets in messages)
        logger.error("server operate failed: %s", type(exc).__name__)
        output = f"The operation failed ({type(exc).__name__})."
        errors.append(type(exc).__name__)
    return run.finish(output, errors)
