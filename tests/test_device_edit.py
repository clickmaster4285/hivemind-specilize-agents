"""Settings → Devices → Edit (PUT /api/servers/{id}): a name or mode change is saved without logging in; a new
address, user or password is tested first; a blank password keeps the saved one; the same address must keep its
pinned host key. No real SSH — linux_audit.test_login is replaced."""

from __future__ import annotations

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.core.auth import get_current_user
from src.core.models_db import User
from src.servers import linux_audit, server_vault
from web.routers import servers

USER = 4343
PINNED = "ssh-ed25519 SHA256:pinned"


def _api(role: str = "member") -> TestClient:
    app = FastAPI()
    app.include_router(servers.router)
    app.dependency_overrides[get_current_user] = lambda: User(id=USER, username="ana", password_hash="x", role=role,
                                                              modules_json=json.dumps(["devices"]))
    return TestClient(app)


@pytest.fixture
def saved(device_store, monkeypatch):
    """One saved server, and a fake SSH test login that records what it was given."""
    tried: list[server_vault.ServerLogin] = []

    def fake_test_login(login):
        tried.append(login)
        if login.host_key and login.host_key != PINNED:
            raise linux_audit.AuditError("the server's identity changed")
        if login.password == "wrong":
            raise linux_audit.AuditError("authentication failed")
        return {"host_key": login.host_key or "ssh-ed25519 SHA256:new", "os": "Ubuntu 24.04.5 LTS",
                "os_family": "linux", "access": "root"}

    monkeypatch.setattr(linux_audit, "test_login", fake_test_login)
    login = server_vault.add(USER, label="pk server", host="31.97.75.233", port=22, username="root",
                             password="old-pw", host_key=PINNED, mode="confirm", os="Ubuntu 24.04.4 LTS")
    return login, tried


def _edit(login, **changes) -> dict:
    return {"kind": "ssh", "label": login.label, "host": login.host, "port": login.port,
            "username": login.username, "mode": login.mode, **changes}


def test_a_new_name_or_mode_is_saved_without_logging_in(saved):
    login, tried = saved
    out = _api().put(f"/api/servers/{login.id}", json=_edit(login, label="pk web", mode="audit"))
    assert out.status_code == 200 and not out.json()["tested"]
    assert tried == []
    kept = server_vault.get(USER, login.id)
    assert (kept.label, kept.mode, kept.password, kept.host_key) == ("pk web", "audit", "old-pw", PINNED)


def test_a_new_password_is_tested_and_saved_encrypted(saved):
    login, tried = saved
    out = _api().put(f"/api/servers/{login.id}", json=_edit(login, password="new-pw"))
    assert out.status_code == 200 and out.json()["tested"]
    assert tried[0].password == "new-pw" and tried[0].host_key == PINNED       # same address: the pinned key
    assert server_vault.get(USER, login.id).password == "new-pw"
    assert "new-pw" not in out.text and "secret" not in out.json()


def test_a_wrong_new_password_changes_nothing(saved):
    login, _tried = saved
    out = _api().put(f"/api/servers/{login.id}", json=_edit(login, password="wrong"))
    assert out.status_code == 400 and "authentication failed" in out.json()["detail"]
    assert server_vault.get(USER, login.id).password == "old-pw"


def test_a_new_address_keeps_the_saved_password_and_learns_its_key(saved):
    login, tried = saved
    out = _api().put(f"/api/servers/{login.id}", json=_edit(login, host="94.72.102.195"))
    assert out.status_code == 200
    assert tried[0].password == "old-pw" and tried[0].host_key == ""            # a new place: no pinned key
    kept = server_vault.get(USER, login.id)
    assert kept.host == "94.72.102.195" and kept.host_key == "ssh-ed25519 SHA256:new"


def test_the_same_address_with_another_identity_is_refused(saved, monkeypatch):
    login, _tried = saved
    monkeypatch.setattr(linux_audit, "test_login",
                        lambda trial: (_ for _ in ()).throw(linux_audit.AuditError("the server's identity changed")))
    out = _api().put(f"/api/servers/{login.id}", json=_edit(login, username="admin"))
    assert out.status_code == 400 and "identity changed" in out.json()["detail"]
    assert server_vault.get(USER, login.id).username == "root"


def test_the_type_cannot_change_and_only_admins_may_trust(saved):
    login, _tried = saved
    assert _api().put(f"/api/servers/{login.id}", json=_edit(login, kind="nvr")).status_code == 400
    assert _api().put(f"/api/servers/{login.id}", json=_edit(login, mode="trusted")).status_code == 403
    assert _api("admin").put(f"/api/servers/{login.id}", json=_edit(login, mode="trusted")).status_code == 200


def test_someone_elses_device_is_not_found(saved, monkeypatch):
    login, _tried = saved
    other = FastAPI()
    other.include_router(servers.router)
    other.dependency_overrides[get_current_user] = lambda: User(id=USER + 1, username="bo", password_hash="x",
                                                                role="admin", modules_json="[]")
    assert TestClient(other).put(f"/api/servers/{login.id}", json=_edit(login, label="mine")).status_code == 404
