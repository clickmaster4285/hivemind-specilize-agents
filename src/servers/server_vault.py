"""Device logins (Settings → Devices): saved per user, the password / key encrypted (BACKLOG L19b D4).

The user types a device's login in the app, never in the chat. It is kept in the `devices` table:
  - the password, private key and passphrase only as AES-256-GCM (src/core/crypto.py), bound to their owner
    and row; decrypted only when a tool logs in, never written to a log or a job, never sent to the model
  - each user sees and uses only the devices they added (admins too)
  - kept until removed (or until an admin takes the user's Devices access away)
  - repr() and the public view never show the password or key
The SSH host key seen when a server is added is remembered; a different key later is refused (someone may be
impersonating the server).
"""

from __future__ import annotations

import ipaddress
import json
import socket
import uuid
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from src.core import crypto
from src.core.db import session_scope
from src.core.models_db import DeviceRow

MAX_SERVERS_PER_USER = 50
# what the agent may do on a server (SERVER_ACCESS_DECISION.md §8):
#   audit   = read-only only (diagnose); any change is refused
#   confirm = changes allowed, but each one needs the user's click first
#   trusted = changes run without a click (destructive ones still blocked) — for a lab server
MODES = ("audit", "confirm", "trusted")
# what a login is for: ssh = a server the agent logs in to; nvr = a camera recorder read over its own API
# (src/servers/nvr.py); network = a router or switch read over SSH (src/servers/netdev.py). nvr and network
# are read-only. The SSH tools only ever see ssh logins.
KINDS = ("ssh", "nvr", "network")
SECRET_FIELDS = ("password", "private_key", "key_passphrase")

# the database session; tests point this at their own database
_scope: Callable[[], AbstractContextManager[Session]] = session_scope


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
    os: str = ""                       # pretty OS name learned at login ("Ubuntu 24.04", "Windows Server 2022")
    os_family: str = "linux"           # linux | windows — picks the command classifier the agent uses
    kind: str = "ssh"                  # KINDS

    def public(self) -> dict[str, Any]:
        """What the app may show — never the secret."""
        return {"id": self.id, "label": self.label, "host": self.host, "port": self.port,
                "username": self.username, "auth": "key" if self.private_key else "password",
                "host_key": self.host_key, "mode": self.mode, "os": self.os, "os_family": self.os_family,
                "kind": self.kind}


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


def check_private(host: str) -> str:
    """A device on the local network only (camera recorders): their web logins are not meant for the internet,
    so a public address is refused like the credential-free network tools do."""
    host = check_host(host)
    try:
        ip = ipaddress.ip_address(socket.gethostbyname(host))
    except OSError as exc:
        raise VaultError(f"cannot find '{host}'") from exc
    if not ip.is_private or ip.is_loopback:
        raise VaultError(f"{host} is not on the local network (10.x, 172.16–31.x, 192.168.x)")
    return host


def _aad(user_id: int, device_id: str) -> str:
    """What the ciphertext is bound to: copied to another user's row or another device, it will not decrypt."""
    return f"device:{user_id}:{device_id}"


def _login(row: DeviceRow) -> ServerLogin:
    secret = json.loads(crypto.decrypt(row.secret_enc, _aad(row.user_id, row.id)))
    return ServerLogin(id=row.id, label=row.label, host=row.host, port=row.port, username=row.username,
                       host_key=row.host_key, mode=row.mode, os=row.os, os_family=row.os_family, kind=row.kind,
                       **{k: secret.get(k) for k in SECRET_FIELDS})


def add(user_id: int, *, label: str, host: str, port: int, username: str, password: str | None = None,
        private_key: str | None = None, key_passphrase: str | None = None, host_key: str = "",
        mode: str = "audit", os: str = "", os_family: str = "linux", kind: str = "ssh") -> ServerLogin:
    if kind not in KINDS:
        raise VaultError(f"kind must be one of {', '.join(KINDS)}")
    if not (username or "").strip():
        raise VaultError("enter the user name")
    if not (password or private_key):
        raise VaultError("enter a password or a private key")
    if not 1 <= int(port) <= 65535:
        raise VaultError("the port must be 1–65535")
    if mode not in MODES:
        raise VaultError(f"mode must be one of {', '.join(MODES)}")
    device_id = uuid.uuid4().hex[:12]
    secret = json.dumps({"password": password or None, "private_key": private_key or None,
                         "key_passphrase": key_passphrase or None})
    row = DeviceRow(id=device_id, user_id=user_id, kind=kind, label=(label or host).strip()[:60], host=host.strip(),
                    port=int(port), username=username.strip(), secret_enc=crypto.encrypt(secret, _aad(user_id, device_id)),
                    host_key=host_key, mode=mode, os=os, os_family=os_family)
    with _scope() as session:
        count = session.scalar(select(func.count()).select_from(DeviceRow).where(DeviceRow.user_id == user_id))
        if count >= MAX_SERVERS_PER_USER:
            raise VaultError(f"at most {MAX_SERVERS_PER_USER} devices per user")
        session.add(row)
        session.flush()
        return _login(row)


def list_servers(user_id: int, kind: str | None = "ssh") -> list[ServerLogin]:
    """The user's logins of one kind (None = all, for Settings)."""
    with _scope() as session:
        query = select(DeviceRow).where(DeviceRow.user_id == user_id)
        if kind is not None:
            query = query.where(DeviceRow.kind == kind)
        logins = [_login(row) for row in session.scalars(query)]
    return sorted(logins, key=lambda s: s.label.lower())


def find(user_id: int, name: str | None, kind: str = "ssh") -> ServerLogin | None:
    """By id, label or address. With exactly one login of the kind it is unambiguous, so that one is
    returned whatever the name says (handles a typo like 'pk sever', or no name at all)."""
    servers = list_servers(user_id, kind)
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
    with _scope() as session:
        row = session.scalar(select(DeviceRow).where(DeviceRow.user_id == user_id, DeviceRow.id == server_id))
        if row is None:
            raise VaultError("device not found")
        row.mode = mode
        return _login(row)


def remove(user_id: int, server_id: str) -> bool:
    with _scope() as session:
        return session.execute(delete(DeviceRow).where(DeviceRow.user_id == user_id,
                                                       DeviceRow.id == server_id)).rowcount > 0


def clear(user_id: int) -> int:
    """Every device of this user (their Devices access was taken away)."""
    with _scope() as session:
        return session.execute(delete(DeviceRow).where(DeviceRow.user_id == user_id)).rowcount
