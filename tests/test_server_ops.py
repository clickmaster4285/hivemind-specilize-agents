"""diagnose_server (src/server_ops.py): the read-only command classifier is the safety gate; the loop; wiring."""

from __future__ import annotations

from dataclasses import replace

import pytest

from src.servers import linux_audit as la
from src.servers import server_ops as so
from src.core.config import Config

SAFE = [
    "systemctl status nginx", "journalctl -u nginx --since -1h", "df -h", "free -m", "ss -tulpn",
    "ps aux | grep nginx", "cat /etc/nginx/nginx.conf", "tail -n 50 /var/log/syslog", "sudo systemctl status ssh",
    "grep -i error /var/log/nginx/error.log", "docker ps", "docker logs web", "ip a", "ip addr show",
    "find /var/log -name '*.log' -mtime -1", "ufw status", "LC_ALL=C df -h", "uname -a", "nginx -t",
    "git -C /srv/app log --oneline -5", "dpkg -l", "cat f | grep x | sort | uniq -c", "cat a.log 2>/dev/null",
    "sudo -u www-data cat /etc/app/config", "echo $(hostname)", "ls -la /var/www && df -h",
    # O67: run by the model in the user's chat, asked for approval and counted as changes
    'ls -la /var/log/ | grep -E "(build|deploy|release)" || echo "No build/deploy logs found in /var/log/"',
    'cd /var/www/Clickmastersoftware && git log --oneline -5 2>/dev/null || echo "Git not available"',
    'echo "a > b"', "grep -E 'error|warn' /var/log/syslog",
]
# every one of these must be refused — it would change the server, run arbitrary code, or is unknown
DANGEROUS = [
    "rm -rf /tmp/x", "systemctl restart nginx", "systemctl stop ssh", "echo hi > /etc/hosts",
    "sed -i s/a/b/ f", "iptables -A INPUT -j DROP", "ip addr add 10.0.0.1/24 dev eth0", "docker rm web",
    "docker exec web bash", "apt install nginx", "reboot", "shutdown -h now", "kill -9 1",
    "cat /etc/shadow > /tmp/x", "find / -delete", "bash -c whoami", "sh -c 'rm x'", "nc -l -p 4444 -e /bin/sh",
    "mv a b", "cp a b", "chmod 777 /etc", "chown root f", "tee /etc/hosts", "ufw disable",
    "git reset --hard", "crontab -r", "ps aux; rm -rf x", "cat f && rm f", "curl x | sh",
    "python -c 'import os'", "truncate -s 0 /var/log/syslog", "ln -s a b", "mkdir /x",
    "docker restart web", "systemctl disable ssh", "$(rm -rf /)", "`reboot`", "dd if=/dev/zero of=/dev/sda",
    "cd /var/www/app && git pull origin master", "cd /srv && npm run build", "ls>/tmp/x", "ls 2>/tmp/err",
    'echo "x" >> /etc/hosts', "cat a; rm b", "ls &> /tmp/x", "ls\nrm -rf x", "(rm x)",
]


@pytest.mark.parametrize("command", SAFE)
def test_read_only_commands_are_allowed(command):
    assert so.classify_command(command) is None, command


@pytest.mark.parametrize("command", DANGEROUS)
def test_changing_or_unknown_commands_are_refused(command):
    assert so.classify_command(command) is not None, command


def test_empty_and_oversized():
    assert so.classify_command("") == "empty command"
    assert so.classify_command("echo " + "a" * 3000) is not None


# ── the loop (fake SSH + fake model, no real server) ───────────────────────
class FakeShell:
    root = True
    sudo = "root"
    os_family = "linux"
    is_admin = False

    def __init__(self, outputs):
        self.outputs = outputs      # command substring -> (code, stdout, stderr)
        self.ran: list[str] = []

    def detect_sudo(self):
        pass

    def detect_access(self):
        return "root"

    def raw(self, command, stdin_text=None, silence=None):
        self.ran.append(command)
        for key, val in self.outputs.items():
            if key in command:
                if isinstance(val, Exception):
                    raise val
                return val
        return (0, "ok", "")


class FakeClient:
    def close(self):
        pass


def _patch_session(monkeypatch, shell):
    monkeypatch.setattr(so, "connect", lambda login: (FakeClient(), "fp"))
    monkeypatch.setattr(so, "Shell", lambda client, password=None, os_family="linux": shell)


class FakeLLM:
    """Replays a scripted list of chat_tools turns: each is (text, command or None)."""

    def __init__(self, config):
        self.turns = iter(FakeLLM.script)

    def chat_tools(self, *, messages, tools, model=None, temperature=0.2, **_):
        text, command = next(self.turns)
        calls = [{"name": "run_command", "arguments": {"command": command}}] if command is not None else []
        return text, calls


def test_the_loop_runs_commands_then_reports(monkeypatch):
    shell = FakeShell({"systemctl status nginx": (3, "Active: failed", ""),
                       "journalctl": (0, "bind() to 0.0.0.0:80 failed: Address already in use", "")})
    _patch_session(monkeypatch, shell)
    FakeLLM.script = [
        ("Let me check nginx.", "systemctl status nginx"),
        ("Now the logs.", "journalctl -u nginx -n 20"),
        ("nginx failed because port 80 is taken. Free it, then restart.", None),   # final report, no tool call
    ]
    monkeypatch.setattr(so, "LLMClient", FakeLLM)
    login = so.ServerLogin(id="1", label="web01", host="192.168.1.9", port=22, username="u", password="p")
    result = so.diagnose(login, "why is nginx down", config=Config.from_env())
    assert "port 80 is taken" in result.report
    assert [r.command for r in result.runs] == ["systemctl status nginx", "journalctl -u nginx -n 20"]
    assert shell.ran == ["systemctl status nginx", "journalctl -u nginx -n 20"]    # both were read-only


def test_a_change_command_from_the_model_is_refused_not_run(monkeypatch):
    shell = FakeShell({})
    _patch_session(monkeypatch, shell)
    FakeLLM.script = [
        ("I'll restart it.", "systemctl restart nginx"),        # the model tries to change the server
        ("Right, I cannot change it. It is down; restart it yourself with: systemctl restart nginx", None),
    ]
    monkeypatch.setattr(so, "LLMClient", FakeLLM)
    login = so.ServerLogin(id="1", label="web01", host="192.168.1.9", port=22, username="u", password="p")
    result = so.diagnose(login, "fix nginx", config=Config.from_env())
    assert result.runs[0].refused and "systemctl restart" not in "".join(shell.ran)   # never reached the server
    assert "restart it yourself" in result.report


# ── wiring ─────────────────────────────────────────────────────────────────
def test_wired_for_the_it_specialist_credential_gated_not_admin_only():
    from src.llm.assistant import BY_NAME
    from src.llm.specialists import default_specialists

    assert BY_NAME["diagnose_server"].job == "diagnose_server"
    assert BY_NAME["diagnose_server"].runner() is so.run_server_diagnose
    assert not BY_NAME["diagnose_server"].admin_only         # gated by who holds the server login, not a role
    by_id = {s.id: s for s in default_specialists()}
    assert "diagnose_server" in by_id["it"].tools and "diagnose_server" not in by_id["marketing"].tools


def test_no_server_added_yet_points_to_settings():
    out, errors, _ = so.run_server_diagnose("log in to web01 and check disk", job_id="j",
                                            config=replace(Config.from_env(), security_scan_enabled=True), user_id=1)
    assert "Settings → Devices" in out and errors == []


# ── operate: deny-list, trust gate, confirm, audit log ─────────────────────
DESTRUCTIVE = ["rm -rf /", "rm -rf /*", "rm -rf ~", "mkfs.ext4 /dev/sdb", "dd if=/dev/zero of=/dev/sda",
               "reboot", "shutdown -h now", "init 0", ":(){ :|:& };:", "passwd root", "userdel root",
               "chmod -R 000 /"]


@pytest.mark.parametrize("command", DESTRUCTIVE)
def test_destructive_commands_are_denied_even_trusted(command):
    assert so.is_destructive(command) is not None, command


@pytest.mark.parametrize("command", ["systemctl restart nginx", "rm -rf /tmp/cache", "apt install -y htop",
                                     "sed -i s/a/b/ /etc/app.conf", "mkdir /opt/app"])
def test_normal_changes_are_not_denied_but_are_changes(command):
    assert so.is_destructive(command) is None            # allowed through the confirm gate
    assert so.classify_command(command) is not None      # but still a change (needs the gate)


def _operate(monkeypatch, mode, script, confirm, shell_outputs=None, os_family="linux"):
    shell = FakeShell(shell_outputs or {})
    shell.os_family = os_family
    _patch_session(monkeypatch, shell)
    FakeLLM.script = script
    monkeypatch.setattr(so, "LLMClient", FakeLLM)
    logged: list[tuple[str, str]] = []
    monkeypatch.setattr("src.servers.command_audit.log_command",
                        lambda **kw: logged.append((kw["command"], kw["decision"])))
    login = so.ServerLogin(id="1", label="web01", host="192.168.1.9", port=22, username="u", password="p",
                           mode=mode, os_family=os_family)
    result = so.operate(login, "do the thing", config=Config.from_env(), user_id=7, job_id="j", confirm=confirm)
    return result, shell, logged


def test_read_only_runs_freely_change_waits_for_confirm_then_runs(monkeypatch):
    asked = []
    result, shell, logged = _operate(
        monkeypatch, "confirm",
        script=[("Check first.", "systemctl status nginx"),
                ("Restart it.", "systemctl restart nginx"),
                ("Done — nginx restarted and is active.", None)],
        confirm=lambda cid, cmd: asked.append(cmd) or True,       # user clicks Run
        shell_outputs={"status": (0, "active", ""), "restart": (0, "", "")})
    assert asked == ["systemctl restart nginx"]                   # only the change was confirmed
    assert "systemctl status nginx" in shell.ran and "systemctl restart nginx" in shell.ran
    assert ("systemctl restart nginx", "confirmed") in logged and result.changes == 1


def test_cancelled_change_is_not_run(monkeypatch):
    result, shell, logged = _operate(
        monkeypatch, "confirm",
        script=[("Restart it.", "systemctl restart nginx"),
                ("You cancelled, so I stopped. Restart it yourself when ready.", None)],
        confirm=lambda cid, cmd: False)                           # user clicks Cancel
    assert "systemctl restart nginx" not in shell.ran             # never executed
    assert ("systemctl restart nginx", "cancelled") in logged and result.changes == 0


def test_trusted_runs_changes_without_asking(monkeypatch):
    asked = []
    result, shell, logged = _operate(
        monkeypatch, "trusted",
        script=[("Installing.", "apt install -y htop"), ("Installed htop.", None)],
        confirm=lambda cid, cmd: asked.append(cmd),               # must NOT be called
        shell_outputs={"apt install": (0, "done", "")})
    assert asked == [] and "apt install -y htop" in shell.ran
    assert ("apt install -y htop", "auto") in logged and result.changes == 1


def test_audit_mode_refuses_every_change(monkeypatch):
    result, shell, logged = _operate(
        monkeypatch, "audit",
        script=[("Restart it.", "systemctl restart nginx"),
                ("This server is read-only, so run it yourself: systemctl restart nginx", None)],
        confirm=lambda cid, cmd: True)                            # even a yes cannot help in audit mode
    assert "systemctl restart nginx" not in shell.ran and result.changes == 0


def test_destructive_is_refused_even_in_trusted(monkeypatch):
    result, shell, logged = _operate(
        monkeypatch, "trusted",
        script=[("Cleaning up.", "rm -rf /"), ("That is blocked; I will not run it.", None)],
        confirm=lambda cid, cmd: True)
    assert "rm -rf /" not in shell.ran and ("rm -rf /", "refused") in logged and result.changes == 0


def test_operate_wired_and_it_only():
    from src.llm.assistant import BY_NAME
    from src.llm.specialists import default_specialists

    assert BY_NAME["operate_server"].job == "operate_server"
    assert BY_NAME["operate_server"].runner() is so.run_server_operate
    by_id = {s.id: s for s in default_specialists()}
    assert "operate_server" in by_id["it"].tools and "operate_server" not in by_id["development"].tools


# ── the confirm round-trip (live.py) ───────────────────────────────────────
def test_confirm_round_trip(monkeypatch):
    import threading

    from src.jobs import live

    live.clear("job-x")
    answers = {}

    def waiter():
        answers["result"] = live.wait_confirm("job-x", "cmd-1", timeout=5)

    t = threading.Thread(target=waiter)
    t.start()
    # the endpoint answers a moment later
    for _ in range(50):
        if live.answer_confirm("job-x", "cmd-1", True):
            break
        time.sleep(0.02)
    t.join(timeout=5)
    assert answers["result"] is True


def test_confirm_times_out_without_an_answer():
    from src.jobs import live

    live.clear("job-y")
    assert live.wait_confirm("job-y", "cmd-9", timeout=0.1) is None     # nobody answered


# ── Windows (PowerShell over SSH) — operate picks the Windows classifier by os_family ──────────────
def test_windows_operate_runs_reads_and_confirms_changes(monkeypatch):
    asked: list[str] = []
    script = [("check the service", "Get-Service W3SVC"),
              ("make the folder", "New-Item -ItemType Directory C:\inetpub\site"),
              ("done", None)]
    result, shell, logged = _operate(monkeypatch, "confirm", script,
                                     confirm=lambda cid, cmd: asked.append(cmd) or True, os_family="windows")
    assert asked == ["New-Item -ItemType Directory C:\inetpub\site"]   # only the change was confirmed
    assert "Get-Service W3SVC" in shell.ran                              # the read-only cmdlet ran freely
    assert result.changes == 1


def test_windows_destructive_is_blocked_even_in_trusted(monkeypatch):
    asked: list[str] = []
    script = [("reboot it", "Restart-Computer -Force"), ("done", None)]
    result, shell, logged = _operate(monkeypatch, "trusted", script,
                                     confirm=lambda cid, cmd: asked.append(cmd) or True, os_family="windows")
    assert asked == []                                                   # never offered — blocked outright
    assert result.changes == 0
    assert ("Restart-Computer -Force", "refused") in logged


# ── O64: a silent command no longer fails the whole job ──────────────────
def test_a_silent_command_times_out_and_the_job_goes_on(monkeypatch):
    seen: list[str] = []
    real = FakeLLM.chat_tools

    def spy(self, *, messages, **kw):
        seen.append(messages[-1]["content"])
        return real(self, messages=messages, **kw)

    monkeypatch.setattr(FakeLLM, "chat_tools", spy)
    result, shell, _ = _operate(
        monkeypatch, "trusted",
        script=[("Restart.", "pm2 restart all"),
                ("Check.", "pm2 list"),
                ("pm2 restart hung; pm2 list shows both apps online.", None)],
        confirm=lambda cid, cmd: True,
        shell_outputs={"restart": la.CommandTimeout("no output"), "list": (0, "online", "")})
    assert [r.outcome for r in result.runs] == ["timed out", "exit 0"]
    assert seen[1] == so.TIMED_OUT_NOTE                       # the model was told, and went on
    assert "both apps online" in result.report and shell.ran[-1] == "pm2 list"
    assert "`pm2 restart all` — timed out" in so._commands_block(result.runs)


def test_a_server_silent_on_the_access_check_is_a_plain_error(monkeypatch):
    shell = FakeShell({})
    shell.detect_access = lambda: (_ for _ in ()).throw(la.CommandTimeout("no output"))
    _patch_session(monkeypatch, shell)
    login = so.ServerLogin(id="1", label="uk", host="94.0.0.1", port=22, username="root", password="p")
    with pytest.raises(so.AuditError, match="did not answer the first check"):
        so.RemoteSession(login)


def test_commands_are_listed_in_markdown_not_html():
    block = so._commands_block([so.CommandRun("ls", code=0), so.CommandRun("rm x", refused="not read-only")])
    assert "<details" not in block and "**Commands run (2)**" in block and "`rm x` — refused" in block
    assert so._commands_block([]) == ""


def test_the_operate_prompt_says_cd_does_not_carry():
    assert "cd /var/www/app && npm run build" in so.OPERATE_SYSTEM and "Set-Location" in so.OPERATE_SYSTEM_WIN


def test_each_confirm_answer_is_saved_as_an_event(monkeypatch):
    from src.jobs import live
    from src.servers import server_vault

    login = so.ServerLogin(id="1", label="uk", host="94.0.0.1", port=22, username="root", password="p", mode="confirm")
    monkeypatch.setattr(server_vault, "list_servers", lambda user_id: [login])
    monkeypatch.setattr(server_vault, "find", lambda user_id, name: login)
    answers = iter([True, False, None])
    monkeypatch.setattr(live, "wait_confirm", lambda job_id, command_id, timeout: next(answers))

    def fake_operate(login, query, *, confirm, **_):
        for command in ("systemctl restart nginx", "rm /tmp/x", "pm2 restart all"):
            confirm("id-" + command.split()[0], command)
        return so.OperateResult(report="done")

    monkeypatch.setattr(so, "operate", fake_operate)
    events: list[dict] = []
    cfg = __import__("dataclasses").replace(Config.from_env(), security_scan_enabled=True)
    so.run_server_operate("restart things", job_id="j", config=cfg, on_progress=events.append, user_id=7)
    resolved = [(e["command_id"], e["answer"]) for e in events if e["type"] == "confirm_resolved"]
    assert resolved == [("id-systemctl", "run"), ("id-rm", "cancel"), ("id-pm2", "timeout")]


def test_both_operate_prompts_run_what_the_user_asks_and_report_only_what_they_saw():
    """O70: a restart the user asked for was judged unnecessary and only printed."""
    for prompt in (so.OPERATE_SYSTEM, so.OPERATE_SYSTEM_WIN):
        text = prompt.format(access="You have root.", mode_note=so.MODE_NOTE["confirm"])
        assert so.OPERATE_RULES in text and "Never skip it" in text and "Report only what the command output" in text


def test_older_outputs_are_shortened_for_the_next_round():
    """O52: each round re-sent every earlier output in full."""
    long = "x" * (so.OLD_OUTPUT_CHARS * 3)
    messages = [{"role": "system", "content": "s"}, {"role": "user", "content": "task"}]
    for n in range(so.KEEP_FULL_OUTPUTS + 2):
        messages += [so._assistant_call("", f"cmd{n}"), {"role": "tool", "content": long}]
    compact = so._compact(messages)
    outputs = [m["content"] for m in compact if m["role"] == "tool"]
    assert all(len(o) < len(long) for o in outputs[:2]) and outputs[-so.KEEP_FULL_OUTPUTS:] == [long] * so.KEEP_FULL_OUTPUTS
    assert all(m["content"] == long for m in messages if m["role"] == "tool")     # the stored conversation stays whole


# ── O82: the chat names servers, never their addresses ───
def test_the_chat_shows_server_names_never_addresses():
    from src.servers import server_vault

    named = so.ServerLogin(id="1", label="uk server", host="94.72.102.195", port=22, username="u", password="p")
    unnamed = so.ServerLogin(id="2", label="10.0.0.5", host="10.0.0.5", port=22, username="u", password="p")
    assert named.shown == "uk server"
    assert unnamed.shown == server_vault.UNNAMED            # added without a name: its label is the address
    listed = server_vault.names([named, unnamed])
    assert "94.72" not in listed and "10.0.0.5" not in listed
    assert listed.startswith("uk server, ")


# ── logins kept open between messages (ISSUES_LOG O96) ─────────────────
class _Transport:
    def __init__(self):
        self.active, self.keepalive = True, None

    def is_active(self):
        return self.active

    def set_keepalive(self, seconds):
        self.keepalive = seconds


class _LiveClient:
    def __init__(self):
        self.transport, self.closed = _Transport(), False

    def get_transport(self):
        return self.transport

    def close(self):
        self.closed, self.transport.active = True, False


@pytest.fixture
def pool(monkeypatch):
    logins = []
    monkeypatch.setattr(so, "connect", lambda login: (logins.append(login) or _LiveClient(), "fp"))
    monkeypatch.setattr(so, "Shell", lambda client, password=None, os_family="linux": FakeShell({}))
    monkeypatch.setattr(so, "_idle", {})
    monkeypatch.setattr(so, "_start_sweeper", lambda: None)
    clock = [1000.0]
    monkeypatch.setattr(so.time, "monotonic", lambda: clock[0])
    return logins, clock


def _web01(**changes):
    return replace(so.ServerLogin(id="1", label="web01", host="192.168.1.9", port=22, username="u", password="p"),
                   **changes)


def test_the_next_message_reuses_the_open_login(pool):
    logins, clock = pool
    first = so.open_session(_web01())
    first.runs.append(so.CommandRun("ls", 0, "x"))
    so.release_session(first)
    clock[0] += so.KEEP_OPEN_SECONDS - 1
    again = so.open_session(_web01(mode="trusted"))
    assert again is first and len(logins) == 1
    assert again.runs == [] and again.login.mode == "trusted"             # this job's commands, today's mode
    assert first.client.transport.keepalive == so.KEEPALIVE_SECONDS


def test_a_new_login_after_idle_time_a_changed_password_or_a_dropped_connection(pool):
    logins, clock = pool
    old = so.open_session(_web01())
    so.release_session(old)
    clock[0] += so.KEEP_OPEN_SECONDS + 1
    assert so.open_session(_web01()) is not old and old.client.closed                    # idle too long
    kept = so.open_session(_web01())
    so.release_session(kept)
    assert so.open_session(_web01(password="new")) is not kept                          # other credentials
    dropped = so.open_session(_web01(id="2"))
    so.release_session(dropped)
    dropped.client.transport.active = False
    assert so.open_session(_web01(id="2")) is not dropped
    assert len(logins) == 6          # every one above was a new login; none reused


def test_a_removed_server_closes_its_kept_login(pool):
    kept = so.open_session(_web01())
    so.release_session(kept)
    so.forget({"1"})
    assert kept.client.closed and so._idle == {}
