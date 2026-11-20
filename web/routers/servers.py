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


def _tested(kind: str, trial: Any) -> dict[str, Any]:
    """Log in once with a trial login, as each kind is checked (read-only): a recorder's camera list, a router's or
    switch's read, a server's SSH login. → host_key, os, os_family, access. A trial carrying a pinned host key
    (an edit of the same address) refuses any other key. Raises HTTPException 400 with what went wrong."""
    from src.servers import linux_audit, netdev, nvr, server_vault

    try:
        if kind == "nvr":
            trial.host = server_vault.check_private(trial.host)
            recorder = nvr.read_recorder(trial.host, trial.username, trial.password or "", trial.port)
            return {"host_key": "", "os": " · ".join(p for p in (recorder.api, recorder.model) if p),
                    "os_family": "linux", "access": f"{len(recorder.cameras)} cameras", "name": recorder.name}
        if kind == "network":
            trial.host = server_vault.check_private(trial.host)
            info = netdev.test_login(trial)
            return {**info, "os_family": "linux"}
        trial.host = server_vault.check_host(trial.host)
        info = linux_audit.test_login(trial)
        return {**info, "os_family": info.get("os_family", "linux")}
    except (server_vault.VaultError, linux_audit.AuditError, netdev.DeviceError, nvr.NvrError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # never hang or 500: a test login can fail in many ways — say what, cleanly
        logger.exception("test login failed for %s@%s:%s", trial.username, trial.host, trial.port)
        raise HTTPException(status_code=400, detail=f"could not log in: {type(exc).__name__}: {exc}") from exc


def _trial(payload: ServerLoginPayload, **keep: Any) -> Any:
    from src.servers import server_vault

    return server_vault.ServerLogin(
        id="trial", label=payload.label or payload.host.strip(), host=payload.host.strip(), port=payload.port,
        username=payload.username.strip(), password=payload.password or keep.get("password"),
        private_key=payload.private_key or keep.get("private_key"),
        key_passphrase=payload.key_passphrase or keep.get("key_passphrase"),
        host_key=keep.get("host_key", ""), kind=payload.kind)


@router.post("")
def add_server(payload: ServerLoginPayload, user: User = Depends(DEVICES)) -> dict[str, Any]:
    """Log in once (learn the device's host key and what it shows), then keep the login until it is removed."""
    from src.servers import server_vault

    readonly = payload.kind != "ssh"           # recorders, routers and switches are only ever read
    if not readonly:
        _check_mode(user, payload.mode)
    trial = _trial(payload)
    info = _tested(payload.kind, trial)
    try:
        login = server_vault.add(user.id, label=payload.label or info.get("name") or trial.host, host=trial.host,
                                 port=trial.port, username=trial.username, password=trial.password,
                                 private_key=trial.private_key, key_passphrase=trial.key_passphrase,
                                 host_key=info["host_key"], mode="audit" if readonly else payload.mode,
                                 os=info.get("os", ""), os_family=info["os_family"], kind=payload.kind)
    except server_vault.VaultError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {**login.public(), "access": info["access"]}


@router.put("/{server_id}")
def update_server(server_id: str, payload: ServerLoginPayload, user: User = Depends(DEVICES)) -> dict[str, Any]:
    """Edit a saved device (Settings → Devices → Edit). A blank password / key keeps the saved one. A new address,
    port, user name or secret is tested first, like adding; the same address must still show the host key pinned
    when it was added. Only the name or mode changed: saved without logging in."""
    from src.servers import server_vault

    try:
        old = server_vault.get(user.id, server_id)
    except server_vault.VaultError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if payload.kind != old.kind:
        raise HTTPException(status_code=400, detail="a device's type cannot change — add it again as the other type")
    readonly = old.kind != "ssh"
    if not readonly:
        _check_mode(user, payload.mode)
    same_place = payload.host.strip() == old.host and payload.port == old.port
    new_secret = bool(payload.password or payload.private_key)
    keep = {k: getattr(old, k) for k in server_vault.SECRET_FIELDS} | ({"host_key": old.host_key} if same_place else {})
    if new_secret:          # a new password replaces the key and vice versa: only what was typed is kept
        keep = {k: v for k, v in keep.items() if k == "host_key"}
    trial = _trial(payload, **keep)
    info: dict[str, Any] = {"host_key": old.host_key, "os": old.os, "os_family": old.os_family, "access": ""}
    if not (same_place and payload.username.strip() == old.username and not new_secret):
        info = _tested(old.kind, trial)
    edited = server_vault.ServerLogin(
        id=old.id, label=payload.label.strip() or old.label, host=trial.host, port=trial.port,
        username=trial.username, password=trial.password, private_key=trial.private_key,
        key_passphrase=trial.key_passphrase, host_key=info["host_key"] or (old.host_key if same_place else ""),
        mode="audit" if readonly else payload.mode, os=info.get("os") or old.os,
        os_family=info.get("os_family") or old.os_family, kind=old.kind)
    try:
        saved = server_vault.update(user.id, edited)
    except server_vault.VaultError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {**saved.public(), "access": info["access"], "tested": bool(info["access"])}


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
