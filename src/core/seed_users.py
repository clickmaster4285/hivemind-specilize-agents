"""Create or update local user accounts from the command line (admin-provisioned sign-up).

Open self-serve registration is off, so accounts are made either from the admin Users panel in the
app or with this command. Use it to seed the initial IT users. Passwords are stored bcrypt-hashed;
the plaintext is read from ``--password``, the ``SEED_USER_PASSWORD`` env var, or an interactive
prompt, and is never printed or logged.

Run from the repo root (app database reachable):
    python -m src.core.seed_users --username alice --team IT
    python -m src.core.seed_users --username bob --team IT --role member --quota 100
    python -m src.core.seed_users --list

Re-running for an existing username updates that account (its role/team/quota, and its password when
one is given), so it is safe to run again. Password rules: at least 8 characters; required when the
account is new, optional when updating (blank keeps the current one).
"""

from __future__ import annotations

import argparse
import getpass
import os
import sys

from sqlalchemy import func, select

from src.core.auth import hash_password
from src.core.config import Config
from src.core.db import init_db, session_scope
from src.core.models_db import User

MIN_PASSWORD_LEN = 8


def _resolve_password(arg: str | None) -> str | None:
    """Plaintext password from the flag, the env var, or a prompt — or None when none was given."""
    password = arg if arg is not None else os.environ.get("SEED_USER_PASSWORD")
    if password is None and sys.stdin.isatty():
        entered = getpass.getpass("Password (blank to keep existing): ")
        password = entered or None
    return password


def seed_user(
    *,
    username: str,
    password: str | None,
    role: str = "member",
    team: str | None = None,
    email: str | None = None,
    phone: str | None = None,
    quota: int | None = None,
    config: Config | None = None,
) -> tuple[str, int]:
    """Create the user, or update an existing one. Returns ("created"|"updated", user_id)."""
    username = username.strip()
    if len(username) < 3:
        raise ValueError("username must be at least 3 characters")
    team = (team.strip() or None) if team else None
    email = (email.strip() or None) if email else None
    if email is not None and "@" not in email:
        raise ValueError("that does not look like an email address")
    phone = (phone.strip() or None) if phone else None

    config = config or Config.from_env()
    init_db(config)
    with session_scope(config) as session:
        user = session.scalar(select(User).where(User.username == username))
        action = "updated" if user is not None else "created"
        if password is not None and len(password) < MIN_PASSWORD_LEN:
            raise ValueError(f"password must be at least {MIN_PASSWORD_LEN} characters")
        if email is not None:
            clash = session.scalar(select(User).where(func.lower(User.email) == email.lower()))
            if clash is not None and (user is None or clash.id != user.id):
                raise ValueError("that email is already in use by another account")
        if user is None:
            if not password:
                raise ValueError("a password is required to create a new account")
            user = User(username=username, password_hash=hash_password(password), role=role)
            session.add(user)
        else:
            if password:
                user.password_hash = hash_password(password)
            user.role = role
        user.team = team
        user.email = email
        user.phone = phone
        if quota is not None:
            user.daily_job_quota = max(0, quota)
        session.flush()
        return action, user.id


def _print_users(config: Config) -> None:
    init_db(config)
    with session_scope(config) as session:
        users = session.scalars(select(User).order_by(User.created_at)).all()
        if not users:
            print("(no accounts yet)")
            return
        print(f"{'id':>4}  {'username':<20} {'role':<8} {'team':<12} {'email':<28} quota")
        for u in users:
            print(f"{u.id:>4}  {u.username:<20} {u.role:<8} {(u.team or '-'):<12} {(u.email or '-'):<28} {u.daily_job_quota}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--username", help="account to create or update")
    parser.add_argument("--password", help="plaintext password (else SEED_USER_PASSWORD or a prompt)")
    parser.add_argument("--role", choices=["member", "admin"], default="member")
    parser.add_argument("--team", default=None, help='e.g. "IT" or "marketing" — segments query analytics')
    parser.add_argument("--email", default=None, help="contact email (optional)")
    parser.add_argument("--phone", default=None, help="contact phone number (optional)")
    parser.add_argument("--quota", type=int, default=None, help="daily job quota (default: model default)")
    parser.add_argument("--list", action="store_true", help="list existing accounts and exit")
    args = parser.parse_args(argv)

    config = Config.from_env()
    if args.list:
        _print_users(config)
        return 0
    if not args.username:
        parser.error("--username is required (or use --list)")

    password = _resolve_password(args.password)
    try:
        action, uid = seed_user(
            username=args.username,
            password=password,
            role=args.role,
            team=args.team,
            email=args.email,
            phone=args.phone,
            quota=args.quota,
            config=config,
        )
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    suffix = f" [{args.team}]" if args.team else ""
    print(f"{action} user #{uid}: {args.username.strip()} ({args.role}){suffix}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
