"""Server logins for the session — kept in this process's memory only (doc/SECURITY_TOOLS_DECISION.md, option B).

The user types a server's login in the app (Settings → Servers), never in the chat. It lives here:
  - never written to disk, the database, a log, or a job; never sent to the language model
  - one store per user: an admin sees and uses only the servers they added
  - gone on logout, when the API restarts, or SESSION_HOURS after it was added
  - repr() and the public view never show the password or key
The SSH host key seen when the server is added is remembered; a different key later is refused
(someone may be impersonating the server).

Only the in-process worker (python main.py --gui) can use these logins — a standalone worker process
has its own memory and sees none.
"""

from __future__ import annotations

import ipaddress
import socket
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from src.net_scan import PRIVATE_NETS

SESSION_HOURS = 8
MAX_SERVERS_PER_USER = 50
# what the agent may do on a server (SERVER_ACCESS_DECISION.md §8):
#   audit   = read-only only (diagnose); any change is refused
#   confirm = changes allowed, but each one needs the user's click first
#   trusted = changes run without a click (destructive ones still blocked) — for a lab server
MODES = ("audit", "confirm", "trusted")


class VaultError(ValueError):
    """The login was refused (not a private address, bad input, too many servers)."""


@dataclass
class ServerLogin:
    id: str
    label: str
    host: str
    port: int
    username: str
    password: str | None = field(default=None, repr=False)
    private_key: str | None = field(default=None, repr=False)          # PEM / OpenSSH key text
    key_passphrase: str | None = field(default=None, repr=False)
    host_key: str = ""                 # "ssh-ed25519 SHA256:…" — the fingerprint trusted when added
    mode: str = "audit"                # audit | confirm | trusted — what the agent may change (MODES)
    added_at: float = field(default_factory=time.time)

    @property
    def expires_at(self) -> float:
        return self.added_at + SESSION_HOURS * 3600

    def public(self) -> dict[str, Any]:
        """What the app may show — never the secret."""
        return {"id": self.id, "label": self.label, "host": self.host, "port": self.port,
                "username": self.username, "auth": "key" if self.private_key else "password",
                "host_key": self.host_key, "mode": self.mode, "expires_at": int(self.expires_at)}


_store: dict[int, dict[str, ServerLogin]] = {}
_lock = threading.Lock()


def check_host(host: str) -> str:
    """A server address to log in to: an IP (private OR public — you own the server, you have its
    password), or a name that resolves to one. SSH logins are not limited to the local network the way
    the credential-free scan tools are; the login itself is the authorization. Returns the host to use."""
    host = (host or "").strip()
    if not host or len(host) > 253 or any(c.isspace() for c in host):
        raise VaultError("enter a server address, e.g. 192.168.1.20 or myvps.example.com")
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        try:
            ip = ipaddress.ip_address(socket.gethostbyname(host))
        except (OSError, ValueError) as exc:
            raise VaultError(f"cannot find the server '{host}'") from exc
    if ip.is_unspecified or ip.is_multicast or ip.is_reserved:
        raise VaultError(f"{host} is not a server address you can log in to")
    return host


def _purge(user_id: int) -> dict[str, ServerLogin]:
    servers = _store.setdefault(user_id, {})
    now = time.time()
    for sid in [sid for sid, s in servers.items() if s.expires_at <= now]:
        del servers[sid]
    return servers


def add(user_id: int, *, label: str, host: str, port: int, username: str, password: str | None = None,
        private_key: str | None = None, key_passphrase: str | None = None, host_key: str = "",
        mode: str = "audit") -> ServerLogin:
    if not (username or "").strip():
        raise VaultError("enter the user name")
    if not (password or private_key):
        raise VaultError("enter a password or a private key")
    if not 1 <= int(port) <= 65535:
        raise VaultError("the SSH port must be 1–65535")
    if mode not in MODES:
        raise VaultError(f"mode must be one of {', '.join(MODES)}")
    login = ServerLogin(id=uuid.uuid4().hex[:12], label=(label or host).strip()[:60], host=host.strip(),
                        port=int(port), username=username.strip(), password=password or None,
                        private_key=private_key or None, key_passphrase=key_passphrase or None,
                        host_key=host_key, mode=mode)
    with _lock:
        servers = _purge(user_id)
        if len(servers) >= MAX_SERVERS_PER_USER:
            raise VaultError(f"at most {MAX_SERVERS_PER_USER} servers per session")
        servers[login.id] = login
    return login


def list_servers(user_id: int) -> list[ServerLogin]:
    with _lock:
        return sorted(_purge(user_id).values(), key=lambda s: s.label.lower())


def find(user_id: int, name: str | None) -> ServerLogin | None:
    """By id, label or address. With exactly one server in the session it is unambiguous, so that one is
    returned whatever the name says (handles a typo like 'pk sever', or no name at all)."""
    servers = list_servers(user_id)
    if not servers:
        return None
    if len(servers) == 1:
        return servers[0]
    wanted = (name or "").strip().lower()
    if not wanted:
        return None
    for s in servers:
        if wanted in (s.id, s.label.lower(), s.host.lower()):
            return s
    # looser: the label or the address appears somewhere in what the user said ("pk server (31.97.75.233)")
    return next((s for s in servers
                 if wanted in s.label.lower() or s.label.lower() in wanted or s.host.lower() in wanted), None)


def set_mode(user_id: int, server_id: str, mode: str) -> ServerLogin:
    if mode not in MODES:
        raise VaultError(f"mode must be one of {', '.join(MODES)}")
    with _lock:
        login = _store.get(user_id, {}).get(server_id)
        if login is None:
            raise VaultError("server not found (the session may have ended)")
        login.mode = mode
    return login


def remove(user_id: int, server_id: str) -> bool:
    with _lock:
        return _store.get(user_id, {}).pop(server_id, None) is not None


def clear(user_id: int) -> int:
    """Logout: forget every login of this user."""
    with _lock:
        return len(_store.pop(user_id, {}))
