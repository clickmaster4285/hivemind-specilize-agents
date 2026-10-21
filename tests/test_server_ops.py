"""diagnose_server (src/server_ops.py): the read-only command classifier is the safety gate; the loop; wiring."""

from __future__ import annotations

from dataclasses import replace

import pytest

from src import server_ops as so
from src.config import Config

SAFE = [
    "systemctl status nginx", "journalctl -u nginx --since -1h", "df -h", "free -m", "ss -tulpn",
    "ps aux | grep nginx", "cat /etc/nginx/nginx.conf", "tail -n 50 /var/log/syslog", "sudo systemctl status ssh",
    "grep -i error /var/log/nginx/error.log", "docker ps", "docker logs web", "ip a", "ip addr show",
    "find /var/log -name '*.log' -mtime -1", "ufw status", "LC_ALL=C df -h", "uname -a", "nginx -t",
    "git -C /srv/app log --oneline -5", "dpkg -l", "cat f | grep x | sort | uniq -c", "cat a.log 2>/dev/null",
    "sudo -u www-data cat /etc/app/config", "echo $(hostname)", "ls -la /var/www && df -h",
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

    def __init__(self, outputs):
        self.outputs = outputs      # command substring -> (code, stdout, stderr)
        self.ran: list[str] = []

    def detect_sudo(self):
        pass

    def raw(self, command, stdin_text=None):
        self.ran.append(command)
        for key, val in self.outputs.items():
            if key in command:
                return val
        return (0, "ok", "")


class FakeClient:
    def close(self):
        pass


def _patch_session(monkeypatch, shell):
    monkeypatch.setattr(so, "connect", lambda login: (FakeClient(), "fp"))
    monkeypatch.setattr(so, "Shell", lambda client, password=None: shell)


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
    from src import worker
    from src.assistant import BY_NAME
    from src.specialists import default_specialists

    assert BY_NAME["diagnose_server"].intent == "server_diagnose"
    assert worker.DIRECT_TOOLS["server_diagnose"] is so.run_server_diagnose
    assert "server_diagnose" not in worker.ADMIN_TOOLS       # gated by who holds the server login, not a role
    by_id = {s.id: s for s in default_specialists()}
    assert "diagnose_server" in by_id["it"].tools and "diagnose_server" not in by_id["marketing"].tools


def test_no_server_added_yet_points_to_settings():
    out, errors, _ = so.run_server_diagnose("log in to web01 and check disk", job_id="j",
                                            config=replace(Config.from_env(), security_scan_enabled=True), user_id=1)
    assert "Settings → Servers" in out and errors == []


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


def _operate(monkeypatch, mode, script, confirm, shell_outputs=None):
    shell = FakeShell(shell_outputs or {})
    _patch_session(monkeypatch, shell)
    FakeLLM.script = script
    monkeypatch.setattr(so, "LLMClient", FakeLLM)
    logged: list[tuple[str, str]] = []
    monkeypatch.setattr("src.command_audit.log_command",
                        lambda **kw: logged.append((kw["command"], kw["decision"])))
    login = so.ServerLogin(id="1", label="web01", host="192.168.1.9", port=22, username="u", password="p", mode=mode)
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
    from src import worker
    from src.assistant import BY_NAME
    from src.specialists import default_specialists

    assert BY_NAME["operate_server"].intent == "server_operate"
    assert worker.DIRECT_TOOLS["server_operate"] is so.run_server_operate
    by_id = {s.id: s for s in default_specialists()}
    assert "operate_server" in by_id["it"].tools and "operate_server" not in by_id["development"].tools


# ── the confirm round-trip (live.py) ───────────────────────────────────────
def test_confirm_round_trip(monkeypatch):
    import threading

    from src import live

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
    from src import live

    live.clear("job-y")
    assert live.wait_confirm("job-y", "cmd-9", timeout=0.1) is None     # nobody answered
