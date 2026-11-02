"""Tracked SEO projects (BACKLOG L15 P5) — Semrush-style projects, local.

- Every site audit a user runs is saved (score, areas, each check with its failing pages) — `seo_audits`
- The next audit of the same site says what changed: score trend, areas up/down, issues fixed, new and worse
- "Track it weekly": the site becomes a project (`seo_projects`) and is re-audited every N days by a background
  thread in the API process (crawl + checks only, no model), so the trend builds up on its own
- The `seo_projects` tool lists a user's tracked sites with their trend and latest changes, and stops tracking one
"""

from __future__ import annotations

import json
import logging
import threading
import time
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import Any

from src.brain.web import WebClient
from src.core.config import Config
from src.core.db import session_scope
from src.core.models_db import SeoAudit, SeoProject
from src.tools.site_audit import (AUDIT_AGENT, AuditError, Score, Site, crawl_site, resolve_start, score_site,
                                  site_url)

log = logging.getLogger("seo.projects")

ROLE = "SEO projects"
MAX_FAILING_SAVED = 50             # failing pages kept per check (enough to see what was fixed)
TREND_POINTS = 6
CHECK_MINUTES = 60                 # how often the tracker looks for projects that are due
STARTUP_DELAY_SECONDS = 120        # let the API finish starting first
MAX_EVERY_DAYS = 90


def _now() -> datetime:
    return datetime.now(timezone.utc)


def snapshot(score: Score, *, created_at: datetime | None = None) -> dict[str, Any]:
    """An audit as plain data: {created_at, total, level, areas, checks: {label: {area, score, failing}}}."""
    return {"created_at": created_at or _now(), "total": score.total, "level": score.level, "areas": score.areas,
            "checks": {r.label: {"area": r.area, "score": r.score, "failing": r.failing[:MAX_FAILING_SAVED]}
                       for r in score.results}}


def _row_snapshot(row: SeoAudit) -> dict[str, Any]:
    return {"created_at": row.created_at, "total": row.total, "level": row.level,
            "areas": json.loads(row.areas_json), "checks": json.loads(row.checks_json)}


def save_audit(config: Config, user_id: int, site: Site, score: Score, *, scheduled: bool = False) -> dict | None:
    """Save this audit → the previous audit of the same site by the same user (a snapshot), or None."""
    now = snapshot(score)
    with session_scope(config) as session:
        prev = (session.query(SeoAudit).filter_by(user_id=user_id, host=site.host)
                .order_by(SeoAudit.created_at.desc()).first())
        previous = _row_snapshot(prev) if prev else None
        session.add(SeoAudit(user_id=user_id, host=site.host, start_url=site.start, total=score.total,
                             level=score.level, pages=len(site.pages), areas_json=json.dumps(now["areas"]),
                             checks_json=json.dumps(now["checks"]), scheduled=scheduled))
    return previous


def _signed(n: int) -> str:
    return f"+{n}" if n > 0 else str(n)


def changes(prev: dict[str, Any], now: dict[str, Any]) -> str:
    """What changed between two audit snapshots: score, areas, and checks fixed / new / worse / better."""
    before = {c: v["score"] for c, v in prev["checks"].items()}
    after = {c: v["score"] for c, v in now["checks"].items()}
    fixed = [c for c, s in before.items() if s < 1 and after.get(c, 0) >= 1]
    new = [c for c, s in after.items() if s < 1 and before.get(c, 1) >= 1]
    worse = [c for c, s in after.items() if 0 < before.get(c, 1) < 1 and s < before[c]]
    better = [c for c, s in after.items() if before.get(c, 1) < s < 1]
    areas = [f"{a} {prev['areas'][a]} → {v}" for a, v in now["areas"].items()
             if a in prev["areas"] and prev["areas"][a] != v]
    lines = [f"**Since the audit of {prev['created_at']:%d %b %Y}:** {prev['total']} → {now['total']} "
             f"({_signed(now['total'] - prev['total'])})" + (f" · {', '.join(areas)}" if areas else "")]
    for label, items in (("✅ Fixed", fixed), ("🆕 New issues", new), ("📉 Worse", worse), ("📈 Better", better)):
        if items:
            lines.append(f"- {label}: {'; '.join(items)}")
    return "\n".join(lines)


def track(config: Config, user_id: int, site: Site, every_days: int) -> int:
    """Start (or change) tracking a site → the interval in days actually used."""
    every = max(1, min(MAX_EVERY_DAYS, every_days))
    with session_scope(config) as session:
        project = session.query(SeoProject).filter_by(user_id=user_id, host=site.host).first()
        if project is None:
            project = SeoProject(user_id=user_id, host=site.host, start_url=site.start)
            session.add(project)
        project.start_url, project.every_days = site.start, every
        project.next_run_at = _now() + timedelta(days=every)
    return every


def stop_tracking(config: Config, user_id: int, host: str) -> bool:
    with session_scope(config) as session:
        project = session.query(SeoProject).filter_by(user_id=user_id, host=host).first()
        if project is None:
            return False
        session.delete(project)
        return True


def projects_report(config: Config, user_id: int) -> str:
    with session_scope(config) as session:
        tracked = {p.host: (p.every_days, p.next_run_at)
                   for p in session.query(SeoProject).filter_by(user_id=user_id).all()}
        by_host: dict[str, list[dict[str, Any]]] = {}
        for row in session.query(SeoAudit).filter_by(user_id=user_id).order_by(SeoAudit.created_at.desc()):
            by_host.setdefault(row.host, []).append(_row_snapshot(row))
    if not by_host and not tracked:
        return "You have no SEO audits yet. Ask e.g. “audit example.com and track it weekly”."
    lines = ["## Your SEO projects", "", "| Site | Tracked | Last audit | Score trend (oldest → newest) |",
             "|---|---|---|---|"]
    for host in sorted(set(by_host) | set(tracked)):
        runs = by_host.get(host, [])
        every, next_run = tracked.get(host, (None, None))
        when = f"every {every} days, next {next_run:%d %b}" if every and next_run else "no"
        trend = " → ".join(str(r["total"]) for r in reversed(runs[:TREND_POINTS])) or "—"
        last = f"{runs[0]['created_at']:%d %b %Y} · {runs[0]['level']}" if runs else "—"
        lines.append(f"| {host} | {when} | {last} | {trend} |")
    for host, runs in sorted(by_host.items()):
        if len(runs) > 1:
            lines += ["", f"### {host}", "", changes(runs[1], runs[0])]
    return "\n".join(lines)


# ── the tracker: re-audit due projects in the background ────────────────

def audit_once(start: str, limit: int) -> tuple[Site, Score]:
    client = WebClient(user_agent=AUDIT_AGENT)
    site = crawl_site(client, resolve_start(client, start), limit=limit)
    if not site.loaded:
        raise AuditError(f"could not read {start}")
    return site, score_site(site)


def run_due(config: Config, *, audit: Callable[[str, int], tuple[Site, Score]] = audit_once) -> int:
    """Re-audit every project whose time has come → how many ran. Each is claimed (its next run moved on) before
    it runs, so a crash or a second process never audits it twice in a row."""
    with session_scope(config) as session:
        due = session.query(SeoProject).filter(SeoProject.next_run_at <= _now()).all()
        claimed = [(p.user_id, p.start_url) for p in due]
        for p in due:
            p.next_run_at = _now() + timedelta(days=p.every_days)
    ran = 0
    for user_id, start in claimed:
        try:
            site, score = audit(start, config.site_audit_max_pages)
            save_audit(config, user_id, site, score, scheduled=True)
            ran += 1
        except Exception as exc:  # noqa: BLE001 — one site failing must not stop the others
            log.warning("tracked SEO audit of %s failed: %s", start, exc)
    return ran


def start_scheduler(config: Config) -> threading.Thread:
    def loop() -> None:
        time.sleep(STARTUP_DELAY_SECONDS)
        while True:
            try:
                ran = run_due(config)
                if ran:
                    log.info("re-audited %d tracked sites", ran)
            except Exception:  # noqa: BLE001 — never let the thread die
                log.exception("SEO tracker crashed")
            time.sleep(CHECK_MINUTES * 60)

    thread = threading.Thread(target=loop, name="seo-tracker", daemon=True)
    thread.start()
    return thread


# ── the chat tool ──────────────────────────────────────────────────────

def run_seo_projects(query: str, *, job_id: str, config: Config, on_progress: Callable[[dict], None] | None = None,
                     user_id: int | None = None, untrack: str | None = None,
                     **_: object) -> tuple[str, list[str], float]:
    emit = on_progress or (lambda event: None)
    agent = {"agent_id": "assistant", "name": "Assistant", "role": ROLE, "stage": "chat", "accent": "#c45c8a",
             "job_id": job_id}
    started = time.monotonic()
    emit({"type": "session_start", "mode": "chat", "query": query})
    emit({"type": "agent_start", **agent, "model": "seo-projects"})
    errors: list[str] = []
    if user_id is None:
        errors.append("not signed in")
        output = "Sign in to keep SEO projects."
    else:
        output = ""
        target = site_url(untrack or "")
        if target:
            host = target.split("://")[-1].split("/")[0].lower()
            bare = host.removeprefix("www.")
            stopped = any(stop_tracking(config, user_id, h) for h in dict.fromkeys([host, bare, f"www.{bare}"]))
            output = (f"Stopped tracking **{host}**." if stopped else f"**{host}** was not being tracked.") + "\n\n"
        output += projects_report(config, user_id)
    elapsed = time.monotonic() - started
    emit({"type": "agent_done", **agent, "elapsed_seconds": elapsed, "output": output})
    emit({"type": "session_done", "total_seconds": elapsed, "errors": errors, "mode": "chat"})
    return output, errors, elapsed

