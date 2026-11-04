"""Module access (BACKLOG L19 D1c): what a member may use, set per user by an admin in Settings → Users.

One entry per module. A module is listed here only when the API enforces it (src.core.auth.require_module) —
a switch that only hides a tab would protect nothing. Admins have every module.
"""

from __future__ import annotations

import json
from dataclasses import dataclass


@dataclass(frozen=True)
class Module:
    id: str
    title: str
    description: str
    default: bool          # on for a new member (what members could already do stays on)


MODULES = (
    Module("devices", "Devices",
           "Add servers, camera recorders, routers and switches in Settings → Devices, and let the agent read them",
           False),
    Module("skills", "Skills", "Run skills in chat (/name) and keep their own in Settings → Skills", True),
    Module("usage", "Usage", "See their own token usage in Settings → Usage", True),
)
MODULE_IDS = tuple(m.id for m in MODULES)
DEFAULT_MODULES_JSON = json.dumps([m.id for m in MODULES if m.default])


def parse(modules_json: str | None) -> list[str]:
    """The stored list, keeping only modules that still exist, in MODULES order."""
    try:
        stored = set(json.loads(modules_json or "[]"))
    except (TypeError, ValueError):
        stored = set()
    return [m for m in MODULE_IDS if m in stored]


def dump(modules: list[str]) -> str:
    unknown = sorted(set(modules) - set(MODULE_IDS))
    if unknown:
        raise ValueError(f"unknown module(s): {', '.join(unknown)} — known: {', '.join(MODULE_IDS)}")
    return json.dumps([m for m in MODULE_IDS if m in modules])
