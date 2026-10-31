"""Server logins for the session (src/servers/server_vault.py): kept in this process's memory only —
never on disk, in the database, a log or a job. Admins only; each admin sees only the servers they added."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from src.core.auth import get_current_user, require_admin
from src.core.models_db import User
from web.schemas import ServerLoginPayload, ServerModePayload

logger = logging.getLogger("multeagent.web")

router = APIRouter(prefix="/api/servers", tags=["servers"])


@router.get("")
def list_servers(user: User = Depends(require_admin)) -> list[dict[str, Any]]:
    from src.servers import server_vault

    return [s.public() for s in server_vault.list_servers(user.id)]


@router.post("")
def add_server(payload: ServerLoginPayload, user: User = Depends(require_admin)) -> dict[str, Any]:
    """Log in once (learn the server's host key and access level), then keep the login for the session."""
    from src.servers import linux_audit, server_vault

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
def set_server_mode(server_id: str, payload: ServerModePayload, user: User = Depends(require_admin)) -> dict[str, Any]:
    """Change a server's trust mode (audit / confirm / trusted)."""
    from src.servers import server_vault

    try:
        return server_vault.set_mode(user.id, server_id, payload.mode).public()
    except server_vault.VaultError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/{server_id}")
def remove_server(server_id: str, user: User = Depends(require_admin)) -> dict[str, str]:
    from src.servers import server_vault

    if not server_vault.remove(user.id, server_id):
        raise HTTPException(status_code=404, detail="server not found (the session may have ended)")
    return {"status": "removed", "id": server_id}


@router.delete("")
def forget_servers(user: User = Depends(get_current_user)) -> dict[str, int]:
    """Logout: forget every server login of this user."""
    from src.servers import server_vault

    return {"forgotten": server_vault.clear(user.id)}
