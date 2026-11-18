"""Guided deploy (ISSUES_LOG O94, O95): folder from a misspelt name, branch choice, install / build / restart —
against a fake server (no SSH), with the user's card clicks scripted."""

from __future__ import annotations

import json
from dataclasses import dataclass, field

import pytest

from src.servers import deploy as dp
from src.servers.server_ops import CommandRun

PROJECT = "/var/www/Clickmastersoftware"


# ── reading the server's answers ───────────────────────────────────────

def test_projects_come_from_git_folders_and_var_www_once_each():
    listing = (f"{PROJECT}/.git\n/home/ana/tools/.git\n{PROJECT}/\n/var/www/html/\n"
               "/var/www/app/node_modules/x/.git\nfind: '/srv': Permission denied\n")
    assert dp.parse_projects(listing) == [PROJECT, "/home/ana/tools", "/var/www/html"]


def test_a_typed_name_is_matched_to_the_folder_names():
    paths = [PROJECT, "/var/www/html", "/home/ana/softwaredevelopment"]
    best = dp.rank_projects("softwaredevelopemnen", paths)
    assert best[0][1] == "/home/ana/softwaredevelopment" and best[0][0] >= dp.CLOSE_MATCH
    assert all(score < dp.CLOSE_MATCH for score, _p in best[1:])
    score, path = dp.rank_projects("clickmaster", paths)[0]
    assert path == PROJECT and score >= dp.CLOSE_MATCH                                # part of the name
    assert dp.rank_projects("ClickMasterSoftware", paths)[0] == (1.0, PROJECT)       # case and spacing ignored


def test_branches_newest_first_without_the_head_pointer():
    out = ("origin/HEAD\t1700000000\t3 days ago\t\n"
           "origin/master\t1700000000\t3 days ago\told work\n"
           "origin/feature/login\t1800000000\t2 hours ago\tadd login\tpage\n")
    branches = dp.parse_branches(out)
    assert [b.ref for b in branches] == ["origin/feature/login", "origin/master"]
    assert (branches[0].remote, branches[0].name, branches[0].subject) == ("origin", "feature/login", "add login\tpage")


def test_folders_know_their_install_command_and_what_the_pull_changed():
    found = dp.parse_folders(PROJECT, "\n".join([
        f"{PROJECT}/package.json", f"{PROJECT}/frontend/package.json", f"{PROJECT}/frontend/yarn.lock",
        f"{PROJECT}/frontend/node_modules", f"{PROJECT}/backend/requirements.txt", f"{PROJECT}/backend/.venv",
        f"{PROJECT}/docs/.git"]))
    assert [(f.rel, f.install_commands()) for f in found] == [
        (".", ["npm install"]),
        ("backend", [f"{PROJECT}/backend/.venv/bin/pip install -r requirements.txt"]),
        ("frontend", ["yarn install --frozen-lockfile"]),
    ]
    root, backend, frontend = found
    changed = ["frontend/yarn.lock", "frontend/src/app.tsx", "README.md"]
    assert dp.dependency_changes(frontend, changed) == ["frontend/yarn.lock"]
    assert dp.dependency_changes(root, changed) == [] and dp.dependency_changes(backend, changed) == []
    assert dp.touches(frontend, changed) and not dp.touches(backend, changed) and dp.touches(root, changed)


def test_pm2_list_after_a_banner():
    rows = [{"name": "web", "pm2_env": {"pm_cwd": f"{PROJECT}/frontend", "status": "online"}}]
    assert dp.parse_pm2("[PM2] Spawning PM2 daemon\n" + json.dumps(rows)) == [
        {"name": "web", "cwd": f"{PROJECT}/frontend", "status": "online"}]
    assert dp.parse_pm2("pm2: command not found") == []


# ── the whole deploy on a fake server ──────────────────────────────────

@dataclass
class FakeLogin:
    mode: str = "confirm"
    shown: str = "uk server"


@dataclass
class FakeServer:
    """execute(): the first rule whose text is in the command answers (code, output); the rest succeed silently."""
    rules: list = field(default_factory=list)
    ran: list[str] = field(default_factory=list)
    head: list[str] = field(default_factory=lambda: ["aaa111"])

    def execute(self, command, *, silence=None, keep_end=False):
        self.ran.append(command)
        if "rev-parse HEAD" in command:
            return CommandRun(command, 0, self.head[0])
        if "pull --ff-only" in command:
            self.head[0] = "bbb222"
        for text, code, output in self.rules:
            if text in command:
                return CommandRun(command, code, output)
        return CommandRun(command, 0, "")


PM2 = json.dumps([{"name": "web", "pm2_env": {"pm_cwd": f"{PROJECT}/frontend", "status": "online"}}])


def _server(**overrides) -> FakeServer:
    rules = {
        "-name .git -prune -print": (0, f"{PROJECT}/.git\n{PROJECT}/\n/var/www/html/\n"),
        "fetch --all": (0, "Fetching origin"),
        "rev-parse --abbrev-ref HEAD": (0, "master"),
        "for-each-ref": (0, "origin/HEAD\t1\tlong ago\t\norigin/master\t1700000000\t3 days ago\told\n"
                            "origin/main\t1800000000\t2 hours ago\tfix header"),
        "HEAD..origin/main": (0, "3"),
        "HEAD..origin/master": (0, "0"),
        "status --porcelain": (0, "?? tmp.log"),
        "log --oneline": (0, "bbb222 fix header"),
        "rev-list --count aaa111..bbb222": (0, "3"),
        "diff --name-only": (0, "frontend/package-lock.json\nfrontend/src/a.ts"),
        f"-maxdepth {dp.MANIFEST_DEPTH}": (0, f"{PROJECT}/frontend/package.json\n{PROJECT}/frontend/package-lock.json\n"
                                             f"{PROJECT}/frontend/node_modules\n{PROJECT}/backend/requirements.txt"),
        "frontend/package.json": (0, '{"scripts": {"build": "next build", "start": "next start"}}'),
        "command -v npm": (0, "/root/.nvm/versions/node/v22.11.0/bin"),
        "pm2 jlist": (0, "[PM2] banner\n" + PM2),
        "grep -ls": (0, ""),
        "grep -rls": (0, "/etc/nginx/sites-enabled/clickmaster"),
        "command -v nginx": (0, "/usr/sbin/nginx"),
        **overrides,
    }
    return FakeServer([(text, code, out) for text, (code, out) in rules.items()])


def _clicks(*answers):
    """Each answer: a function of the card's options → the ids picked. Every card asked is recorded."""
    asked = []

    def ask(question, options, multi):
        asked.append((question, options, multi))
        return answers[len(asked) - 1](options)
    return ask, asked


def by_label(*texts):
    return lambda options: [o.id for o in options if any(t in o.label for t in texts)]


def checked(options):
    return [o.id for o in options if o.checked]


def test_a_misspelt_folder_is_asked_then_pulled_installed_built_and_restarted():
    server = _server()
    ask, asked = _clicks(by_label("Clickmastersoftware"), by_label("origin/main"), checked, checked, checked)
    report = dp.Deploy(server, FakeLogin(), ask=ask).run("softwaredevelopemnen")

    questions = [q for q, _o, _m in asked]
    assert questions[0] == "No project is called “softwaredevelopemnen”. Which one is it?"
    assert [o.label for o in asked[0][1]] == ["Clickmastersoftware", "html", "None of these — stop"]
    assert "The newest branch is **origin/main**" in questions[1] and "You are on **master**" in questions[1]
    main = next(o for o in asked[1][1] if o.label.startswith("origin/main"))
    assert main.label == "origin/main (newest)" and main.detail.startswith("3 new commits")
    # install: only the folder whose lock file changed, ticked; the backend's requirements.txt did not change
    assert [(o.label, o.checked) for o in asked[2][1][:-1]] == [("frontend/ (package-lock.json changed)", True)]

    node = "export PATH=/root/.nvm/versions/node/v22.11.0/bin:$PATH &&"
    changes = [c for c in server.ran if any(w in c for w in ("checkout", "pull", "npm ci", "run build", "restart",
                                                             "reload"))]
    assert changes == [
        f"git -c safe.directory={PROJECT} -C {PROJECT} checkout main 2>&1 && "
        f"git -c safe.directory={PROJECT} -C {PROJECT} pull --ff-only origin main 2>&1",
        f"cd {PROJECT}/frontend && {node} npm ci 2>&1",
        f"cd {PROJECT}/frontend && {node} npm run build 2>&1",
        f"{node} pm2 restart web 2>&1",
        "nginx -t 2>&1 && systemctl reload nginx 2>&1",
    ]
    assert "Pulled **origin/main**: 3 new commit(s)" in report and "Restarted — pm2 web: **online**" in report
    assert report.endswith("Done.")


def test_a_failed_build_stops_before_anything_is_restarted():
    server = _server(**{"run build": (1, "Type error: x is not defined")})
    ask, asked = _clicks(by_label("origin/main"), checked, checked)
    report = dp.Deploy(server, FakeLogin(), ask=ask).run("clickmaster")         # one close match: no folder card
    assert asked[0][0].startswith("The newest branch")
    assert not any("restart" in c for c in server.ran)
    assert "the build of frontend/ failed, so nothing was restarted" in report and "Type error" in report


def test_uncommitted_changes_are_asked_about_and_can_be_stashed():
    server = _server(**{"status --porcelain": (0, " M src/app.ts\n?? tmp.log")})
    ask, asked = _clicks(by_label("origin/master"), by_label("git stash"), lambda o: [], lambda o: [], lambda o: [])
    dp.Deploy(server, FakeLogin(), ask=ask).run("click master software")
    assert asked[1][0].startswith("1 file(s) were changed on the server and not committed: `src/app.ts`")
    stash = next(i for i, c in enumerate(server.ran) if "stash push" in c)
    pull = next(i for i, c in enumerate(server.ran) if "pull --ff-only" in c)
    assert stash < pull and "checkout" not in server.ran[pull]          # master is the current branch


def test_a_read_only_server_is_never_changed():
    server = _server()
    ask, _asked = _clicks(by_label("origin/main"))
    report = dp.Deploy(server, FakeLogin(mode="audit"), ask=ask).run("Clickmastersoftware")
    assert not any("pull" in c or "checkout" in c for c in server.ran)
    assert "this server is read-only (audit mode)" in report


@pytest.mark.parametrize("answer, expected", [
    (None, "no answer within the time limit"),
    (["stop"], "you chose to stop"),
])
def test_no_answer_or_stop_ends_the_deploy(answer, expected):
    ask, _asked = _clicks(lambda options: answer)
    report = dp.Deploy(_server(), FakeLogin(), ask=ask).run("nothing like it")
    assert f"**Stopped:** {expected}." in report
