"""Command-line entry point."""

from __future__ import annotations

import argparse
import json
import os
from datetime import timedelta
import sys
from pathlib import Path

from . import __version__
from .common import GitstatError, log, now_utc, parse_when, run
from .ghdata import (
    detect_repo, fetch_default_branch_commits, fetch_pull_requests, lookup_commit_logins,
)
from .gitdata import IdentityMap, is_git_repo, load_config, read_commits, remote_matches
from .metrics import compute
from .periods import parse_anchor, parse_length, sprints, weekly
from .names import (
    apply_names, build_name_map, fetch_profile_names, find_developer, looks_like_login, match_by_profile_name,
)
from .render_html import render_html
from .render_text import render_csv, render_json, render_markdown, render_text

EPILOG = """examples:
  gitstat                                   # team overview for the repo in the current directory
  gitstat --since 30d --author octocat      # one developer's detailed report
  gitstat --me                              # your own report (uses your gh login)
  gitstat --repo org/api --path ~/src/api --since 2026-01-01 --format html -o team.html
  gitstat --format csv -o stats.csv         # spreadsheet export
  gitstat --sprint-start 2026-09-29 --sprint-length 2w --since 6s   # last 6 two-week sprints
"""


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="gitstat",
        description="Developer contribution analytics for a GitHub repository (PRs, reviews, comments, commits).",
        epilog=EPILOG, formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--repo", help="GitHub repo as OWNER/NAME (default: detected from --path)")
    p.add_argument("--path", default=".", help="local clone to read commits from (default: current directory)")
    p.add_argument("--since", default="90d",
                   help="window start: 30d, 12w, 6m, 1y, YYYY-MM-DD, or 6s for the last 6 sprints (default: 90d)")
    p.add_argument("--until", default=None, help="window end, same formats (default: now)")
    p.add_argument("--sprint-start", metavar="DATE",
                   help="any sprint's start date (past or future), e.g. 2026-09-29; trends are shown per sprint")
    p.add_argument("--sprint-length", metavar="LEN", help="sprint length, e.g. 2w or 14d")
    p.add_argument("--author", action="append", default=[], metavar="LOGIN",
                   help="show a detailed report for this GitHub login (repeatable)")
    p.add_argument("--me", action="store_true", help="show a detailed report for the authenticated gh user")
    p.add_argument("--all-details", action="store_true", help="detailed report for every developer")
    p.add_argument("--top", type=int, metavar="N", help="only list the N most active developers")
    p.add_argument("--format", choices=["text", "md", "csv", "json", "html"], default="text")
    p.add_argument("-o", "--output", help="write to this file instead of stdout")
    p.add_argument("--branch", help="branch/ref to read local commits from (default: HEAD)")
    p.add_argument("--all-branches", action="store_true", help="count local commits on all branches")
    p.add_argument("--api-commits", action="store_true",
                   help="read default-branch commits through the GitHub API even when a clone is present")
    p.add_argument("--fetch-names", action="store_true",
                   help="show GitHub profile names for people not listed under \"names\" in the config")
    p.add_argument("--no-names", action="store_true", help="show raw GitHub logins instead of display names")
    p.add_argument("--print-names", action="store_true",
                   help="print a \"names\" config block for everyone in the window, then exit")
    p.add_argument("--include-bots", action="store_true", help="include bot accounts")
    p.add_argument("--exclude", action="append", default=[], metavar="LOGIN", help="ignore this login (repeatable)")
    p.add_argument("--config", help="path to a .gitstat.json config with aliases/excludes")
    p.add_argument("--no-cache", action="store_true", help="ignore and don't write the PR cache")
    p.add_argument("--page-size", type=int, default=25, help="PRs per GraphQL page (lower it if queries time out)")
    p.add_argument("--no-color", action="store_true")
    p.add_argument("--version", action="version", version=f"gitstat {__version__}")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return _run(args)
    except GitstatError as exc:
        print(f"gitstat: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


def _resolve_unknown_authors(repo: str, prs: list[dict], commits: list[dict], identity: IdentityMap) -> None:
    for pr in prs:
        for node in (pr.get("commits") or {}).get("nodes", []):
            a = node["commit"].get("author") or {}
            identity.learn(a.get("name"), a.get("email"), (a.get("user") or {}).get("login"))
    sample: dict[str, dict] = {}
    for cm in commits:
        if cm["email"] and cm["email"] not in sample and not identity.lookup(cm["name"], cm["email"], cm.get("login")):
            sample[cm["email"]] = cm
    if not sample:
        return
    log(f"gh: looking up GitHub accounts for {len(sample)} unmatched commit email(s)")
    found = lookup_commit_logins(repo, [cm["sha"] for cm in sample.values()])
    for cm in sample.values():
        if cm["sha"] in found:
            identity.learn(cm["name"], cm["email"], found[cm["sha"]])


def _window(args, config: dict):
    """Resolve --since/--until and the trend buckets (weeks, or sprints when configured)."""
    until = parse_when(args.until) if args.until else now_utc()
    sprint = config.get("sprint") or {}
    start = args.sprint_start or sprint.get("start")
    length = args.sprint_length or sprint.get("length")
    if bool(start) != bool(length):
        raise GitstatError("sprints need both a start date and a length (--sprint-start and --sprint-length)")

    if not start:
        if args.since.lower().endswith("s") and args.since[:-1].isdigit():
            raise GitstatError("--since Ns counts sprints; set --sprint-start and --sprint-length first")
        since = parse_when(args.since, ref=until)
        if since >= until:
            raise GitstatError("--since must be before --until")
        return since, until, weekly(since, until)

    anchor, days = parse_anchor(start), parse_length(length)
    current = sprints(anchor, days, until, until).since  # start of the sprint containing `until`
    if args.since.lower().endswith("s") and args.since[:-1].isdigit():
        since = current - timedelta(days=days * (int(args.since[:-1]) - 1))
    else:
        since = parse_when(args.since, ref=until)
    if since >= until:
        raise GitstatError("--since must be before --until")
    periods = sprints(anchor, days, since, until)
    log(f"sprints: {days}-day sprints anchored on {start}; "
        f"{len(periods)} sprint(s) from {periods.to_json()[0]['label'].split('–')[0]} (current one in progress)")
    return periods.since, until, periods


def _run(args) -> int:
    path = str(Path(args.path).expanduser().resolve())
    local = is_git_repo(path)
    repo = args.repo or detect_repo(path if local else None)
    if "/" not in repo:
        raise GitstatError("--repo must look like OWNER/NAME")

    config = load_config(args.config, path if local else None)
    since, until, periods = _window(args, config)
    identity = IdentityMap(config.get("aliases"))
    exclude = set(args.exclude) | set(config.get("exclude", []))

    prs = fetch_pull_requests(repo, since, use_cache=not args.no_cache, page_size=args.page_size)

    if local and not args.api_commits and (args.repo is None or remote_matches(path, repo)):
        log(f"git: reading commits from {path}")
        commits = read_commits(path, since, until, ref=args.branch, all_branches=args.all_branches)
    else:
        if local and args.repo and not args.api_commits:
            log(f"git: {path} is not a clone of {repo}; falling back to the API")
        commits = fetch_default_branch_commits(repo, since, until)

    _resolve_unknown_authors(repo, prs, commits, identity)
    report = compute(prs, commits, since, until, identity,
                     include_bots=args.include_bots, exclude=exclude, periods=periods)

    fetch = args.print_names or (not args.no_names and (args.fetch_names or config.get("fetch_names", False)))
    profile = {}
    if fetch:
        logins = [l for l in report["developers"] if looks_like_login(l)]
        profile = fetch_profile_names(logins, use_cache=not args.no_cache)
        # Commits whose author name matches exactly one GitHub profile name belong to that account.
        unmatched = [k for k in report["developers"] if not looks_like_login(k)]
        matches = match_by_profile_name(unmatched, profile)
        if matches:
            for name, login in matches.items():
                log(f"names: matched commit author '{name}' to @{login} by GitHub profile name")
                identity.aliases[name.lower()] = login
            report = compute(prs, commits, since, until, identity,
                             include_bots=args.include_bots, exclude=exclude, periods=periods)

    if args.print_names:
        configured = {k.lower(): v for k, v in config.get("names", {}).items()}
        names = {l: configured.get(l.lower()) or profile.get(l) or ""
                 for l in report["developers"] if looks_like_login(l)}
        sys.stdout.write(json.dumps({"names": names}, indent=2, ensure_ascii=False) + "\n")
        leftovers = [k for k in report["developers"] if not looks_like_login(k)]
        log("fill in the blanks and add this block to .gitstat.json")
        if leftovers:
            log("these commit authors aren't linked to a GitHub login; map them under \"aliases\": " + ", ".join(leftovers))
        return 0
    if not args.no_names:
        apply_names(report, build_name_map(list(report["developers"]), config.get("names", {}), profile))

    wanted = list(args.author)
    if args.me:
        wanted.append(run(["gh", "api", "user", "-q", ".login"]).strip())
    detail = []
    for query in wanted:
        key = find_developer(report, query)
        detail.append(key or query)  # renderers report "no activity" for unknown names

    if args.top:
        keep = set(list(report["developers"])[:args.top]) | set(detail)
        report["developers"] = {k: v for k, v in report["developers"].items() if k in keep}
    if args.all_details:
        detail = list(report["developers"])

    if args.format == "text":
        color = False if (args.no_color or args.output) else None
        text = render_text(report, repo, detail, color=color)
    elif args.format == "md":
        text = render_markdown(report, repo, detail)
    elif args.format == "csv":
        text = render_csv(report)
    elif args.format == "json":
        text = render_json(report, repo)
    else:
        text = render_html(report, repo, detail)

    if args.output:
        Path(args.output).write_text(text)
        log(f"wrote {os.path.abspath(args.output)}")
    else:
        sys.stdout.write(text if text.endswith("\n") else text + "\n")
    return 0
