"""Device logins (src/servers/server_vault.py): saved per user, the password / key encrypted (AES-256-GCM) —
never readable in the database, never in a log or a job. Admins and the users an admin gave access may add devices;
each user sees and uses only the ones they added. Only admins may let the agent change a server without asking (trusted)."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from src.core.auth import require_module
from src.core.models_db import User
from web.schemas import ServerLoginPayload, ServerModePayload

logger = logging.getLogger("multeagent.web")

router = APIRouter(prefix="/api/servers", tags=["servers"])

DEVICES = require_module("devices")
# changes run without the user's click: an admin's decision, not every user's
ADMIN_ONLY_MODES = frozenset({"trusted"})


def _check_mode(user: User, mode: str) -> None:
    if mode in ADMIN_ONLY_MODES and user.role != "admin":
        raise HTTPException(status_code=403, detail="Only an admin can let the agent change a server without asking")


@router.get("")
def list_servers(user: User = Depends(DEVICES)) -> list[dict[str, Any]]:
    from src.servers import server_vault

    return [s.public() for s in server_vault.list_servers(user.id, kind=None)]


def _add_recorder(payload: ServerLoginPayload, user: User) -> dict[str, Any]:
    """A camera recorder: one read of its camera list is the test login (read-only, never retried)."""
    from src.servers import nvr, server_vault

    try:
        host = server_vault.check_private(payload.host)
        recorder = nvr.read_recorder(host, payload.username.strip(), payload.password or "", payload.port)
        login = server_vault.add(user.id, label=payload.label or recorder.name or host, host=host, port=payload.port,
                                 username=payload.username, password=payload.password, kind="nvr",
                                 os=" · ".join(p for p in (recorder.api, recorder.model) if p))
    except (server_vault.VaultError, nvr.NvrError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {**login.public(), "access": f"{len(recorder.cameras)} cameras"}


def _add_network_device(payload: ServerLoginPayload, user: User) -> dict[str, Any]:
    """A router or switch: one read-only read is the test login; its SSH host key is pinned like a server's."""
    from src.servers import linux_audit, netdev, server_vault

    try:
        host = server_vault.check_private(payload.host)
        trial = server_vault.ServerLogin(id="trial", label=payload.label or host, host=host, port=payload.port,
                                         username=payload.username.strip(), password=payload.password or None,
                                         private_key=payload.private_key or None,
                                         key_passphrase=payload.key_passphrase or None, kind="network")
        info = netdev.test_login(trial)
        login = server_vault.add(user.id, label=trial.label, host=host, port=trial.port, username=trial.username,
                                 password=trial.password, private_key=trial.private_key,
                                 key_passphrase=trial.key_passphrase, host_key=info["host_key"], kind="network",
                                 os=info["os"])
    except (server_vault.VaultError, linux_audit.AuditError, netdev.DeviceError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {**login.public(), "access": info["access"]}


@router.post("")
def add_server(payload: ServerLoginPayload, user: User = Depends(DEVICES)) -> dict[str, Any]:
    """Log in once (learn the server's host key and access level), then keep the login for the session."""
    from src.servers import linux_audit, server_vault

    if payload.kind == "nvr":
        return _add_recorder(payload, user)
    if payload.kind == "network":
        return _add_network_device(payload, user)
    _check_mode(user, payload.mode)
    try:
        server_vault.check_host(payload.host)
        trial = server_vault.ServerLogin(id="trial", label=payload.label or payload.host, host=payload.host.strip(),
                                         port=payload.port, username=payload.username.strip(),
                                         password=payload.password or None, private_key=payload.private_key or None,
                                         key_passphrase=payload.key_passphrase or None)
        info = linux_audit.test_login(trial)
        login = server_vault.add(user.id, label=trial.label, host=trial.host, port=trial.port,
                                 username=trial.username, password=trial.password, private_key=trial.private_key,
                                 key_passphrase=trial.key_passphrase, host_key=info["host_key"], mode=payload.mode,
                                 os=info.get("os", ""), os_family=info.get("os_family", "linux"))
    except (server_vault.VaultError, linux_audit.AuditError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # never hang or 500: a test login can fail in many ways — say what, cleanly
        logger.exception("add_server failed for %s@%s:%s", payload.username, payload.host, payload.port)
        raise HTTPException(status_code=400, detail=f"could not add the server: {type(exc).__name__}: {exc}") from exc
    return {**login.public(), "os": info["os"], "access": info["access"]}


@router.put("/{server_id}/mode")
def set_server_mode(server_id: str, payload: ServerModePayload, user: User = Depends(DEVICES)) -> dict[str, Any]:
    """Change a server's trust mode (audit / confirm / trusted)."""
    _check_mode(user, payload.mode)
    from src.servers import server_vault

    try:
        return server_vault.set_mode(user.id, server_id, payload.mode).public()
    except server_vault.VaultError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/{server_id}")
def remove_server(server_id: str, user: User = Depends(DEVICES)) -> dict[str, str]:
    from src.servers import server_vault

    if not server_vault.remove(user.id, server_id):
        raise HTTPException(status_code=404, detail="server not found (the session may have ended)")
    return {"status": "removed", "id": server_id}
