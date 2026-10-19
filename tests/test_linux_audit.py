"""Option B — Linux audit over SSH: session-only logins, fixed commands, parsers, rules, API (no real server)."""

from __future__ import annotations

from dataclasses import replace

import pytest

from src import linux_audit as la
from src import server_vault as vault
from src.config import Config
from src.security_scan import HIGH, LOW, MEDIUM

SECRET = "S3cret-pa55!"
CFG = replace(Config.from_env(), security_scan_enabled=True)


@pytest.fixture(autouse=True)
def empty_vault():
    vault._store.clear()
    yield
    vault._store.clear()


# ── the session store ────────────────────────────────────────────────────
def test_logins_are_per_user_and_never_shown():
    login = vault.add(1, label="web01", host="192.168.88.66", port=22, username="audit", password=SECRET)
    assert SECRET not in repr(login) and SECRET not in str(login.public())
    assert login.public()["auth"] == "password"
    assert [s.label for s in vault.list_servers(1)] == ["web01"]
    assert vault.list_servers(2) == []                                  # another admin sees nothing
    assert vault.find(1, "web01") is login and vault.find(1, "192.168.88.66") is login
    assert vault.find(1, None) is login                                 # the only one
    assert vault.find(2, "web01") is None


def test_logins_expire_and_logout_forgets_them(monkeypatch):
    vault.add(1, label="a", host="10.0.0.5", port=22, username="u", password=SECRET)
    later = vault.time.time() + vault.SESSION_HOURS * 3600 + 1
    monkeypatch.setattr(vault.time, "time", lambda: later)
    assert vault.list_servers(1) == []
    monkeypatch.undo()
    vault.add(1, label="b", host="10.0.0.6", port=22, username="u", password=SECRET)
    assert vault.clear(1) == 1 and vault.list_servers(1) == []


@pytest.mark.parametrize("host", ["8.8.8.8", "", "1.1.1.1"])
def test_only_private_servers(host):
    with pytest.raises(vault.VaultError):
        vault.check_host(host)


def test_a_password_or_key_is_required():
    with pytest.raises(vault.VaultError, match="password or a private key"):
        vault.add(1, label="x", host="10.0.0.5", port=22, username="u")


# ── commands: fixed, sudo only around them, the password only on stdin ───
def test_commands_are_fixed_strings():
    for key, (command, _root) in la.COMMANDS.items():
        assert "{" not in command and "%s" not in command, key


class FakeClient:
    """Records the command lines and what was written to stdin."""

    def __init__(self, answers: dict[str, tuple[int, str]] | None = None):
        self.calls: list[tuple[str, str]] = []
        self.answers = answers or {}

    def exec_command(self, command, timeout=None):
        client = self
        written: list[str] = []
        code, out = next(((c, o) for key, (c, o) in self.answers.items() if command.endswith(key)), (0, ""))

        class Channel:
            def shutdown_write(self):
                client.calls.append((command, "".join(written)))

            def recv_exit_status(self):
                return code

        class Stream:
            channel = Channel()

            def __init__(self, data: str = ""):
                self.data = data

            def write(self, text):
                written.append(text)

            def flush(self):
                pass

            def read(self):
                return self.data.encode()

        return Stream(), Stream(out), Stream()


def test_sudo_with_a_password_goes_through_stdin_only():
    client = FakeClient({"id -u": (0, "1000"), "sudo -n true": (1, ""), "sudo -S -p '' true": (0, "")})
    shell = la.Shell(client, password=SECRET)
    shell.detect_sudo()
    assert (shell.root, shell.sudo) == (False, "password")
    shell.run("ufw")
    command, stdin = client.calls[-1]
    assert command.endswith("sudo -S -p '' ufw status verbose")
    assert SECRET not in command and stdin == SECRET + "\n"
    shell.run("os")                                                      # needs no root: no sudo
    assert "sudo" not in client.calls[-1][0] and client.calls[-1][1] == ""


def test_passwordless_sudo_and_root():
    client = FakeClient({"id -u": (0, "1000"), "sudo -n true": (0, "")})
    shell = la.Shell(client, password=None)
    shell.detect_sudo()
    assert shell.sudo == "passwordless"
    shell.run("sshd")
    assert client.calls[-1][0].endswith("sudo -n sshd -T")
    root = la.Shell(FakeClient({"id -u": (0, "0")}))
    root.detect_sudo()
    assert root.root is True
    root.run("sshd")
    assert "sudo" not in root.client.calls[-1][0]


# ── reading what the commands print ──────────────────────────────────────
SS = """tcp   LISTEN 0 4096   0.0.0.0:22        0.0.0.0:*  users:(("sshd",pid=812,fd=3))
tcp   LISTEN 0 511    0.0.0.0:6379      0.0.0.0:*  users:(("redis-server",pid=900,fd=6))
tcp   LISTEN 0 244    127.0.0.1:5432    0.0.0.0:*  users:(("postgres",pid=700,fd=5))
tcp   LISTEN 0 4096   [::]:22           [::]:*     users:(("sshd",pid=812,fd=4))
udp   UNCONN 0 0      127.0.0.53%lo:53  0.0.0.0:*  users:(("systemd-resolve",pid=600,fd=13))
"""


def test_parse_ss():
    ports = la.parse_ss(SS)
    exposed = [(p.port, p.program) for p in ports if p.network]
    assert (6379, "redis-server") in exposed and (22, "sshd") in exposed
    assert not any(p.port == 5432 and p.network for p in ports)          # postgres on 127.0.0.1 only
    assert any(p.port == 53 and not p.network for p in ports)


def test_parse_firewalls():
    ufw = la.parse_ufw("Status: active\nLogging: on (low)\nDefault: deny (incoming), allow (outgoing), disabled (routed)\n"
                       "\nTo   Action   From\n22/tcp   ALLOW IN   Anywhere\n")
    assert ufw == {"active": True, "incoming": "deny", "rules": ["22/tcp   ALLOW IN   Anywhere"]}
    assert la.parse_ufw("Status: inactive\n")["active"] is False
    ipt = la.parse_iptables("-P INPUT ACCEPT\n-P FORWARD DROP\n-A INPUT -p tcp --dport 22 -j ACCEPT\n")
    assert ipt == {"input_policy": "ACCEPT", "drop_rules": 0, "rules": 1}
    assert la.nft_filters_input("chain input {\n type filter hook input priority 0; policy drop;\n}")


def test_parse_sshd_updates_logins_fail2ban():
    sshd = la.parse_sshd("port 22\npermitrootlogin yes\npasswordauthentication yes\nmaxauthtries 6\n")
    assert sshd["permitrootlogin"] == "yes" and sshd["maxauthtries"] == "6"
    apt = ("Listing...\nopenssl/jammy-security 3.0.2-0ubuntu1.18 amd64 [upgradable from: 3.0.2-0ubuntu1.15]\n"
           "vim/jammy-updates 2:8.2 amd64 [upgradable from: 2:8.1]\n")
    assert la.parse_apt(apt) == (2, 1)
    log = "\n".join(["sshd[1]: Failed password for root from 203.0.113.9 port 4242 ssh2"] * 3
                    + ["sshd[2]: Invalid user admin from 198.51.100.7 port 1"])
    assert la.parse_failed_logins(log) == (4, [("203.0.113.9", 3), ("198.51.100.7", 1)])
    assert la.parse_fail2ban("Status\n|- Number of jail: 1\n`- Jail list: sshd", 0) is True
    assert la.parse_fail2ban("bash: fail2ban-client: command not found", 127) is False


# ── the rules ────────────────────────────────────────────────────────────
def test_findings_for_a_weak_server():
    audit = la.Audit(label="web01 (192.168.88.66)", host="192.168.88.66",
                     ports=la.parse_ss(SS), ufw={"active": False, "incoming": "", "rules": []},
                     iptables={"input_policy": "ACCEPT", "drop_rules": 0, "rules": 0},
                     sshd={"permitrootlogin": "yes", "passwordauthentication": "yes", "x11forwarding": "yes"},
                     updates=12, security_updates=3, reboot_required=True,
                     failed_logins=150, top_sources=[("203.0.113.9", 140)], fail2ban=False)
    found = {(f.risk, f.title) for f in la.audit_findings(audit)}
    assert (HIGH, "No firewall filters incoming traffic") in found
    assert (HIGH, "SSH allows root login with a password") in found
    assert (MEDIUM, "SSH accepts passwords") in found
    assert (HIGH, "Redis listening on the network (redis-server)") in found
    assert (MEDIUM, "3 security updates waiting") in found and (MEDIUM, "Reboot required") in found
    assert (MEDIUM, "150 failed SSH logins in 24 h") in found
    assert (LOW, "No fail2ban") in found and (LOW, "SSH X11 forwarding is on") in found


def test_a_hardened_server_has_no_findings():
    audit = la.Audit(label="db01", host="10.0.0.9", ports=[la.Port("tcp", "0.0.0.0", 22, "sshd")],
                     ufw={"active": True, "incoming": "deny", "rules": ["22/tcp ALLOW"]},
                     sshd={"permitrootlogin": "prohibit-password", "passwordauthentication": "no"},
                     updates=0, security_updates=0, failed_logins=3, fail2ban=True)
    assert la.audit_findings(audit) == []
    assert "No risky settings found" in la.markdown(audit)


# ── the chat job ─────────────────────────────────────────────────────────
def test_no_server_added_yet_says_where_to_add_it():
    out, errors, _ = la.run_server_audit("audit my server", job_id="j", config=CFG, user_id=1)
    assert "Settings → Servers" in out and errors == []


def test_login_failure_message_never_contains_the_password(monkeypatch):
    vault.add(1, label="web01", host="192.168.88.66", port=22, username="audit", password=SECRET)

    def refused(login):
        raise la.AuditError("login refused — check the user name and password / key")

    monkeypatch.setattr(la, "connect", refused)
    out, errors, _ = la.run_server_audit("audit web01", job_id="j", config=CFG, user_id=1)
    assert "login refused" in out and SECRET not in out and all(SECRET not in e for e in errors)


def test_the_server_named_in_the_message_is_used(monkeypatch):
    vault.add(1, label="web01", host="192.168.88.66", port=22, username="u", password=SECRET)
    vault.add(1, label="db01", host="192.168.88.45", port=22, username="u", password=SECRET)
    seen = []
    monkeypatch.setattr(la, "run_audit", lambda login, **kw: seen.append(login.label) or la.Audit("x", "y"))
    la.run_server_audit("please audit db01 now", job_id="j", config=CFG, user_id=1)
    assert seen == ["db01"]
    out, _, _ = la.run_server_audit("audit my server", job_id="j", config=CFG, user_id=1)
    assert "Which server" in out                                         # two added, none named


def test_admin_only_in_the_worker():
    from src import worker

    assert "server_audit" in worker.ADMIN_TOOLS and worker.DIRECT_TOOLS["server_audit"] is la.run_server_audit


# ── the API ──────────────────────────────────────────────────────────────
@pytest.fixture
def api(monkeypatch):
    from fastapi.testclient import TestClient

    from web import server

    admin = type("U", (), {"id": 7, "role": "admin", "username": "admin"})()
    server.app.dependency_overrides[server.require_admin] = lambda: admin
    server.app.dependency_overrides[server.get_current_user] = lambda: admin
    monkeypatch.setattr(la, "test_login", lambda login: {"host_key": "ssh-ed25519 SHA256:abc", "os": "Ubuntu 22.04",
                                                          "access": "sudo: password"})
    yield TestClient(server.app)
    server.app.dependency_overrides.clear()


def test_api_adds_lists_and_forgets_without_returning_secrets(api):
    added = api.post("/api/servers", json={"label": "web01", "host": "192.168.88.66", "username": "audit",
                                            "password": SECRET})
    assert added.status_code == 200 and SECRET not in added.text
    assert added.json()["host_key"] == "ssh-ed25519 SHA256:abc" and added.json()["os"] == "Ubuntu 22.04"
    listed = api.get("/api/servers")
    assert [s["label"] for s in listed.json()] == ["web01"] and SECRET not in listed.text
    assert api.post("/api/servers", json={"host": "8.8.8.8", "username": "u", "password": "x"}).status_code == 400
    assert api.delete("/api/servers").json() == {"forgotten": 1}
    assert api.get("/api/servers").json() == []
