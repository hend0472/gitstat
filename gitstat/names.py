"""Display names: show people as "Jane Doe" instead of logins like SSO numbers.

Renaming happens after metrics are computed, so it only affects presentation;
identity matching (aliases, emails) is unchanged.
"""

from __future__ import annotations

import json
import re
import subprocess
import time

from .common import log
from .ghdata import _cache_dir

_LOGIN = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9_-]{0,38})$")
_CACHE_TTL = 7 * 86400


def _cache_file():
    return _cache_dir() / "profile-names.json"


def fetch_profile_names(logins: list[str], use_cache: bool = True) -> dict[str, str]:
    """Look up the `name` field of GitHub profiles, 50 users per GraphQL request."""
    cache = {}
    if use_cache and _cache_file().exists():
        try:
            cache = json.loads(_cache_file().read_text())
        except (OSError, json.JSONDecodeError):
            cache = {}
    now = time.time()
    fresh = {k: v for k, v in cache.items() if now - v.get("at", 0) < _CACHE_TTL}
    todo = [l for l in logins if _LOGIN.match(l) and l.lower() not in fresh]

    if todo:
        log(f"gh: looking up profile names for {len(todo)} user(s)")
    for i in range(0, len(todo), 50):
        batch = todo[i:i + 50]
        fields = " ".join(f'u{j}: user(login: {json.dumps(l)}) {{ login name }}' for j, l in enumerate(batch))
        # Missing or renamed accounts make gh exit non-zero but still print the other users' data.
        proc = subprocess.run(["gh", "api", "graphql", "-f", f"query=query {{ {fields} }}"],
                              capture_output=True, text=True, check=False)
        try:
            data = json.loads(proc.stdout or "{}").get("data") or {}
        except json.JSONDecodeError:
            data = {}
        if not data:
            log(f"gh: profile lookup failed ({proc.stderr.strip()[:200]}); continuing without those names")
            continue
        for j, login in enumerate(batch):
            user = data.get(f"u{j}") or {}
            fresh[login.lower()] = {"name": (user.get("name") or "").strip(), "at": now}

    if use_cache:
        try:
            _cache_file().parent.mkdir(parents=True, exist_ok=True)
            _cache_file().write_text(json.dumps(fresh))
        except OSError:
            pass
    return {l: fresh[l.lower()]["name"] for l in logins if fresh.get(l.lower(), {}).get("name")}


def build_name_map(logins: list[str], configured: dict[str, str], profile: dict[str, str] | None = None) -> dict[str, str]:
    """login -> display name. Config wins over GitHub profiles; duplicates get the login appended."""
    lower_cfg = {k.lower(): v for k, v in (configured or {}).items() if v}
    mapping = {}
    # Keys that stay as-is are taken too, so a display name can never overwrite another person.
    used = {l.lower(): l for l in logins if not (lower_cfg.get(l.lower()) or (profile or {}).get(l))}
    for login in logins:
        name = lower_cfg.get(login.lower()) or (profile or {}).get(login)
        if not name or name == login:
            continue
        if name.lower() in used:
            name = f"{name} ({login})"
        used[name.lower()] = login
        mapping[login] = name
    return mapping


def apply_names(report: dict, mapping: dict[str, str]) -> None:
    """Re-key the report's developers by display name, keeping the login in `github_login`."""
    display = lambda login: mapping.get(login, login)
    renamed = {}
    for login, dev in report["developers"].items():
        dev["github_login"] = login
        dev["login"] = display(login)
        dev["interactions"] = {display(k): v for k, v in dev.get("interactions", {}).items()}
        renamed[dev["login"]] = dev
    report["developers"] = renamed


def match_by_profile_name(unmatched: list[str], profile: dict[str, str]) -> dict[str, str]:
    """Commit author name -> login, where the name equals exactly one GitHub profile name.

    Only full names (two or more words) are matched; a lone first name is too likely to collide.
    """
    by_name: dict[str, list[str]] = {}
    for login, name in profile.items():
        by_name.setdefault(name.lower(), []).append(login)
    return {n: by_name[n.lower()][0] for n in unmatched
            if len(n.split()) >= 2 and len(by_name.get(n.lower(), [])) == 1}


def looks_like_login(key: str) -> bool:
    return bool(_LOGIN.match(key))


def find_developer(report: dict, query: str) -> str | None:
    """Resolve a login or display name (case-insensitive) to the report key."""
    q = query.lower().lstrip("@")
    for key, dev in report["developers"].items():
        if key.lower() == q or dev.get("github_login", "").lower() == q:
            return key
    return None
