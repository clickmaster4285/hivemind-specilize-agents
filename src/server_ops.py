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

import logging
import re
import shlex
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from src.config import Config
from src.linux_audit import AuditError, Shell, connect
from src.llm_client import LLMClient
from src.server_vault import ServerLogin, VaultError

logger = logging.getLogger(__name__)

MAX_ROUNDS = 12                 # how many commands the agent may run per question
MAX_OUTPUT_CHARS = 4000         # of one command's output kept for the model

# Programs that only read. A command may only run if EVERY program in it is here (classify_command).
READ_ONLY: frozenset[str] = frozenset({
    # files / text
    "cat", "head", "tail", "less", "more", "ls", "ll", "dir", "stat", "file", "wc", "grep", "egrep",
    "fgrep", "zgrep", "zcat", "cut", "sort", "uniq", "tr", "awk", "nl", "tac", "readlink", "realpath",
    "basename", "dirname", "find", "locate", "md5sum", "sha256sum", "cmp", "diff", "column", "fold",
    "strings", "xxd", "hexdump", "od", "pwd", "echo", "printf", "true", "false", "test", "seq", "jq", "base64",
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
                      "shortlog", "blame", "ls-files", "tag", "reflog"}),
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
_OPERATORS = re.compile(r"\|\||&&|\||;|\n")
_SUBSHELL = re.compile(r"\$\([^)]*\)|`[^`]*`|<\([^)]*\)")
# a > or >> that writes to a file (2>&1, 2>/dev/null, >/dev/null are cleared before this runs)
_REDIRECT = re.compile(r">>?")


class CommandRefused(ValueError):
    """The command is not read-only (this server is in Audit mode)."""


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


def _check_simple(command: str) -> str | None:
    """A single command (no pipes/operators): reason it is NOT read-only, or None if it is."""
    command = command.strip()
    if not command:
        return None
    cleaned = command.replace("2>&1", " ").replace("2>/dev/null", " ").replace(">/dev/null", " ")
    if _REDIRECT.search(cleaned):
        return "it writes to a file (>)"
    try:
        tokens = shlex.split(command, comments=True)
    except ValueError:
        return "it could not be parsed safely"
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
    for part in _OPERATORS.split(_SUBSHELL.sub(" echo INNER ", command)):
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


class RemoteSession:
    """A logged-in SSH connection. `run` enforces read-only; `execute` runs anything (used after approval)."""

    def __init__(self, login: ServerLogin):
        self.login = login
        self.client, _ = connect(login)
        self.shell = Shell(self.client, password=login.password)
        self.shell.detect_sudo()
        self.runs: list[CommandRun] = []

    def _reconnect(self) -> None:
        """A long operate/diagnose session can be dropped by the server (idle, MaxStartups). Log back in
        so the next command still runs instead of failing the whole operation."""
        try:
            self.client.close()
        except Exception:  # noqa: BLE001 — already broken; closing is best-effort
            pass
        logger.info("server_ops reconnecting to %s after a dropped session", self.login.host)
        self.client, _ = connect(self.login)
        self.shell = Shell(self.client, password=self.login.password)
        self.shell.detect_sudo()

    def _raw(self, command: str) -> tuple[int, str, str]:
        import paramiko

        try:
            return self.shell.raw(command)
        except paramiko.SSHException:
            self._reconnect()                 # one retry on a dropped session
            return self.shell.raw(command)

    def execute(self, command: str) -> CommandRun:
        """Run the command with no read-only check (the caller has already decided it may run)."""
        code, out, err = self._raw(command)
        text = (out + (("\n" + err) if err.strip() else "")).strip()
        if len(text) > MAX_OUTPUT_CHARS:
            text = text[:MAX_OUTPUT_CHARS] + f"\n… (output cut at {MAX_OUTPUT_CHARS} characters)"
        run = CommandRun(command=command, code=code, output=text or "(no output)")
        self.runs.append(run)
        return run

    def run(self, command: str) -> CommandRun:
        """Read-only only (diagnose): a changing command is refused, never sent to the server."""
        reason = classify_command(command)
        if reason:
            run = CommandRun(command=command, refused=reason)
            self.runs.append(run)
            logger.info("server_ops refused: %s (%s)", command, reason)
            return run
        return self.execute(command)

    def close(self) -> None:
        self.client.close()


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

DIAGNOSE_SYSTEM = """You are an IT engineer logged in to a Linux server over SSH. {access}
Investigate the user's request by running READ-ONLY commands with run_command, one at a time, reading
each output before the next. Typical tools: systemctl status, journalctl, df -h, free -m, ss -tulpn,
ps aux, cat/tail of config and log files, ip a, docker ps/logs.

Rules:
- Read-only only. You cannot change anything on this server (commands that would are refused). If the fix
  needs a change, do NOT try to run it — describe it instead at the end.
- One command per step. Base the next command on what you just saw.
- Stop when you can explain the issue (or confirm all is well). Then write a short report:
  what you checked, what you found, the likely cause, and the fix to apply (as commands the user can run).
- Be concrete and use the real values from the output. Do not invent."""


@dataclass
class DiagnoseResult:
    report: str
    runs: list[CommandRun] = field(default_factory=list)
    rounds: int = 0
    stopped: bool = False


def _assistant_call(text: str, command: str | None) -> dict[str, Any]:
    args = {"command": command} if command is not None else {}
    return {"role": "assistant", "content": text,
            "tool_calls": [{"function": {"name": "run_command", "arguments": args}}]}


def diagnose(
    login: ServerLogin,
    question: str,
    *,
    config: Config,
    model: str | None = None,
    should_continue: Callable[[], bool] = lambda: True,
    on_command: Callable[[CommandRun], None] = lambda run: None,
) -> DiagnoseResult:
    """The model runs read-only commands until it can answer `question`, then reports. Never changes the server."""
    model = model or config.model_fast or config.llm_model
    session = RemoteSession(login)
    access = "You have root." if session.shell.root else f"You run as a normal user (sudo: {session.shell.sudo})."
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": DIAGNOSE_SYSTEM.format(access=access)},
        {"role": "user", "content": f"Server: {login.label} ({login.host}). Request: {question}"},
    ]
    client = LLMClient(config)
    result = DiagnoseResult(report="")
    try:
        for round_no in range(1, MAX_ROUNDS + 1):
            result.rounds = round_no
            if not should_continue():
                result.stopped = True
                break
            text, calls = client.chat_tools(messages=messages, tools=[RUN_TOOL], model=model, temperature=0.1)
            if not calls:
                result.report = text
                break
            command = str((calls[0].get("arguments") or {}).get("command") or "").strip()
            if not command:
                messages.append(_assistant_call(text, None))
                messages.append({"role": "tool", "content": "No command given. Provide a command, or write your findings."})
                continue
            run = session.run(command)
            on_command(run)
            result.runs.append(run)
            observation = (f"REFUSED: {run.refused}. This server is read-only; do not try to change it. "
                           "Use another read-only command, or write your findings." if run.refused
                           else f"exit {run.code}\n{run.output}")
            messages.append(_assistant_call(text, command))
            messages.append({"role": "tool", "content": observation})
        else:
            text, _ = client.chat_tools(
                messages=[*messages, {"role": "user", "content": "Stop running commands. Write your findings now "
                                      "from what you have seen: what you checked, the likely cause, and the fix."}],
                tools=[RUN_TOOL], model=model, temperature=0.1)
            result.report = text or "Could not finish diagnosing within the command limit."
    finally:
        session.close()
    return result


# ── as a chat job ──────────────────────────────────────────────────────────
def _commands_block(runs: list[CommandRun]) -> str:
    return "\n".join(f"- `{run.command}` — {'refused' if run.refused else f'exit {run.code}'}" for run in runs)


def run_server_diagnose(
    query: str,
    *,
    job_id: str,
    config: Config,
    on_progress: Callable[[dict], None] | None = None,
    server: str | None = None,
    user_id: int | None = None,
    **_: object,
) -> tuple[str, list[str], float]:
    """'log in to web01 and check why it is slow' → read-only investigation + findings. Never changes the server."""
    from src import live, server_vault
    from src.linux_audit import _server_in

    emit = on_progress or (lambda event: None)
    agent = {"agent_id": "assistant", "name": "Assistant", "role": "Server diagnosis", "stage": "chat",
             "accent": "#c45c5c", "job_id": job_id}
    started = time.monotonic()
    errors: list[str] = []
    emit({"type": "session_start", "mode": "chat", "query": query})
    emit({"type": "agent_start", **agent, "model": "diagnose_server"})

    def status(message: str) -> None:
        emit({"type": "status", "agent_id": "assistant", "message": message})

    try:
        if not config.security_scan_enabled:
            raise AuditError("security tools are switched off (SECURITY_SCAN_ENABLED=false)")
        servers = server_vault.list_servers(user_id) if user_id is not None else []
        login = server_vault.find(user_id, server or _server_in(query, servers)) if user_id is not None else None
        if login is None and not servers:
            output = ("No server login for this session yet. Add the server in **Settings → Servers** (address, user, "
                      "password or key) — it stays in memory for this session only, never in the chat.")
        elif login is None:
            output = f"Which server? Added this session: {', '.join(f'{s.label} ({s.host})' for s in servers)}."
        else:
            status(f"Logging in to {login.host}…")

            def on_command(run: CommandRun) -> None:
                status(("Refused: " if run.refused else "Ran: ") + run.command[:70])

            result = diagnose(login, query, config=config, should_continue=lambda: not live.is_stopped(job_id),
                              on_command=on_command)
            note = "\n\n_Read-only diagnosis: the server was not changed._"
            commands = (f"\n\n<details><summary>Commands run ({len(result.runs)})</summary>\n\n"
                        f"{_commands_block(result.runs)}\n</details>") if result.runs else ""
            output = (result.report or "No findings.") + commands + note
    except (AuditError, VaultError) as exc:
        output = f"Not run: {exc}."
        errors.append(str(exc))
    except Exception as exc:  # noqa: BLE001 — report, never crash the worker; type only (no secrets in messages)
        logger.error("server diagnose failed: %s", type(exc).__name__)
        output = f"The diagnosis failed ({type(exc).__name__})."
        errors.append(type(exc).__name__)
    elapsed = time.monotonic() - started
    emit({"type": "agent_done", **agent, "elapsed_seconds": elapsed, "output": output})
    emit({"type": "session_done", "total_seconds": elapsed, "errors": errors, "mode": "chat"})
    return output, errors, elapsed


# ── operate: changes allowed, gated by the server's mode (SERVER_ACCESS_DECISION.md §8) ────────────
OPERATE_SYSTEM = """You are an IT engineer working on a Linux server over SSH. {access}
Do the user's task with run_command, one command at a time, reading each output before the next.
You MAY change the server (install, edit config, restart services) when the task needs it.

How changes are handled on this server: {mode_note}
Rules:
- Read-only checks run freely. A change may need the user's approval first (you will see the result).
- If a change is refused or cancelled, do not retry it blindly — explain and stop, or try another way.
- Make the smallest change that does the job. Check your work afterwards (e.g. systemctl status).
- At the end, write what you did, what changed, and how to verify or undo it.
- Use real values from the output. Never invent."""

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
) -> OperateResult:
    """The model does `task`, running read-only commands freely and changing commands through the server's
    trust gate (audit → refused, confirm → the `confirm` callback, trusted → auto). Every change is logged."""
    from src.command_audit import log_command

    model = model or config.model_fast or config.llm_model
    session = RemoteSession(login)
    access = "You have root." if session.shell.root else f"You run as a normal user (sudo: {session.shell.sudo})."
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": OPERATE_SYSTEM.format(access=access, mode_note=MODE_NOTE[login.mode])},
        {"role": "user", "content": f"Server: {login.label} ({login.host}). Task: {task}"},
    ]
    client = LLMClient(config)
    result = OperateResult(report="")

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
            text, calls = client.chat_tools(messages=messages, tools=[RUN_TOOL], model=model, temperature=0.1)
            if not calls:
                result.report = text
                break
            command = str((calls[0].get("arguments") or {}).get("command") or "").strip()
            if not command:
                messages.append(_assistant_call(text, None))
                messages.append({"role": "tool", "content": "No command given. Provide a command, or write your findings."})
                continue

            if classify_command(command) is None:
                run = session.execute(command)              # read-only: runs freely
                observation = f"exit {run.code}\n{run.output}"
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
                tools=[RUN_TOOL], model=model, temperature=0.1)
            result.report = text or "Reached the command limit."
    finally:
        session.close()
    return result


def _handle_change(session, command, login, confirm, audit, result) -> tuple[str, CommandRun | None]:
    """A changing command: deny-list → mode gate → run or refuse. Returns (observation for the model, run)."""
    import uuid as _uuid

    deny = is_destructive(command)
    if deny:
        audit(command, "refused", None)
        return (f"REFUSED: this command {deny}; it is blocked for safety and will not run. Do not retry it.", None)
    if login.mode == "audit":
        return ("This server is read-only (audit mode). I cannot change it. Describe the change for the user "
                "to run, or ask them to switch this server to 'confirm' in Settings → Servers.", None)
    if login.mode == "trusted":
        run = session.execute(command)
        audit(command, "auto", run)
        result.changes += 1
        return (f"exit {run.code}\n{run.output}", run)
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
    return (f"exit {run.code}\n{run.output}", run)


def run_server_operate(
    query: str,
    *,
    job_id: str,
    config: Config,
    on_progress: Callable[[dict], None] | None = None,
    server: str | None = None,
    user_id: int | None = None,
    **_: object,
) -> tuple[str, list[str], float]:
    """'log in to web01 and restart nginx' / 'run deploy.sh on web01' → the agent does the work, changing
    commands gated by the server's mode (audit/confirm/trusted). Every change is logged."""
    from src import live, server_vault
    from src.linux_audit import _server_in

    emit = on_progress or (lambda event: None)
    agent = {"agent_id": "assistant", "name": "Assistant", "role": "Server operate", "stage": "chat",
             "accent": "#c45c5c", "job_id": job_id}
    started = time.monotonic()
    errors: list[str] = []
    emit({"type": "session_start", "mode": "chat", "query": query})
    emit({"type": "agent_start", **agent, "model": "operate_server"})

    def status(message: str) -> None:
        emit({"type": "status", "agent_id": "assistant", "message": message})

    def confirm(command_id: str, command: str) -> bool | None:
        emit({"type": "confirm_command", "agent_id": "assistant", "command_id": command_id, "command": command,
              "server": login.label, "host": login.host})
        status("Waiting for you to approve a command…")
        return live.wait_confirm(job_id, command_id, CONFIRM_TIMEOUT)

    try:
        if not config.security_scan_enabled:
            raise AuditError("security tools are switched off (SECURITY_SCAN_ENABLED=false)")
        servers = server_vault.list_servers(user_id) if user_id is not None else []
        login = server_vault.find(user_id, server or _server_in(query, servers)) if user_id is not None else None
        if login is None and not servers:
            output = ("No server login for this session yet. Add the server in **Settings → Servers** (address, user, "
                      "password or key, and its mode) — it stays in memory for this session only, never in the chat.")
        elif login is None:
            output = f"Which server? Added this session: {', '.join(f'{s.label} ({s.host})' for s in servers)}."
        else:
            status(f"Logging in to {login.host}…")

            def on_command(run: CommandRun) -> None:
                status(("Refused: " if run.refused else "Ran: ") + run.command[:70])

            result = operate(login, query, config=config, user_id=user_id, job_id=job_id,
                             should_continue=lambda: not live.is_stopped(job_id), confirm=confirm,
                             on_command=on_command)
            note = (f"\n\n_Operated on **{login.label}** ({login.mode} mode): {result.changes} change(s) made. "
                    "Every change is in the command log._")
            commands = (f"\n\n<details><summary>Commands ({len(result.runs)})</summary>\n\n"
                        f"{_commands_block(result.runs)}\n</details>") if result.runs else ""
            output = (result.report or "Done.") + commands + note
    except (AuditError, VaultError) as exc:
        output = f"Not run: {exc}."
        errors.append(str(exc))
    except Exception as exc:  # noqa: BLE001 — report, never crash the worker; type only (no secrets in messages)
        logger.error("server operate failed: %s", type(exc).__name__)
        output = f"The operation failed ({type(exc).__name__})."
        errors.append(type(exc).__name__)
    elapsed = time.monotonic() - started
    emit({"type": "agent_done", **agent, "elapsed_seconds": elapsed, "output": output})
    emit({"type": "session_done", "total_seconds": elapsed, "errors": errors, "mode": "chat"})
    return output, errors, elapsed
