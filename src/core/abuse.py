"""Abuse limits (BACKLOG L16 S2, ISSUES_LOG O36): sign-in throttling and request rates.

- **Sign-in**, kept in `login_failures` so an API restart does not reset an attacker's count. Per account:
  LOGIN_FREE_FAILURES are free (typos), then each further try waits twice as long after the last failure,
  and ACCOUNT_LOCK_FAILURES lock the account. Per address: ADDRESS_BLOCK_FAILURES block that PC (one PC
  trying many usernames). A refused try checks no password and is not counted. A name with no account is
  counted the same way, so the answers do not tell which usernames exist.
- **Request rates**, in memory (the API is one process): a token bucket per signed-in user, another per
  address for calls without a sign-in.

The caller's address comes from the UI's Node server (frontend/server.mjs), which overwrites
X-Forwarded-For with the browser's own address. The header is believed only from that proxy on this host.
"""

from __future__ import annotations

import ipaddress
import logging
import math
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from src.core.models_db import LoginFailure, utcnow

log = logging.getLogger(__name__)

# ── who is calling ────────────────────────────────────────────────────────────────────────────────────
# the only proxy in front of the API is the UI's Node server on this host; any other peer could forge it
TRUSTED_PROXIES = frozenset({"127.0.0.1", "::1"})
FORWARDED_FOR = b"x-forwarded-for"

# ── sign-in (approved by the user 2026-11-11) ─────────────────────────────────────────────────────────
LOGIN_WINDOW = timedelta(minutes=15)          # failures older than this are forgotten
LOGIN_FREE_FAILURES = 5                       # typos: no wait
LOGIN_BACKOFF_FIRST = timedelta(seconds=1)    # wait after the first failure past the free ones; doubles
LOGIN_BACKOFF_MAX = timedelta(seconds=30)
ACCOUNT_LOCK_FAILURES = 10
ADDRESS_BLOCK_FAILURES = 20
# as long as the window: a lock ends exactly when the failures that caused it are forgotten
LOGIN_LOCK = LOGIN_WINDOW
# the longest identifier an account can have (the email column); a longer name matches nothing anyway
KEY_IDENT_CHARS = 255

# ── request rates (approved by the user 2026-11-11; a page load is ~10–20 calls) ─────────────────────
USER_RATE_PER_MINUTE = 120
USER_BURST = 60
ANON_RATE_PER_MINUTE = 60
ANON_BURST = 60                               # a minute's worth: the login page and SSO status need few
# idle buckets are dropped this often, so memory holds only the callers of the last few minutes
BUCKET_PRUNE_SECONDS = 300
SECONDS_PER_MINUTE = 60


def client_address(scope: dict) -> str:
    """The caller's address for an ASGI request: X-Forwarded-For (last entry) when the direct peer is the
    trusted proxy, else the peer itself."""
    client = scope.get("client")
    address = client[0] if client else ""
    if address in TRUSTED_PROXIES:
        forwarded = [value for name, value in scope.get("headers", []) if name == FORWARDED_FOR]
        if forwarded:
            address = forwarded[-1].decode("latin-1").split(",")[-1].strip() or address
    return _plain(address)


def _plain(address: str) -> str:
    """'::ffff:192.168.88.20' (Node reports IPv4 callers on an IPv6 socket this way) → '192.168.88.20'."""
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return address
    return str(getattr(ip, "ipv4_mapped", None) or ip)


def account_key(user_id: int | None, typed: str) -> str:
    return f"user:{user_id}" if user_id is not None else f"name:{typed.strip().lower()[:KEY_IDENT_CHARS]}"


def address_key(address: str) -> str:
    return f"addr:{address}"


class TooManyAttempts(Exception):
    def __init__(self, retry_after: timedelta):
        super().__init__(f"retry after {retry_after}")
        self.retry_after = retry_after


def wait_text(wait: timedelta) -> str:
    seconds = math.ceil(wait.total_seconds())
    if seconds < SECONDS_PER_MINUTE:
        return f"{seconds} s"
    return f"{math.ceil(seconds / SECONDS_PER_MINUTE)} min"


def account_wait(failures: int) -> timedelta:
    """How long after its latest failure an account may try again."""
    if failures >= ACCOUNT_LOCK_FAILURES:
        return LOGIN_LOCK
    if failures >= LOGIN_FREE_FAILURES:
        return min(LOGIN_BACKOFF_FIRST * 2 ** (failures - LOGIN_FREE_FAILURES), LOGIN_BACKOFF_MAX)
    return timedelta(0)


def address_wait(failures: int) -> timedelta:
    return LOGIN_LOCK if failures >= ADDRESS_BLOCK_FAILURES else timedelta(0)


def _aware(value: datetime) -> datetime:
    """SQLite hands timestamps back without their zone; they were stored in UTC."""
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _recent(db: Session, key: str, now: datetime) -> tuple[int, datetime | None]:
    """(failures within the window, the latest one)."""
    count, latest = db.execute(
        select(func.count(LoginFailure.id), func.max(LoginFailure.failed_at))
        .where(LoginFailure.key == key, LoginFailure.failed_at > now - LOGIN_WINDOW)
    ).one()
    return count, (_aware(latest) if latest is not None else None)


def check_login(db: Session, account: str, address: str, now: datetime | None = None) -> None:
    """Raise TooManyAttempts while the account or the address must wait."""
    now = now or utcnow()
    waits = []
    for key, rule in ((account, account_wait), (address, address_wait)):
        count, latest = _recent(db, key, now)
        if latest is not None:
            waits.append(latest + rule(count) - now)
    longest = max(waits, default=timedelta(0))
    if longest > timedelta(0):
        raise TooManyAttempts(longest)


def record_failure(db: Session, account: str, address: str, now: datetime | None = None) -> None:
    """Count a failed sign-in against the account and the address; forget failures past the window."""
    now = now or utcnow()
    db.execute(delete(LoginFailure).where(LoginFailure.failed_at <= now - LOGIN_WINDOW))
    db.add_all([LoginFailure(key=account, failed_at=now), LoginFailure(key=address, failed_at=now)])
    db.commit()
    account_failures, _ = _recent(db, account, now)
    address_failures, _ = _recent(db, address, now)
    log.info("sign-in failed: %s from %s (%d in %s)", account, address, account_failures, LOGIN_WINDOW)
    if account_failures == ACCOUNT_LOCK_FAILURES:
        log.warning("sign-in locked for %s: %s after %d failures (from %s)", LOGIN_LOCK, account,
                    account_failures, address)
    if address_failures == ADDRESS_BLOCK_FAILURES:
        log.warning("sign-in blocked for %s: address %s after %d failures", LOGIN_LOCK, address, address_failures)


def clear_account(db: Session, account: str) -> None:
    """A successful sign-in clears the account's count (not the address's: one good login must not wipe
    the trail of names tried from that PC)."""
    db.execute(delete(LoginFailure).where(LoginFailure.key == account))


@dataclass
class _Bucket:
    tokens: float
    at: float


class RateLimiter:
    """Token bucket per key: `burst` calls at once, refilled at `per_minute`."""

    def __init__(self, per_minute: int, burst: int, clock: Callable[[], float] = time.monotonic):
        self.rate = per_minute / SECONDS_PER_MINUTE
        self.burst = burst
        self.clock = clock
        self._buckets: dict[str, _Bucket] = {}
        self._pruned = clock()

    def _tokens(self, bucket: _Bucket, now: float) -> float:
        return min(self.burst, bucket.tokens + (now - bucket.at) * self.rate)

    def take(self, key: str) -> float:
        """Spend one call: 0 when allowed, else the seconds until the next one is."""
        now = self.clock()
        self._prune(now)
        bucket = self._buckets.get(key)
        tokens = self.burst if bucket is None else self._tokens(bucket, now)
        if tokens >= 1:
            self._buckets[key] = _Bucket(tokens - 1, now)
            return 0.0
        self._buckets[key] = _Bucket(tokens, now)
        return (1 - tokens) / self.rate

    def _prune(self, now: float) -> None:
        if now - self._pruned < BUCKET_PRUNE_SECONDS:
            return
        self._pruned = now
        self._buckets = {k: b for k, b in self._buckets.items() if self._tokens(b, now) < self.burst}

    def reset(self) -> None:
        self._buckets.clear()
