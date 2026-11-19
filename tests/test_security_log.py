"""The security log (BACKLOG L16 S6) and what an upload may be (S3) — through the real app, offline."""

from __future__ import annotations

import base64
import io
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from src.core import security_log
from src.core.abuse import ACCOUNT_LOCK_FAILURES, LOGIN_BACKOFF_MAX
from src.core.auth import REFRESH_COOKIE, REFRESH_REUSE_GRACE, get_db, hash_password
from src.core.db import Base
from src.core.models_db import LoginFailure, RefreshToken, SecurityEvent, User
from src.tools import attachments
from src.tools.attachments import UploadRefused
from web import server

pytestmark = pytest.mark.usefixtures("cheap_hashing", "captchas_solved")

PASSWORD = "longenough"
PROXY = ("127.0.0.1", 50000)


@pytest.fixture
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    session = Session(bind=engine)
    session.add_all([User(id=1, username="ana", password_hash=hash_password(PASSWORD), role="admin"),
                     User(id=2, username="bilal", password_hash=hash_password(PASSWORD), role="member")])
    session.commit()
    server.app.dependency_overrides[get_db] = lambda: session
    yield session
    server.app.dependency_overrides.clear()
    session.close()


def _pc(address: str = "192.168.88.20") -> TestClient:
    return TestClient(server.app, client=PROXY, headers={"X-Forwarded-For": address})


def _kinds(db: Session) -> list[str]:
    return [e.kind for e in db.scalars(select(SecurityEvent).order_by(SecurityEvent.id))]


# ── S6: what is written ────────────────────────────────────────────────

def test_failed_sign_ins_and_the_lock_are_logged_with_who_and_from_where(db):
    client = _pc()
    for _ in range(ACCOUNT_LOCK_FAILURES):
        assert client.post("/api/auth/login", json={"username": "bilal", "password": "wrong"}).status_code == 401
        for row in db.scalars(select(LoginFailure)):
            row.failed_at -= LOGIN_BACKOFF_MAX
        db.commit()
    assert _kinds(db) == [security_log.LOGIN_FAILED] * ACCOUNT_LOCK_FAILURES + [security_log.ACCOUNT_LOCKED]
    lock = db.scalars(select(SecurityEvent).where(SecurityEvent.kind == security_log.ACCOUNT_LOCKED)).one()
    assert (lock.user_id, lock.account, lock.address) == (2, "user:2", "addr:192.168.88.20")


def test_a_name_with_no_account_is_logged_without_a_user(db):
    _pc().post("/api/auth/login", json={"username": "root", "password": "x"})
    event = db.scalars(select(SecurityEvent)).one()
    assert (event.kind, event.user_id, event.account) == (security_log.LOGIN_FAILED, None, "name:root")


def test_a_reused_sign_in_token_is_logged(db):
    client = _pc()
    assert client.post("/api/auth/login", json={"username": "bilal", "password": PASSWORD}).status_code == 200
    first = client.cookies[REFRESH_COOKIE]
    assert client.post("/api/auth/refresh").status_code == 200
    for row in db.scalars(select(RefreshToken).where(RefreshToken.used_at.is_not(None))):
        row.used_at -= REFRESH_REUSE_GRACE * 2
    db.commit()
    thief = TestClient(server.app, client=PROXY, headers={"X-Forwarded-For": "10.0.0.66"},
                       cookies={REFRESH_COOKIE: first})
    assert thief.post("/api/auth/refresh").status_code == 401
    event = db.scalars(select(SecurityEvent).where(SecurityEvent.kind == security_log.TOKEN_REUSED)).one()
    assert (event.user_id, event.account, event.address) == (2, "bilal", "10.0.0.66")


def test_only_an_admin_reads_the_log_and_old_events_go(db):
    old = security_log.utcnow() - timedelta(days=security_log.SECURITY_LOG_DAYS + 1)
    security_log.record(db, security_log.UPLOAD_REFUSED, user_id=2, detail="ancient", now=old)
    db.commit()
    member, admin = _pc(), _pc()
    member.post("/api/auth/login", json={"username": "bilal", "password": PASSWORD})
    assert member.get("/api/auth/users/security-events").status_code == 403
    admin.post("/api/auth/login", json={"username": "ana", "password": PASSWORD})
    security_log.record(db, security_log.UPLOAD_REFUSED, user_id=2, detail="chat picture: Empty image")
    db.commit()
    body = admin.get("/api/auth/users/security-events").json()
    assert [(e["kind"], e["username"], e["detail"]) for e in body["events"]] == [
        (security_log.UPLOAD_REFUSED, "bilal", "chat picture: Empty image")]          # the 91-day-old one is gone
    assert admin.get("/api/auth/users/security-events?kind=nonsense").status_code == 400


# ── S3: an upload is what its bytes say, re-encoded ──────────────────────

def _png(size=(4, 3), mode="RGB", fmt="PNG", **save) -> bytes:
    out = io.BytesIO()
    Image.new(mode, size, "red").save(out, fmt, **save)
    return out.getvalue()


def _item(blob: bytes, mime: str = "image/png") -> dict:
    return {"filename": "x.png", "mime": mime, "data": base64.b64encode(blob).decode()}


def test_the_type_comes_from_the_bytes_and_what_follows_the_picture_is_dropped():
    jpeg = _png(fmt="JPEG")
    _name, mime, blob = attachments.decode_image(_item(jpeg + b"<script>alert(1)</script>", mime="image/png"))
    assert mime == "image/jpeg" and b"<script>" not in blob                     # called a PNG, it is a JPEG
    assert Image.open(io.BytesIO(blob)).size == (4, 3)


def test_metadata_is_dropped():
    image = Image.new("RGB", (4, 3), "red")
    exif = Image.Exif()
    exif[0x010F] = "PhoneMaker GPS-here"                                          # Make
    out = io.BytesIO()
    image.save(out, "JPEG", exif=exif)
    _name, _mime, blob = attachments.decode_image(_item(out.getvalue(), "image/jpeg"))
    assert b"PhoneMaker" not in blob and not Image.open(io.BytesIO(blob)).getexif()


def test_a_gif_keeps_its_first_frame_as_a_png():
    _name, mime, blob = attachments.decode_image(_item(_png(fmt="GIF"), "image/gif"))
    assert mime == "image/png" and Image.open(io.BytesIO(blob)).format == "PNG"


@pytest.mark.parametrize("blob, why", [
    (b"%PDF-1.7 not a picture at all", "Not a picture that can be read"),
    (_png()[:40], "Not a picture that can be read"),                               # cut off
])
def test_what_is_not_a_picture_is_refused(blob, why):
    with pytest.raises(UploadRefused, match=why):
        attachments.decode_image(_item(blob))


def test_too_many_pixels_are_refused_before_decoding(monkeypatch):
    monkeypatch.setattr(attachments, "MAX_PIXELS", 11)
    with pytest.raises(UploadRefused, match="Picture too large: 4×3 pixels"):
        attachments.decode_image(_item(_png()))


def test_nothing_is_written_when_one_of_the_pictures_is_refused(tmp_path):
    config = type("C", (), {"artifacts_dir": str(tmp_path)})()
    with pytest.raises(UploadRefused):
        attachments.save_images(config, "job1", [_item(_png()), _item(b"not a picture")])
    assert not any(tmp_path.rglob("*.png"))


def test_a_skill_file_has_a_size_limit(db):
    admin = _pc()
    admin.post("/api/auth/login", json={"username": "ana", "password": PASSWORD})
    from web.schemas import SKILL_FILE_CHARS
    too_big = admin.post("/api/skills/upload", json={"text": "x" * (SKILL_FILE_CHARS + 1)})
    assert too_big.status_code == 422
