"""Read commit history from a local clone with `git log`, and map commit identities to GitHub logins."""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

from .common import GitstatError, log, run

RS, US = "\x1e", "\x1f"
LOG_FORMAT = RS + US.join(["%H", "%aN", "%aE", "%aI", "%cI", "%s"])


def is_git_repo(path: str) -> bool:
    try:
        return run(["git", "rev-parse", "--is-inside-work-tree"], cwd=path).strip() == "true"
    except GitstatError:
        return False


def remote_matches(path: str, repo: str) -> bool:
    try:
        remotes = run(["git", "remote", "-v"], cwd=path).lower()
    except GitstatError:
        return False
    return repo.lower() in remotes or (repo.lower() + ".git") in remotes


def read_commits(path: str, since: datetime, until: datetime, ref: str | None = None,
                 all_branches: bool = False) -> list[dict]:
    cmd = [
        "git", "log", "--no-merges", "--use-mailmap", "--numstat",
        f"--since={since.isoformat()}", f"--until={until.isoformat()}",
        f"--format={LOG_FORMAT}",
    ]
    if all_branches:
        cmd.append("--all")
    elif ref:
        cmd.append(ref)
    out = run(cmd, cwd=path)

    commits = []
    for chunk in out.split(RS)[1:]:
        header, _, body = chunk.partition("\n")
        sha, name, email, authored, committed, subject = header.split(US, 5)
        adds = dels = files = 0
        for line in body.splitlines():
            parts = line.split("\t")
            if len(parts) != 3:
                continue
            a, d, _ = parts
            files += 1
            adds += int(a) if a.isdigit() else 0  # binary files show '-'
            dels += int(d) if d.isdigit() else 0
        commits.append({
            "sha": sha, "name": name, "email": email.lower(), "login": None,
            "authored": authored, "committed": committed,
            "additions": adds, "deletions": dels, "files": files, "subject": subject,
        })
    return commits


# ---------------------------------------------------------------- identity

_NOREPLY = re.compile(r"^(?:\d+\+)?([^@]+)@users\.noreply\.github\.com$")


class IdentityMap:
    """Resolves a commit's (name, email) to a GitHub login.

    Sources, in priority order: explicit aliases from a config file, logins GitHub
    attached to commits it knows about, GitHub noreply addresses, then a name match
    against known logins. Anything unresolved keeps the author's name.
    """

    def __init__(self, aliases: dict[str, str] | None = None):
        self.aliases = {k.lower(): v for k, v in (aliases or {}).items()}
        self.by_email: dict[str, str] = {}
        self.by_name: dict[str, str] = {}

    def learn(self, name: str | None, email: str | None, login: str | None) -> None:
        if not login:
            return
        if email:
            self.by_email.setdefault(email.lower(), login)
        if name:
            self.by_name.setdefault(name.lower(), login)

    def lookup(self, name: str, email: str, login: str | None = None) -> str | None:
        """The GitHub login for this identity, or None if it can't be determined."""
        email, lname = (email or "").lower(), (name or "").lower()
        if email in self.aliases:
            return self.aliases[email]
        if lname in self.aliases:
            return self.aliases[lname]
        if login:
            return self.aliases.get(login.lower(), login)
        if email in self.by_email:
            return self.by_email[email]
        m = _NOREPLY.match(email)
        if m:
            return m.group(1)
        return self.by_name.get(lname)

    def resolve(self, name: str, email: str, login: str | None = None) -> str:
        return self.lookup(name, email, login) or name or email or "unknown"


def load_config(path: str | None, repo_path: str | None = None) -> dict:
    """Load .gitstat.json.

    {"aliases": {"email-or-name": "login"},   # merge commit identities into a login
     "names": {"login": "Display Name"},      # how people are shown in reports
     "fetch_names": false,                    # fill missing names from GitHub profiles
     "exclude": ["login", ...]}
    """
    if path:
        candidates = [Path(path).expanduser()]
    else:
        candidates = [Path(".gitstat.json"), Path.home() / ".gitstat.json"]
        if repo_path:
            candidates.insert(1, Path(repo_path) / ".gitstat.json")
    for p in dict.fromkeys(c.resolve() for c in candidates):
        if p.exists():
            try:
                config = json.loads(p.read_text())
            except json.JSONDecodeError as exc:
                raise GitstatError(f"invalid JSON in {p}: {exc}") from exc
            log(f"config: using {p}")
            return config
    if path:
        raise GitstatError(f"config file not found: {path}")
    stray = [p for p in (Path("names.json"), Path(repo_path or ".") / "names.json") if p.exists()]
    if stray:
        log(f"config: found {stray[0]} but gitstat reads .gitstat.json; rename it "
            f"(e.g. mv {stray[0]} ~/.gitstat.json) or pass --config {stray[0]}")
    return {}
