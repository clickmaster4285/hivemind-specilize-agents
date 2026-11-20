"""Team devices (BACKLOG L30 T4): an admin saves a server once and shares it with people or a team; they use it from
chat in the share's mode and never see its password; the owner alone edits it; every command on it is logged."""

from __future__ import annotations

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from src.core.auth import get_current_user
from src.core.models_db import User
from src.servers import server_vault
from web.routers import servers

ADMIN, ANA, BO, CY = 1, 2, 3, 4


@pytest.fixture
def people(device_store):
    with Session(device_store) as s:
        s.add_all([User(id=ADMIN, username="root", password_hash="x", role="admin", team="IT"),
                   User(id=ANA, username="ana", password_hash="x", team="IT"),
                   User(id=BO, username="bo", password_hash="x", team="marketing"),
                   User(id=CY, username="cy", password_hash="x", team="IT")])
        s.commit()
    return server_vault.add(ADMIN, label="uk server", host="94.72.102.195", port=22, username="root",
                            password="root-secret", host_key="ssh-ed25519 SHA256:x", mode="trusted")


def _api(user_id: int, role: str = "member") -> TestClient:
    app = FastAPI()
    app.include_router(servers.router)
    app.dependency_overrides[get_current_user] = lambda: User(id=user_id, username=f"u{user_id}", password_hash="x",
                                                              role=role, modules_json=json.dumps(["devices"]))
    return TestClient(app)


def test_a_shared_device_is_usable_in_the_shares_mode_never_trusted(people):
    server_vault.set_shares(ADMIN, people.id, [{"user_id": BO, "mode": "confirm"}])
    [seen] = server_vault.list_servers(BO)
    assert (seen.label, seen.mode, seen.shared_by) == ("uk server", "confirm", "root")
    assert seen.password == "root-secret"                     # the tools can log in …
    shown = _api(BO).get("/api/servers").json()
    assert shown == [seen.public()] and "root-secret" not in json.dumps(shown)      # … the page never sees it
    with pytest.raises(server_vault.VaultError):
        server_vault.set_shares(ADMIN, people.id, [{"user_id": BO, "mode": "trusted"}])


def test_a_team_share_reaches_every_member_and_a_personal_share_wins(people):
    server_vault.set_shares(ADMIN, people.id, [{"team": "IT", "mode": "audit"}, {"user_id": CY, "mode": "confirm"}])
    assert [s.mode for s in server_vault.list_servers(ANA)] == ["audit"]
    assert [s.mode for s in server_vault.list_servers(CY)] == ["confirm"]
    assert server_vault.list_servers(BO) == []                # marketing: not shared


def test_only_the_owner_edits_removes_or_shares_it(people):
    server_vault.set_shares(ADMIN, people.id, [{"user_id": ANA, "mode": "audit"}])
    member = _api(ANA)
    assert member.delete(f"/api/servers/{people.id}").status_code == 404
    assert member.put(f"/api/servers/{people.id}/mode", json={"mode": "confirm"}).status_code == 400
    assert member.get(f"/api/servers/{people.id}/shares").status_code == 403                 # admins only
    another_admin = _api(BO, role="admin")
    assert another_admin.put(f"/api/servers/{people.id}/shares", json={"shares": []}).status_code == 404
    assert server_vault.list_servers(ANA)[0].id == people.id                                  # untouched


def test_the_owner_shares_through_the_api_and_sees_who_has_it(people):
    admin = _api(ADMIN, role="admin")
    out = admin.put(f"/api/servers/{people.id}/shares",
                    json={"shares": [{"team": "marketing", "mode": "confirm"}, {"user_id": ANA}]})
    assert out.status_code == 200
    assert out.json() == [{"user_id": None, "username": None, "team": "marketing", "mode": "confirm"},
                          {"user_id": ANA, "username": "ana", "team": None, "mode": "audit"}]
    assert admin.put(f"/api/servers/{people.id}/shares", json={"shares": [{"user_id": 999}]}).status_code == 400


def test_removing_the_device_takes_it_from_everyone_and_one_password_change_serves_all(people):
    server_vault.set_shares(ADMIN, people.id, [{"team": "IT"}])
    edited = server_vault.get(ADMIN, people.id)
    edited.password = "new-root-secret"
    server_vault.update(ADMIN, edited)
    assert server_vault.list_servers(ANA)[0].password == "new-root-secret"
    assert server_vault.remove(ADMIN, people.id)
    assert server_vault.list_servers(ANA) == [] and server_vault.list_servers(CY) == []


def test_every_command_on_a_shared_device_is_logged_with_who_asked(people, monkeypatch):
    from src.servers import server_ops
    from src.servers.server_ops import CommandRun

    logged: list[dict] = []
    monkeypatch.setattr("src.servers.command_audit.log_command", lambda **kw: logged.append(kw))
    runs = [CommandRun("uptime", code=0, output="up 3 days"), CommandRun("rm -rf /tmp/x", refused="changes files"),
            CommandRun("systemctl restart nginx", code=0)]
    server_vault.set_shares(ADMIN, people.id, [{"user_id": ANA}])
    shared = server_vault.list_servers(ANA)[0]
    server_ops._log_shared_reads(None, ANA, "job1", shared, runs, refused_too=True)
    assert [(e["user_id"], e["command"], e["decision"]) for e in logged] == [
        (ANA, "uptime", "read"), (ANA, "rm -rf /tmp/x", "refused")]    # the change: logged where it ran
    logged.clear()
    own = server_vault.list_servers(ADMIN)[0]
    server_ops._log_shared_reads(None, ADMIN, "job2", own, runs, refused_too=True)
    assert logged == []                                                # a personal device: as before
