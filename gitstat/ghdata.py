"""Fetch pull request activity from GitHub via `gh api graphql`, with an on-disk cache."""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta
from pathlib import Path

from .common import GitstatError, log, now_utc, parse_ts, run

PR_QUERY = """
query($owner: String!, $name: String!, $pageSize: Int!, $cursor: String) {
  repository(owner: $owner, name: $name) {
    pullRequests(first: $pageSize, after: $cursor, orderBy: {field: UPDATED_AT, direction: DESC}) {
      pageInfo { hasNextPage endCursor }
      nodes {
        number title url state isDraft createdAt updatedAt mergedAt closedAt
        additions deletions changedFiles baseRefName
        author { __typename login }
        mergedBy { login }
        reviews(first: 60) {
          nodes { author { __typename login } state submittedAt }
        }
        comments(first: 80) {
          nodes { author { __typename login } createdAt }
        }
        reviewThreads(first: 60) {
          nodes {
            isResolved
            comments(first: 30) { nodes { author { __typename login } createdAt } }
          }
        }
        commits(first: 100) {
          totalCount
          nodes {
            commit {
              oid committedDate authoredDate additions deletions messageHeadline
              parents { totalCount }
              author { name email user { login } }
            }
          }
        }
        timelineItems(first: 80, itemTypes: [REVIEW_REQUESTED_EVENT, READY_FOR_REVIEW_EVENT, HEAD_REF_FORCE_PUSHED_EVENT]) {
          nodes {
            __typename
            ... on ReviewRequestedEvent {
              createdAt
              requestedReviewer { __typename ... on User { login } }
            }
            ... on ReadyForReviewEvent { createdAt }
            ... on HeadRefForcePushedEvent { createdAt actor { __typename login } }
          }
        }
      }
    }
  }
}
"""

HISTORY_QUERY = """
query($owner: String!, $name: String!, $since: GitTimestamp!, $until: GitTimestamp!, $cursor: String) {
  repository(owner: $owner, name: $name) {
    defaultBranchRef {
      target {
        ... on Commit {
          history(first: 100, after: $cursor, since: $since, until: $until) {
            pageInfo { hasNextPage endCursor }
            nodes {
              oid committedDate authoredDate additions deletions changedFilesIfAvailable
              messageHeadline
              parents { totalCount }
              author { name email date user { login } }
            }
          }
        }
      }
    }
  }
}
"""


def graphql(query: str, variables: dict) -> dict:
    cmd = ["gh", "api", "graphql", "-f", f"query={query}"]
    for key, val in variables.items():
        if val is None:
            continue
        flag = "-F" if isinstance(val, int) else "-f"
        cmd += [flag, f"{key}={val}"]
    data = json.loads(run(cmd))
    if data.get("errors"):
        raise GitstatError("GraphQL error: " + "; ".join(e.get("message", "") for e in data["errors"]))
    return data["data"]


def detect_repo(path: str | None) -> str:
    try:
        out = run(["gh", "repo", "view", "--json", "nameWithOwner", "-q", ".nameWithOwner"], cwd=path)
    except GitstatError as exc:
        raise GitstatError(
            "could not detect the GitHub repo; pass --repo OWNER/NAME or run inside a clone"
        ) from exc
    return out.strip()


# ---------------------------------------------------------------- cache

def _cache_dir() -> Path:
    base = os.environ.get("XDG_CACHE_HOME") or os.path.join(Path.home(), ".cache")
    return Path(base) / "gitstat"


def _cache_path(repo: str) -> Path:
    return _cache_dir() / (repo.replace("/", "__") + ".json")


# Bump when PR_QUERY gains fields, so older caches are refetched instead of silently lacking data.
CACHE_VERSION = 2


def _load_cache(repo: str) -> dict:
    path = _cache_path(repo)
    if not path.exists():
        return {}
    try:
        cache = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    if cache.get("version") != CACHE_VERSION:
        log("gh: cached data is from an older gitstat version; refetching")
        return {}
    return cache


def _save_cache(repo: str, cache: dict) -> None:
    path = _cache_path(repo)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(cache))
    tmp.replace(path)


# ---------------------------------------------------------------- fetching

def fetch_pull_requests(repo: str, since: datetime, use_cache: bool = True, page_size: int = 25) -> list[dict]:
    """Return every PR updated on/after `since` (plus cached older ones).

    PRs are paged newest-updated first; we stop once a page falls entirely before
    the point we need. With a warm cache we only pull what changed since last run.
    """
    owner, name = repo.split("/", 1)
    cache = _load_cache(repo) if use_cache else {}
    prs: dict[str, dict] = cache.get("prs", {})
    covered_since = parse_ts(cache.get("covered_since"))
    fetched_at = parse_ts(cache.get("fetched_at"))

    if covered_since and fetched_at and covered_since <= since:
        stop_at = fetched_at - timedelta(hours=1)
        log(f"gh: refreshing PRs updated since last run ({fetched_at:%Y-%m-%d %H:%M} UTC)")
    else:
        stop_at = since
        log(f"gh: fetching PRs updated since {since:%Y-%m-%d} for {repo}")

    started = now_utc()
    cursor, pages, fetched = None, 0, 0
    while True:
        try:
            data = graphql(PR_QUERY, {"owner": owner, "name": name, "pageSize": page_size, "cursor": cursor})
        except GitstatError as exc:
            # Large PRs can make the query time out; retry with a smaller page.
            if page_size > 5 and ("timeout" in str(exc).lower() or "502" in str(exc) or "something went wrong" in str(exc).lower()):
                page_size = max(5, page_size // 2)
                log(f"gh: query too heavy, retrying with page size {page_size}")
                continue
            raise
        repo_data = data.get("repository")
        if repo_data is None:
            raise GitstatError(f"repository {repo} not found or not accessible")
        conn = repo_data["pullRequests"]
        nodes = conn["nodes"]
        pages += 1
        for pr in nodes:
            prs[str(pr["number"])] = pr
        fetched += len(nodes)
        log(f"gh:   page {pages}: {fetched} PRs")
        if not conn["pageInfo"]["hasNextPage"] or not nodes:
            break
        if parse_ts(nodes[-1]["updatedAt"]) < stop_at:
            break
        cursor = conn["pageInfo"]["endCursor"]

    new_covered = min(since, covered_since) if covered_since and covered_since <= since else since
    if use_cache:
        _save_cache(repo, {
            "version": CACHE_VERSION,
            "prs": prs,
            "covered_since": new_covered.isoformat(),
            "fetched_at": started.isoformat(),
        })
    return list(prs.values())


def fetch_default_branch_commits(repo: str, since: datetime, until: datetime) -> list[dict]:
    """Commits on the default branch via the API (used when no local clone is available)."""
    owner, name = repo.split("/", 1)
    log("gh: fetching default-branch commit history (no local clone)")
    commits, cursor = [], None
    while True:
        data = graphql(HISTORY_QUERY, {
            "owner": owner, "name": name, "cursor": cursor,
            "since": since.isoformat(), "until": until.isoformat(),
        })
        ref = data["repository"]["defaultBranchRef"]
        if not ref:
            return []
        hist = ref["target"]["history"]
        for c in hist["nodes"]:
            if c["parents"]["totalCount"] > 1:
                continue  # skip merge commits, matching `git log --no-merges`
            author = c.get("author") or {}
            commits.append({
                "sha": c["oid"],
                "name": author.get("name") or "",
                "email": (author.get("email") or "").lower(),
                "login": (author.get("user") or {}).get("login"),
                "authored": author.get("date") or c["authoredDate"],
                "committed": c["committedDate"],
                "additions": c["additions"],
                "deletions": c["deletions"],
                "files": c.get("changedFilesIfAvailable") or 0,
                "subject": c["messageHeadline"],
            })
        if not hist["pageInfo"]["hasNextPage"]:
            break
        cursor = hist["pageInfo"]["endCursor"]
    return commits


def lookup_commit_logins(repo: str, shas: list[str], limit: int = 60) -> dict[str, str]:
    """Ask GitHub which account authored each commit (REST resolves verified emails)."""
    found = {}
    for sha in shas[:limit]:
        try:
            login = run(["gh", "api", f"repos/{repo}/commits/{sha}", "-q", ".author.login // empty"]).strip()
        except GitstatError:
            continue
        if login:
            found[sha] = login
    return found
