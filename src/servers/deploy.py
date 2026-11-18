"""deploy_project — a guided deploy on the user's Linux server (ISSUES_LOG O94, O95).

"go to the uk server, softwaredevelopemnen folder, pull the latest branch and deploy" → fixed steps, not a free
model loop, each one asked on a choice card in the chat:

1. Project: the git repositories (and /var/www folders) on the server are listed and the typed name is matched to
   them. One clear match is used; two or more close ones → "which one?"; none → every project to pick from.
2. Branch: `git fetch --all --prune`, then the remote branches newest first with how many commits each is ahead
   → "pull which branch?". Uncommitted changes on the server are asked about first (stash / pull anyway / stop).
3. Dependencies: the dependency files the pull changed (package.json, a lock file, requirements.txt,
   composer.json), and Node folders with no node_modules → "install in which folders?" (frontend, backend, root…).
4. Build: Node folders with a `build` script → "build which?".
5. Restart: pm2 processes and systemd services running from the project, and nginx when a site points at it
   → "restart which?". Then their state is read back.

A step that fails stops the deploy before anything is restarted, so the running site keeps the last good build.
Every changing command passes the deny-list (server_ops.destructive_for) and the command log; on an audit-mode
(read-only) server nothing is changed. Clicking an option on the card is the approval for the commands it names.
"""

from __future__ import annotations

import difflib
import json
import logging
import posixpath
import re
import shlex
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from src.core.config import Config
from src.servers.server_ops import (CONFIRM_TIMEOUT, CommandRun, RemoteSession, _commands_block,
                                    destructive_for, open_session, release_session)
from src.servers.server_vault import ServerLogin, VaultError
from src.servers.linux_audit import AuditError
from src.tools.tool_run import ToolRun

logger = logging.getLogger(__name__)

# where projects live on a typical web server; depth 3 reaches /home/<user>/<project>/.git
PROJECT_ROOTS = ("/var/www", "/srv", "/opt", "/home", "/root")
PROJECT_DEPTH = 3
# a typed name this similar to a folder's name (difflib ratio, 0–1) counts as meaning it: "clickmaster" vs
# "Clickmastersoftware" is 0.73, "softwaredevelopemnen" vs "softwaredevelopment" 0.92, vs "Clickmastersoftware" 0.41
CLOSE_MATCH = 0.6
MAX_OPTIONS = 15                # projects or branches on one card: more is a list nobody reads
MAX_BRANCHES = 8                # branches whose "commits ahead" are counted (one git call each)
MANIFEST_DEPTH = 4              # project/apps/web/package.json
# how long an install or a build may print nothing: a Next.js build is silent for minutes on a small server
LONG_SILENCE = 900
FETCH_SILENCE = 120             # git fetch over a slow link
LOG_LINES = 10                  # new commits listed in the report

# the files that say what a folder depends on; a pull that changes one means an install
DEPENDENCY_FILES = frozenset({"package.json", "package-lock.json", "yarn.lock", "pnpm-lock.yaml",
                              "requirements.txt", "composer.json", "composer.lock"})
# folders that hold installed packages or history, not sources: listed (their presence matters) but not entered
SKIPPED_DIRS = ("node_modules", ".git", "vendor", ".venv", "venv")

STOP, SKIP = "stop", "skip"


@dataclass
class Option:
    id: str
    label: str
    detail: str = ""
    checked: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {"id": self.id, "label": self.label, "detail": self.detail, "checked": self.checked}


# ask(question, options, multi) → the picked option ids, or None (no answer in time / the job was stopped)
Ask = Callable[[str, list[Option], bool], "list[str] | None"]


class Stopped(Exception):
    """The deploy ends here; the message says why (shown in the report)."""


@dataclass
class Branch:
    ref: str                    # origin/main
    when: int                   # unix time of its last commit
    ago: str                    # "2 hours ago"
    subject: str
    ahead: int | None = None    # commits it has that HEAD does not

    @property
    def remote(self) -> str:
        return self.ref.split("/", 1)[0]

    @property
    def name(self) -> str:
        return self.ref.split("/", 1)[1] if "/" in self.ref else self.ref


@dataclass
class Folder:
    """A folder of the project that has its own dependencies (the root, frontend/, backend/ …)."""
    path: str                   # absolute
    rel: str                    # relative to the project ("." = the project itself)
    names: set[str] = field(default_factory=set)        # the manifest / lock files and package dirs it holds
    build: bool = False         # package.json has a "build" script

    @property
    def node(self) -> bool:
        return "package.json" in self.names

    @property
    def manager(self) -> str:
        return "pnpm" if "pnpm-lock.yaml" in self.names else "yarn" if "yarn.lock" in self.names else "npm"

    def install_steps(self) -> list[tuple[str, bool]]:
        """(command, is a Node command — needs nvm's PATH) for each kind of dependency the folder has."""
        out = []
        if self.node:
            out.append(({"pnpm": "pnpm install --frozen-lockfile", "yarn": "yarn install --frozen-lockfile"}.get(
                self.manager, "npm ci" if "package-lock.json" in self.names else "npm install"), True))
        if "requirements.txt" in self.names:
            pip = (f"{self.path}/.venv/bin/pip" if ".venv" in self.names
                   else f"{self.path}/venv/bin/pip" if "venv" in self.names else "pip3")
            out.append((f"{shlex.quote(pip)} install -r requirements.txt", False))
        if "composer.json" in self.names:
            out.append(("composer install --no-dev --optimize-autoloader --no-interaction", False))
        return out

    def install_commands(self) -> list[str]:
        return [command for command, _node in self.install_steps()]

    @property
    def shown(self) -> str:
        return "the project root" if self.rel == "." else f"{self.rel}/"


# ── reading the server's answers (pure: tested without a server) ─────────

def parse_projects(output: str) -> list[str]:
    """`find … -name .git` and `ls -d /var/www/*/` lines → project folders, in order, once each."""
    out: dict[str, None] = {}
    for line in output.splitlines():
        path = line.strip().rstrip("/")
        if not path.startswith("/"):
            continue
        if path.endswith("/.git"):
            path = path[: -len("/.git")]
        if path and path not in PROJECT_ROOTS and not any(f"/{d}/" in f"{path}/" for d in SKIPPED_DIRS):
            out[path] = None
    return list(out)


def _norm(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


def rank_projects(typed: str, paths: list[str]) -> list[tuple[float, str]]:
    """Every project with how well its folder name matches the typed name, best first."""
    want = _norm(typed)

    def score(path: str) -> float:
        have = _norm(posixpath.basename(path))
        if not want or not have:
            return 0.0
        if want == have:
            return 1.0
        ratio = difflib.SequenceMatcher(None, want, have).ratio()
        # "clickmaster" inside "clickmastersoftware": a part of the name is as good as a close spelling
        return max(ratio, CLOSE_MATCH) if len(want) >= 4 and (want in have or have in want) else ratio

    return sorted(((score(p), p) for p in paths), key=lambda sp: (-sp[0], sp[1]))


def parse_branches(output: str) -> list[Branch]:
    """`git for-each-ref --format='%(refname:short)<TAB>%(committerdate:unix)<TAB>%(committerdate:relative)
    <TAB>%(subject)' refs/remotes` → branches, newest first; origin/HEAD (a pointer, not a branch) left out."""
    out = []
    for line in output.splitlines():
        parts = line.split("\t", 3)
        if len(parts) < 3 or "/" not in parts[0] or parts[0].endswith("/HEAD"):
            continue
        try:
            when = int(parts[1])
        except ValueError:
            continue
        out.append(Branch(parts[0], when, parts[2], parts[3] if len(parts) > 3 else ""))
    return sorted(out, key=lambda b: -b.when)


def parse_folders(project: str, output: str) -> list[Folder]:
    """The project's `find` of manifests, lock files and package dirs → its folders with dependencies."""
    folders: dict[str, Folder] = {}
    for line in output.splitlines():
        path = line.strip()
        if not path.startswith(project):
            continue
        parent, name = posixpath.split(path)
        rel = posixpath.relpath(parent, project)
        if rel.startswith(".."):
            continue
        folders.setdefault(parent, Folder(parent, rel)).names.add(name)
    return [f for f in sorted(folders.values(), key=lambda f: (f.rel != ".", f.rel)) if f.install_commands()]


def dependency_changes(folder: Folder, changed: list[str]) -> list[str]:
    """The dependency files of this folder that the pull changed (paths relative to the project)."""
    return [f for f in changed if posixpath.dirname(f) == ("" if folder.rel == "." else folder.rel)
            and posixpath.basename(f) in DEPENDENCY_FILES]


def touches(folder: Folder, changed: list[str]) -> bool:
    return folder.rel == "." and bool(changed) or any(f.startswith(folder.rel + "/") for f in changed)


def parse_pm2(output: str) -> list[dict[str, str]]:
    """`pm2 jlist` → [{name, cwd, status}]; pm2 may print a banner before the JSON ("[PM2] Spawning…", whose
    bracket is not the list's)."""
    rows: Any = []
    for start in (m.start() for m in re.finditer(r"\[", output)):
        try:
            rows = json.loads(output[start:])
            break
        except json.JSONDecodeError:
            continue
    if not isinstance(rows, list):
        return []
    return [{"name": str(r.get("name", "")), "cwd": str((r.get("pm2_env") or {}).get("pm_cwd", "")),
             "status": str((r.get("pm2_env") or {}).get("status", ""))} for r in rows if isinstance(r, dict)]


def _inside(path: str, project: str) -> bool:
    path = path.rstrip("/")
    return path == project or path.startswith(project + "/")


def _tail(text: str, lines: int = 25) -> str:
    return "\n".join(text.strip().splitlines()[-lines:])


# ── the deploy ───────────────────────────────────────────────────────────

class Deploy:
    def __init__(self, session: RemoteSession, login: ServerLogin, *, ask: Ask,
                 status: Callable[[str], None] = lambda message: None,
                 log: Callable[[str, str, CommandRun | None], None] = lambda command, decision, run: None,
                 should_continue: Callable[[], bool] = lambda: True):
        self.session, self.login, self.ask, self.status, self.log = session, login, ask, status, log
        self.should_continue = should_continue
        self.lines: list[str] = []            # the report, one line per step
        self.project = ""
        self._node = ""

    # commands ------------------------------------------------------------
    def read(self, command: str, *, silence: int | None = None) -> CommandRun:
        return self.session.execute(command, **({"silence": silence} if silence else {}))

    def change(self, command: str, *, silence: int | None = None) -> CommandRun:
        """A changing command the user picked on a card: deny-list, the server's mode, then run and log it."""
        if not self.should_continue():
            raise Stopped("you stopped the job")
        deny = destructive_for(command, "linux")
        if deny:
            self.log(command, "refused", None)
            raise Stopped(f"`{command}` was not run: it {deny}")
        if self.login.mode == "audit":
            raise Stopped(f"this server is read-only (audit mode), so `{command}` was not run. Switch it to "
                          "'confirm' in Settings → Devices to deploy")
        run = self.session.execute(command, keep_end=True, **({"silence": silence} if silence else {}))
        self.log(command, "confirmed" if self.login.mode == "confirm" else "auto", run)
        return run

    def git(self, args: str) -> str:
        # safe.directory: root running git in a repository another user owns is refused otherwise ("dubious
        # ownership"); given per command, the server's git config is not changed
        q = shlex.quote(self.project)
        return f"git -c safe.directory={q} -C {q} {args}"

    def node(self, command: str) -> str:
        """npm / pm2 installed with nvm are not on a non-interactive shell's PATH: add the newest nvm node."""
        if not self._node:
            found = self.read("command -v npm || ls -d $HOME/.nvm/versions/node/*/bin 2>/dev/null | sort -V | "
                              "tail -n 1").output.strip().splitlines()
            path = found[-1] if found else ""
            self._node = f"export PATH={shlex.quote(path)}:$PATH && " if path.endswith("/bin") else " "
        return f"{self._node.strip()} {command}".strip()

    def choose(self, question: str, options: list[Option], *, multi: bool = False) -> list[str]:
        picked = self.ask(question, options, multi)
        if picked is None:
            raise Stopped("no answer within the time limit")
        known = {o.id for o in options}
        picked = [p for p in picked if p in known]
        if not multi and not picked:
            raise Stopped("no option was picked")
        if STOP in picked:
            raise Stopped("you chose to stop")
        return picked

    # steps ---------------------------------------------------------------
    def find_project(self, typed: str) -> str:
        self.status("Looking for the projects on the server…")
        roots = " ".join(PROJECT_ROOTS)
        listing = self.read(f"find {roots} -maxdepth {PROJECT_DEPTH} -type d -name .git -prune -print 2>/dev/null; "
                            "ls -1d /var/www/*/ 2>/dev/null").output
        projects = parse_projects(listing)
        if not projects:
            raise Stopped(f"no projects were found in {', '.join(PROJECT_ROOTS)} (no git repository, nothing in "
                          "/var/www). Say where the project is, or ask me to clone it")
        if len(projects) == 1:
            return projects[0]
        ranked = rank_projects(typed, projects)
        close = [p for s, p in ranked if s >= CLOSE_MATCH]
        if ranked[0][0] == 1.0 or len(close) == 1:
            return close[0]
        if close:
            question = f"Which project do you mean by “{typed}”?"
            shown = close
        else:
            question = (f"No project is called “{typed}”. Which one is it?" if typed
                        else "Which project should I deploy?")
            shown = [p for _s, p in ranked]
        options = [Option(str(i), posixpath.basename(p), p) for i, p in enumerate(shown[:MAX_OPTIONS])]
        options.append(Option(STOP, "None of these — stop",
                              "To start a new one, ask me to create the folder or clone the repository"))
        return shown[int(self.choose(question, options)[0])]

    def pull(self, hint: str) -> tuple[str, str]:
        """Fetch, ask which branch, pull it. → (HEAD before, HEAD after); equal when nothing was pulled."""
        self.status("Fetching every branch…")
        fetched = self.read(self.git("fetch --all --prune 2>&1"), silence=FETCH_SILENCE)
        if fetched.timed_out or fetched.code:
            raise Stopped(f"`git fetch` failed, so the branches could not be compared:\n```\n"
                          f"{_tail(fetched.output) or 'no output'}\n```")
        current = self.read(self.git("rev-parse --abbrev-ref HEAD")).output.strip()
        before = self.read(self.git("rev-parse HEAD")).output.strip()
        branches = parse_branches(self.read(self.git(
            "for-each-ref --sort=-committerdate refs/remotes "
            "--format='%(refname:short)%09%(committerdate:unix)%09%(committerdate:relative)%09%(subject)'")).output)
        if not branches:
            raise Stopped("the repository has no remote branches to pull from")
        for branch in branches[:MAX_BRANCHES]:
            counted = self.read(self.git(f"rev-list --count HEAD..{shlex.quote(branch.ref)}"))
            branch.ahead = int(counted.output.strip()) if counted.code == 0 and counted.output.strip().isdigit() \
                else None

        options = []
        for i, b in enumerate(branches[:MAX_OPTIONS]):
            notes = [f"{b.ahead} new commit{'s' if b.ahead != 1 else ''}" if b.ahead else
                     "nothing new" if b.ahead == 0 else "", f"last commit {b.ago}",
                     f"“{b.subject[:60]}”" if b.subject else ""]
            tags = [t for t in ("newest" if i == 0 else "", "current branch" if b.name == current else "",
                                "you named it" if hint and hint.lower() in (b.name.lower(), b.ref.lower()) else "")
                    if t]
            options.append(Option(str(i), b.ref + (f" ({', '.join(tags)})" if tags else ""),
                                  " · ".join(n for n in notes if n)))
        options += [Option(SKIP, "Don't pull — go on to install / build / restart"), Option(STOP, "Stop")]
        newest = branches[0]
        lead = (f"The newest branch is **{newest.ref}** (last commit {newest.ago}). You are on **{current}**."
                if newest.name != current else f"You are on **{current}**, which is also the newest branch.")
        picked = self.choose(f"{lead} Pull which branch?", options)[0]
        if picked == SKIP:
            self.lines.append(f"Not pulled: you kept **{current}** as it is.")
            return before, before
        branch = branches[int(picked)]

        dirty = [line for line in self.read(self.git("status --porcelain")).output.splitlines()
                 if line.strip() and not line.startswith("??")]
        if dirty:
            files = ", ".join(f"`{line[3:]}`" for line in dirty[:MAX_OPTIONS])
            answer = self.choose(
                f"{len(dirty)} file(s) were changed on the server and not committed: {files}. A pull may refuse "
                "to overwrite them.",
                [Option("stash", "Put them aside (git stash), then pull",
                        "They stay in the stash; `git stash pop` brings them back"),
                 Option("pull", "Pull anyway"), Option(STOP, "Stop")])[0]
            if answer == "stash":
                stashed = self.change(self.git(f"stash push -m {shlex.quote('before deploy ' + time.strftime('%F %T'))}"))
                if stashed.code:
                    raise Stopped(f"`git stash` failed:\n```\n{_tail(stashed.output)}\n```")
                self.lines.append(f"Put {len(dirty)} uncommitted change(s) aside: `git stash list` shows them.")

        q = shlex.quote
        pull = self.git(f"pull --ff-only {q(branch.remote)} {q(branch.name)} 2>&1")
        command = pull if branch.name == current else f"{self.git(f'checkout {q(branch.name)} 2>&1')} && {pull}"
        self.status(f"Pulling {branch.ref}…")
        run = self.change(command, silence=FETCH_SILENCE)
        if run.timed_out or run.code:
            raise Stopped(f"the pull from **{branch.ref}** failed (it only fast-forwards, so nothing was merged):"
                          f"\n```\n{_tail(run.output)}\n```")
        after = self.read(self.git("rev-parse HEAD")).output.strip()
        if after == before:
            self.lines.append(f"Pulled **{branch.ref}**: already up to date.")
        else:
            log = self.read(self.git(f"log --oneline -n {LOG_LINES} {before}..{after}")).output.strip()
            count = self.read(self.git(f"rev-list --count {before}..{after}")).output.strip()
            self.lines.append(f"Pulled **{branch.ref}**: {count} new commit(s)\n```\n{log}\n```")
        return before, after

    def folders(self) -> list[Folder]:
        names = " -o ".join(f"-name {shlex.quote(n)}" for n in sorted(DEPENDENCY_FILES))
        skipped = " -o ".join(f"-name {shlex.quote(d)}" for d in SKIPPED_DIRS)
        q = shlex.quote(self.project)
        found = parse_folders(self.project, self.read(
            f"find {q} -maxdepth {MANIFEST_DEPTH} \\( {skipped} \\) -prune -print -o \\( {names} \\) -print "
            "2>/dev/null").output)
        for folder in found:
            if folder.node:
                text = self.read(f"cat {shlex.quote(folder.path + '/package.json')}").output
                try:
                    folder.build = "build" in (json.loads(text).get("scripts") or {})
                except (json.JSONDecodeError, AttributeError):
                    folder.build = False
        return found

    def install(self, folders: list[Folder], changed: list[str]) -> list[Folder]:
        options = []
        for i, f in enumerate(folders):
            why = dependency_changes(f, changed)
            missing = f.node and "node_modules" not in f.names
            if why or missing:
                reason = ", ".join(posixpath.basename(w) for w in why) + " changed" if why else "no node_modules yet"
                options.append(Option(str(i), f"{f.shown} ({reason})", " && ".join(f.install_commands()), True))
        if not options:
            self.lines.append("Dependencies: no dependency file changed — nothing to install.")
            return []
        picked = self.choose("New dependencies were found. Install them in which folders?",
                             options + [Option(STOP, "Stop here")], multi=True)
        done = []
        for i in picked:
            folder = folders[int(i)]
            self.status(f"Installing in {folder.shown}…")
            for command, is_node in folder.install_steps():
                full = f"cd {shlex.quote(folder.path)} && {self.node(command) if is_node else command}"
                run = self.change(full + " 2>&1", silence=LONG_SILENCE)
                if run.timed_out or run.code:
                    raise Stopped(f"installing in {folder.shown} failed (`{command}`), so nothing was built or "
                                  f"restarted:\n```\n{_tail(run.output)}\n```")
            done.append(folder)
            self.lines.append(f"Installed dependencies in {folder.shown} (`{' && '.join(folder.install_commands())}`).")
        if not picked:
            self.lines.append("Dependencies: you chose not to install.")
        return done

    def build(self, folders: list[Folder], changed: list[str], installed: list[Folder]) -> None:
        buildable = [f for f in folders if f.build]
        if not buildable:
            self.lines.append("Build: no folder has a `build` script.")
            return
        options = [Option(str(i), f.shown, f"{f.manager} run build", touches(f, changed) or f in installed)
                   for i, f in enumerate(buildable)]
        picked = self.choose("Build which folders?", options + [Option(STOP, "Stop here")], multi=True)
        for i in picked:
            folder = buildable[int(i)]
            self.status(f"Building {folder.shown}… (this can take a few minutes)")
            started = time.monotonic()
            run = self.change(f"cd {shlex.quote(folder.path)} && {self.node(folder.manager + ' run build')} 2>&1",
                              silence=LONG_SILENCE)
            if run.timed_out or run.code:
                raise Stopped(f"the build of {folder.shown} failed, so nothing was restarted — the site still runs "
                              f"the previous build:\n```\n{_tail(run.output)}\n```")
            self.lines.append(f"Built {folder.shown} in {time.monotonic() - started:.0f} s.")
        if not picked:
            self.lines.append("Build: you chose not to build.")

    def restart(self) -> None:
        self.status("Looking for what runs the project (pm2, systemd, nginx)…")
        q = shlex.quote(self.project)
        pm2 = parse_pm2(self.read(self.node("pm2 jlist 2>/dev/null")).output)
        units = [posixpath.basename(u).removesuffix(".service") for u in self.read(
            f"grep -ls -- {shlex.quote('WorkingDirectory=' + self.project)} /etc/systemd/system/*.service "
            "2>/dev/null").output.split()]
        nginx_sites = self.read(f"grep -rls -- {q} /etc/nginx/sites-enabled /etc/nginx/conf.d 2>/dev/null").output
        has_nginx = bool(self.read("command -v nginx").output.strip())

        options: list[Option] = []
        commands: dict[str, str] = {}
        ours = [p for p in pm2 if _inside(p["cwd"], self.project)]
        for p in ours or pm2:                       # none from this folder: every pm2 process, none ticked
            oid = f"pm2:{p['name']}"
            commands[oid] = self.node(f"pm2 restart {shlex.quote(p['name'])}")
            options.append(Option(oid, f"pm2 {p['name']} ({p['status'] or '?'})", commands[oid], bool(ours)))
        for unit in units:
            oid = f"systemd:{unit}"
            commands[oid] = f"systemctl restart {shlex.quote(unit)}"
            options.append(Option(oid, f"service {unit}", commands[oid], True))
        if has_nginx:
            commands["nginx"] = "nginx -t 2>&1 && systemctl reload nginx"
            options.append(Option("nginx", "nginx (check the config, then reload)", commands["nginx"],
                                  bool(nginx_sites.strip())))
        if not options:
            self.lines.append("Restart: nothing found that runs this project (no pm2 process, systemd service or "
                              "nginx) — restart it yourself if it needs one.")
            return
        picked = self.choose("Restart which?", options + [Option(STOP, "Stop here")], multi=True)
        for oid in picked:
            self.status(f"Restarting {oid.split(':')[-1]}…")
            run = self.change(commands[oid] + " 2>&1")
            if run.timed_out or run.code:
                raise Stopped(f"`{commands[oid]}` failed:\n```\n{_tail(run.output)}\n```")
        if not picked:
            self.lines.append("Restart: you chose not to restart anything.")
            return
        names = [oid.split(":", 1)[1] for oid in picked if oid.startswith("pm2:")]
        state = {p["name"]: p["status"] for p in parse_pm2(self.read(self.node("pm2 jlist 2>/dev/null")).output)}
        shown = [f"pm2 {n}: **{state.get(n, '?')}**" for n in names]
        for oid in picked:
            if oid.startswith("systemd:"):
                unit = oid.split(":", 1)[1]
                shown.append(f"{unit}: **{self.read(f'systemctl is-active {shlex.quote(unit)}').output.strip()}**")
        if "nginx" in picked:
            shown.append("nginx: reloaded")
        self.lines.append("Restarted — " + "; ".join(shown) + ".")

    def run(self, project: str, branch: str = "") -> str:
        """The whole deploy → the report (Markdown)."""
        stopped = ""
        try:
            self.project = self.find_project(project)
            self.lines.append(f"Project: `{self.project}`.")
            before, after = self.pull(branch)
            changed = (self.read(self.git(f"diff --name-only {before} {after}")).output.split()
                       if before != after else [])
            folders = self.folders()
            installed = self.install(folders, changed)
            self.build(folders, changed, installed)
            self.restart()
        except Stopped as exc:
            stopped = str(exc)
        title = f"Deploy of **{posixpath.basename(self.project) or project or 'the project'}** on **{self.login.shown}**"
        body = "\n".join(f"- {line}" for line in self.lines)
        end = f"\n\n**Stopped:** {stopped}." if stopped else "\n\nDone."
        return f"{title}\n\n{body}{end}" if body else f"{title}{end}"


# ── the chat tool ────────────────────────────────────────────────────────

def run_deploy_project(
    query: str,
    *,
    job_id: str,
    config: Config,
    on_progress: Callable[[dict], None] | None = None,
    server: str | None = None,
    project: str | None = None,
    branch: str | None = None,
    user_id: int | None = None,
    **_: object,
) -> tuple[str, list[str], float]:
    from src.jobs import live
    from src.servers import server_vault
    from src.servers.command_audit import log_command

    emit = on_progress or (lambda event: None)
    run = ToolRun(on_progress, query=query, job_id=job_id, role="Deploy", model="deploy_project", accent="#c45c5c")
    errors: list[str] = []

    status = run.status
    try:
        if not config.security_scan_enabled:
            raise AuditError("server tools are switched off (SECURITY_SCAN_ENABLED=false)")
        login, ask_first = server_vault.login_for(user_id, server, query)
        if login is None:
            output = ask_first
        elif login.os_family == "windows":
            output = f"{login.shown} is a Windows server: the guided deploy works on Linux servers only."
        else:
            def ask(question: str, options: list[Option], multi: bool) -> list[str] | None:
                choice_id = uuid.uuid4().hex[:12]
                emit({"type": "choice_ask", "agent_id": "assistant", "choice_id": choice_id, "question": question,
                      "options": [o.as_dict() for o in options], "multi": multi, "server": login.shown})
                status("Waiting for your choice…")
                picked = live.wait_choice(job_id, choice_id, CONFIRM_TIMEOUT)
                # saved with the job's events, so a reloaded chat shows the card answered (as confirm cards, O66)
                emit({"type": "choice_resolved", "agent_id": "assistant", "choice_id": choice_id,
                      "answer": "timeout" if picked is None else "picked", "picked": picked or []})
                return picked

            def log(command: str, decision: str, run: CommandRun | None) -> None:
                log_command(config=config, user_id=user_id, job_id=job_id, server_label=login.label,
                            host=login.host, command=command, decision=decision,
                            exit_code=run.code if run else None, output=run.output if run else None)

            status(f"Logging in to {login.shown}…")
            session = open_session(login)
            try:
                deploy = Deploy(session, login, ask=ask, status=status, log=log,
                                should_continue=lambda: not live.is_stopped(job_id))
                output = deploy.run(project or "", branch or "") + _commands_block(session.runs)
            finally:
                release_session(session)
    except (AuditError, VaultError) as exc:
        output = f"Not run: {exc}."
        errors.append(str(exc))
    except Exception as exc:  # noqa: BLE001 — report, never crash the worker; type only (no secrets in messages)
        logger.error("deploy failed: %s", type(exc).__name__)
        output = f"The deploy failed ({type(exc).__name__})."
        errors.append(type(exc).__name__)
    return run.finish(output, errors)
