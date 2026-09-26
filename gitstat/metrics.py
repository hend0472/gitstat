"""Turn raw PR + commit data into per-developer metrics, weekly series and team baselines."""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta

from .common import (
    fmt_hours, fmt_num, fmt_pct, hours_between, is_bot, median, parse_ts, percentile, ratio,
)
from .gitdata import IdentityMap
from .periods import Periods, weekly


@dataclass(frozen=True)
class Metric:
    key: str
    label: str
    section: str
    kind: str            # count | hours | pct | num
    better: str | None   # "higher" | "lower" | None (context-dependent)
    help: str


METRICS: list[Metric] = [
    # --- Pull requests authored
    Metric("prs_opened", "PRs opened", "Authoring", "count", "higher", "Pull requests created in the window."),
    Metric("prs_merged", "PRs merged", "Authoring", "count", "higher", "Their PRs merged in the window."),
    Metric("prs_closed_unmerged", "PRs closed unmerged", "Authoring", "count", None, "Their PRs closed without merging in the window."),
    Metric("prs_open_now", "PRs still open", "Authoring", "count", None, "PRs opened in the window that are still open."),
    Metric("merge_rate", "Merge rate", "Authoring", "pct", "higher", "Merged / (merged + closed unmerged) for PRs finished in the window."),
    Metric("prs_per_week", "PRs per week", "Authoring", "num", "higher", "PRs opened per week of the window."),
    Metric("pr_open_gap_median", "Time between PRs", "Authoring", "hours", None, "Median gap between consecutive PR openings."),
    Metric("pr_size_median", "PR size (lines)", "Authoring", "num", "lower", "Median additions + deletions per PR opened."),
    Metric("pr_files_median", "PR size (files)", "Authoring", "num", "lower", "Median changed files per PR opened."),
    Metric("pr_commits_median", "Commits per PR", "Authoring", "num", None, "Median commits per PR opened."),
    Metric("time_to_first_review_median", "Wait for first feedback", "Authoring", "hours", "lower", "Median time from ready-for-review to the first review or comment by someone else."),
    Metric("time_to_merge_median", "Time to merge", "Authoring", "hours", "lower", "Median time from PR creation to merge."),
    Metric("time_to_merge_p90", "Time to merge (p90)", "Authoring", "hours", "lower", "90th-percentile time from creation to merge."),
    Metric("changes_requested_received", "Changes requested (received)", "Authoring", "count", None, "'Request changes' reviews on their PRs."),
    Metric("changes_requested_rate", "PRs needing changes", "Authoring", "pct", "lower", "Share of their PRs that got at least one 'request changes'."),
    Metric("comments_received_per_pr", "Comments received / PR", "Authoring", "num", None, "Review + conversation comments from others per PR."),
    Metric("feedback_to_commit_median", "Feedback → next commit", "Authoring", "hours", "lower", "Median time from reviewer feedback on their PR to their next commit on it."),
    Metric("feedback_to_reply_median", "Feedback → reply", "Authoring", "hours", "lower", "Median time from reviewer feedback to their next comment on the PR."),
    Metric("merged_without_approval", "Merged w/o approval", "Authoring", "count", "lower", "Their PRs merged with no approving review from someone else."),
    Metric("self_merged", "Self-merged PRs", "Authoring", "count", None, "Their PRs they merged themselves."),
    Metric("base_merges", "Base branch merges", "Authoring", "count", None, "Times they merged the target branch (e.g. develop or main) into a PR branch to bring it up to date, including GitHub's 'Update branch' button."),
    Metric("force_pushes", "Force pushes", "Authoring", "count", None, "Force pushes to their PR branches, usually a rebase onto the latest target branch."),
    Metric("prs_kept_current", "PRs kept up to date", "Authoring", "count", "higher", "Distinct PRs of theirs they updated from the target branch (merge or force push)."),
    # --- Reviewing
    Metric("reviews_given", "Reviews given", "Reviewing", "count", "higher", "Reviews submitted on others' PRs."),
    Metric("approvals_given", "Approvals", "Reviewing", "count", "higher", "Approving reviews on others' PRs."),
    Metric("changes_requested_given", "Changes requested", "Reviewing", "count", None, "'Request changes' reviews on others' PRs."),
    Metric("review_comments_given", "Inline review comments", "Reviewing", "count", "higher", "Line comments written on others' PRs."),
    Metric("pr_comments_given", "PR conversation comments", "Reviewing", "count", "higher", "Conversation comments on others' PRs."),
    Metric("prs_reviewed", "PRs reviewed", "Reviewing", "count", "higher", "Distinct PRs of others they reviewed."),
    Metric("authors_reviewed", "Teammates reviewed", "Reviewing", "count", "higher", "Distinct authors whose PRs they reviewed."),
    Metric("merges_for_others", "PRs merged for others", "Reviewing", "count", None, "Other people's PRs they merged."),
    Metric("review_requests", "Review requests received", "Reviewing", "count", None, "Times they were requested as reviewer."),
    Metric("review_response_rate", "Review requests answered", "Reviewing", "pct", "higher", "Share of review requests they followed with a review."),
    Metric("review_turnaround_median", "Review turnaround", "Reviewing", "hours", "lower", "Median time from being requested to submitting a review."),
    Metric("review_turnaround_p90", "Review turnaround (p90)", "Reviewing", "hours", "lower", "90th-percentile review turnaround."),
    # --- Commits
    Metric("commits", "Commits", "Commits", "count", "higher", "Non-merge commits on the analysed branch."),
    Metric("pr_commits", "Commits in PRs", "Commits", "count", None, "Commits pushed to their PRs (useful in squash-merge repos)."),
    Metric("active_days", "Active days", "Commits", "count", "higher", "Distinct days with at least one commit."),
    Metric("commits_per_week", "Commits per week", "Commits", "num", "higher", "Commits per week of the window."),
    Metric("commits_per_active_day", "Commits per active day", "Commits", "num", None, "Average commits on days they committed."),
    Metric("commit_gap_median", "Time between commits", "Commits", "hours", None, "Median gap between consecutive commits."),
    Metric("longest_streak_days", "Longest streak (days)", "Commits", "count", None, "Most consecutive calendar days with commits."),
    Metric("lines_added", "Lines added", "Commits", "count", None, "Lines added across commits."),
    Metric("lines_deleted", "Lines deleted", "Commits", "count", None, "Lines deleted across commits."),
    Metric("commit_size_median", "Commit size (lines)", "Commits", "num", "lower", "Median additions + deletions per commit."),
    Metric("off_hours_pct", "Off-hours commits", "Commits", "pct", None, "Commits on weekends or outside 08:00–19:00 in the author's timezone."),
]
METRIC_BY_KEY = {m.key: m for m in METRICS}

COUNT_KEYS = [m.key for m in METRICS if m.kind == "count"]
ACTIVITY_KEYS = ["prs_opened", "prs_merged", "reviews_given", "review_comments_given", "merges_for_others",
                 "pr_comments_given", "commits", "pr_commits"]


def _login(actor: dict | None) -> tuple[str | None, str | None]:
    if not actor:
        return None, None
    return actor.get("login"), actor.get("__typename")


class _Dev:
    def __init__(self):
        self.c = Counter()
        self.lists: dict[str, list] = defaultdict(list)
        self.sets: dict[str, set] = defaultdict(set)
        self.weekly: dict[str, Counter] = defaultdict(Counter)
        self.heatmap = [[0] * 24 for _ in range(7)]
        self.prs: list[dict] = []
        self.interactions: Counter = Counter()
        self.review_prs: dict[str, set] = defaultdict(set)  # author -> their PRs this dev reviewed/commented on


# "Merge branch 'develop' into feature/x", "Merge remote-tracking branch 'origin/main' into ...",
# "Merge branch 'main' of github.com:org/repo into ..." (also what GitHub's "Update branch" writes).
_MERGE_BRANCH = re.compile(r"^Merge (?:remote-tracking )?branch '([^']+)'", re.I)
_TRUNKS = {"main", "master", "develop", "development", "dev", "trunk"}


def is_base_sync(headline: str | None, parents: int, base: str | None) -> bool:
    """A merge commit that brings the PR's target branch (or a trunk branch) into the PR branch."""
    if parents < 2:
        return False
    m = _MERGE_BRANCH.match(headline or "")
    if not m:
        return False
    source = m.group(1).split("/")[-1].lower() if m.group(1).startswith(("origin/", "upstream/")) else m.group(1).lower()
    return source == (base or "").lower() or source in _TRUNKS


def compute(prs: list[dict], commits: list[dict], since: datetime, until: datetime,
            identity: IdentityMap, include_bots: bool = False,
            exclude: set[str] | None = None, periods: Periods | None = None) -> dict:
    exclude = {e.lower() for e in (exclude or set())}
    devs: dict[str, _Dev] = defaultdict(_Dev)
    periods = periods or weekly(since, until)
    n_weeks = max((until - since).total_seconds() / (7 * 86400), 1 / 7)

    def keep(login: str | None, typename: str | None = None) -> bool:
        if not login or login.lower() in exclude:
            return False
        return include_bots or not is_bot(login, typename)

    def inside(t: datetime | None) -> bool:
        return t is not None and since <= t <= until

    def wk(t: datetime) -> int | None:
        return periods.index(t)  # None only for times outside the window, which are never recorded

    # Learn email -> login from PR commits first so local git commits resolve well.
    for pr in prs:
        for node in (pr.get("commits") or {}).get("nodes", []):
            a = node["commit"].get("author") or {}
            identity.learn(a.get("name"), a.get("email"), (a.get("user") or {}).get("login"))
    for cm in commits:
        identity.learn(cm.get("name"), cm.get("email"), cm.get("login"))

    repo_ttm, repo_ttfr = [], []

    # ------------------------------------------------------------ pull requests
    for pr in prs:
        author, atype = _login(pr.get("author"))
        created, merged_at, closed_at = parse_ts(pr["createdAt"]), parse_ts(pr.get("mergedAt")), parse_ts(pr.get("closedAt"))

        reviews = []
        for r in (pr.get("reviews") or {}).get("nodes", []):
            who, typ = _login(r.get("author"))
            t = parse_ts(r.get("submittedAt"))
            if t and r["state"] != "PENDING" and keep(who, typ):
                reviews.append((t, who, r["state"]))
        conv = [(parse_ts(c["createdAt"]), *_login(c.get("author"))) for c in (pr.get("comments") or {}).get("nodes", [])]
        conv = [(t, who) for t, who, typ in conv if keep(who, typ)]
        inline = []
        for th in (pr.get("reviewThreads") or {}).get("nodes", []):
            for c in (th.get("comments") or {}).get("nodes", []):
                who, typ = _login(c.get("author"))
                if keep(who, typ):
                    inline.append((parse_ts(c["createdAt"]), who))
        pr_commits = []
        for node in (pr.get("commits") or {}).get("nodes", []):
            cm = node["commit"]
            a = cm.get("author") or {}
            who = identity.resolve(a.get("name"), a.get("email"), (a.get("user") or {}).get("login"))
            parents = (cm.get("parents") or {}).get("totalCount", 1)
            sync = is_base_sync(cm.get("messageHeadline"), parents, pr.get("baseRefName"))
            pr_commits.append((parse_ts(cm["committedDate"]), who, parents > 1, sync))
        requests, ready, force_pushes = [], [], []
        for ev in (pr.get("timelineItems") or {}).get("nodes", []):
            if ev["__typename"] == "ReviewRequestedEvent":
                who, _ = _login(ev.get("requestedReviewer"))
                if keep(who):
                    requests.append((parse_ts(ev["createdAt"]), who))
            elif ev["__typename"] == "ReadyForReviewEvent":
                ready.append(parse_ts(ev["createdAt"]))
            elif ev["__typename"] == "HeadRefForcePushedEvent":
                who, typ = _login(ev.get("actor"))
                if keep(who, typ):
                    force_pushes.append((parse_ts(ev["createdAt"]), who))
        # PRs open at some point in the window: the denominator for review coverage.
        open_in_window = created <= until and (closed_at is None or closed_at >= since)

        # --- reviewer-side activity (on PRs they didn't author)
        for t, who, state in reviews:
            if who == author or not inside(t):
                continue
            d = devs[who]
            d.c["reviews_given"] += 1
            d.weekly[wk(t)]["reviews"] += 1
            if state == "APPROVED":
                d.c["approvals_given"] += 1
            elif state == "CHANGES_REQUESTED":
                d.c["changes_requested_given"] += 1
            d.sets["prs_reviewed"].add(pr["number"])
            if author:
                d.sets["authors_reviewed"].add(author)
                d.interactions[author] += 1
                if open_in_window:
                    d.review_prs[author].add(pr["number"])
        for t, who in inline:
            if who != author and inside(t):
                devs[who].c["review_comments_given"] += 1
                if author:
                    devs[who].interactions[author] += 1
                    if open_in_window:
                        devs[who].review_prs[author].add(pr["number"])
                devs[who].sets["prs_reviewed"].add(pr["number"])
                if author:
                    devs[who].sets["authors_reviewed"].add(author)
        for t, who in conv:
            if who != author and inside(t):
                devs[who].c["pr_comments_given"] += 1
                if author:
                    devs[who].interactions[author] += 1
                    if open_in_window:
                        devs[who].review_prs[author].add(pr["number"])

        merger = (pr.get("mergedBy") or {}).get("login")
        if merger and merger != author and keep(merger) and inside(merged_at):
            devs[merger].c["merges_for_others"] += 1

        for t_req, who in requests:
            if who == author or not inside(t_req):
                continue
            d = devs[who]
            d.c["review_requests"] += 1
            responses = [t for t, w, _ in reviews if w == who and t >= t_req]
            responses += [t for t, w in inline if w == who and t >= t_req]
            if responses:
                d.c["review_requests_answered"] += 1
                d.lists["review_turnaround"].append(hours_between(t_req, min(responses)))
            elif pr["state"] == "OPEN" and not pr.get("isDraft"):
                d.c["review_requests_pending"] += 1

        # --- keeping PR branches up to date (credited to whoever did it)
        updated_by = set()
        for t, who, _, sync in pr_commits:
            if sync and inside(t) and keep(who):
                devs[who].c["base_merges"] += 1
                updated_by.add(who)
        for t, who in force_pushes:
            if inside(t):
                devs[who].c["force_pushes"] += 1
                updated_by.add(who)
        if author in updated_by:
            devs[author].c["prs_kept_current"] += 1

        if not author or not keep(author, atype):
            continue

        # --- author-side activity
        if open_in_window:
            d_open = devs[author]
            d_open.c["prs_open_in_window"] += 1
        d = devs[author]
        others_reviews = [(t, w, s) for t, w, s in reviews if w != author]
        first_review = min([t for t, _, _ in others_reviews] + [t for t, w in inline + conv if w != author], default=None)

        for t, who, is_merge, _ in pr_commits:
            if who == author and inside(t) and not is_merge:  # merges aren't new work
                d.c["pr_commits"] += 1

        if inside(created):
            d.c["prs_opened"] += 1
            d.weekly[wk(created)]["prs_opened"] += 1
            d.lists["pr_open_times"].append(created)
            d.lists["pr_size"].append(pr["additions"] + pr["deletions"])
            d.lists["pr_files"].append(pr["changedFiles"])
            d.lists["pr_commits_count"].append(pr["commits"]["totalCount"])
            if pr["state"] == "OPEN":
                d.c["prs_open_now"] += 1
            if first_review:
                ready_at = max([r for r in ready if r <= first_review], default=created)
                ttfr = hours_between(ready_at, first_review)
                d.lists["ttfr"].append(ttfr)
                repo_ttfr.append(ttfr)
            cr = sum(1 for _, _, s in others_reviews if s == "CHANGES_REQUESTED")
            d.c["prs_with_cr"] += 1 if cr else 0
            received = sum(1 for _, w in inline if w != author) + sum(1 for _, w in conv if w != author)
            d.lists["comments_received"].append(received)
            d.prs.append({
                "number": pr["number"], "title": pr["title"], "url": pr["url"], "state": pr["state"],
                "created": pr["createdAt"], "merged": pr.get("mergedAt"),
                "size": pr["additions"] + pr["deletions"],
                "hours_to_merge": hours_between(created, merged_at) if merged_at else None,
                "hours_to_first_review": d.lists["ttfr"][-1] if first_review else None,
            })

        if inside(merged_at):
            d.c["prs_merged"] += 1
            d.weekly[wk(merged_at)]["prs_merged"] += 1
            ttm = hours_between(created, merged_at)
            d.lists["ttm"].append(ttm)
            repo_ttm.append(ttm)
            if not any(s == "APPROVED" and t <= merged_at for t, _, s in others_reviews):
                d.c["merged_without_approval"] += 1
            if (pr.get("mergedBy") or {}).get("login") == author:
                d.c["self_merged"] += 1
        elif pr["state"] == "CLOSED" and inside(closed_at):
            d.c["prs_closed_unmerged"] += 1

        d.c["changes_requested_received"] += sum(
            1 for t, _, s in others_reviews if s == "CHANGES_REQUESTED" and inside(t))

        # Feedback response: walk the PR timeline; the first unanswered piece of
        # feedback starts a clock that stops at the author's next commit / reply.
        feedback = [t for t, _, s in others_reviews if s in ("CHANGES_REQUESTED", "COMMENTED")]
        feedback += [t for t, w in inline if w != author] + [t for t, w in conv if w != author]
        events = [(t, "fb") for t in feedback]
        events += [(t, "commit") for t, w, is_merge, _ in pr_commits if w == author and not is_merge]
        events += [(t, "reply") for t, w in inline + conv if w == author]
        events.sort(key=lambda e: e[0])
        pending_commit = pending_reply = None
        for t, kind in events:
            if kind == "fb":
                pending_commit = pending_commit or t
                pending_reply = pending_reply or t
            elif kind == "commit" and pending_commit:
                if inside(pending_commit):
                    d.lists["fb_commit"].append(hours_between(pending_commit, t))
                pending_commit = None
            elif kind == "reply" and pending_reply:
                if inside(pending_reply):
                    d.lists["fb_reply"].append(hours_between(pending_reply, t))
                pending_reply = None

    # ------------------------------------------------------------ commits
    for cm in commits:
        who = identity.resolve(cm["name"], cm["email"], cm.get("login"))
        if not keep(who) or is_bot(cm["name"]) and not include_bots:
            continue
        t = parse_ts(cm["authored"])
        if not inside(t):
            continue
        d = devs[who]
        d.c["commits"] += 1
        d.c["lines_added"] += cm["additions"]
        d.c["lines_deleted"] += cm["deletions"]
        d.lists["commit_size"].append(cm["additions"] + cm["deletions"])
        d.lists["commit_times"].append(t)
        d.sets["active_dates"].add(t.date())  # author's local date
        d.weekly[wk(t)]["commits"] += 1
        d.heatmap[t.weekday()][t.hour] += 1
        if t.weekday() >= 5 or t.hour < 8 or t.hour >= 19:
            d.c["off_hours"] += 1

    # ------------------------------------------------------------ finalize
    developers = {}
    for login, d in devs.items():
        m = _finalize(d, n_weeks)
        if not any(m.get(k) for k in ACTIVITY_KEYS):
            continue
        developers[login] = {
            "login": login,
            "metrics": m,
            "series": {s: [d.weekly[i][s] for i in range(len(periods))] for s in ("commits", "prs_opened", "prs_merged", "reviews")},
            "heatmap": d.heatmap,
            "interactions": dict(d.interactions),
            "review_prs": {a: len(v) for a, v in d.review_prs.items()},
            "prs_open_in_window": d.c["prs_open_in_window"],
            "active": {sec: active_in(sec, m) for sec in ("Authoring", "Reviewing", "Commits")},
            "prs": sorted(d.prs, key=lambda p: p["created"], reverse=True),
        }

    team = _team_baseline(developers)
    for dev in developers.values():
        dev["ranks"] = _ranks(dev, developers)
        dev["insights"] = insights(dev, team["median"])

    return {
        "since": since.isoformat(), "until": until.isoformat(),
        "period": {"kind": periods.kind, "days": periods.days},
        "periods": periods.to_json(),
        "metrics": [asdict(m) for m in METRICS],
        "developers": dict(sorted(developers.items(), key=lambda kv: _activity_score(kv[1]), reverse=True)),
        "team": team,
        "repo_summary": {
            "prs_considered": len(prs),
            "prs_opened": sum(v["metrics"]["prs_opened"] for v in developers.values()),
            "prs_merged": sum(v["metrics"]["prs_merged"] for v in developers.values()),
            "reviews": sum(v["metrics"]["reviews_given"] for v in developers.values()),
            "commits": sum(v["metrics"]["commits"] for v in developers.values()),
            "contributors": len(developers),
            "time_to_merge_median": median(repo_ttm),
            "time_to_first_review_median": median(repo_ttfr),
        },
    }


def _gaps(times: list[datetime]) -> list[float]:
    times = sorted(times)
    return [hours_between(a, b) for a, b in zip(times, times[1:])]


def _longest_streak(dates: set) -> int:
    best = run = 0
    prev = None
    for day in sorted(dates):
        run = run + 1 if prev and day - prev == timedelta(days=1) else 1
        best, prev = max(best, run), day
    return best


def _finalize(d: _Dev, n_weeks: float) -> dict:
    c, L = d.c, d.lists
    finished = c["prs_merged"] + c["prs_closed_unmerged"]
    active_days = len(d.sets["active_dates"])
    return {
        "prs_opened": c["prs_opened"],
        "prs_merged": c["prs_merged"],
        "prs_closed_unmerged": c["prs_closed_unmerged"],
        "prs_open_now": c["prs_open_now"],
        "merge_rate": ratio(c["prs_merged"], finished),
        "prs_per_week": c["prs_opened"] / n_weeks,
        "pr_open_gap_median": median(_gaps(L["pr_open_times"])),
        "pr_size_median": median(L["pr_size"]),
        "pr_files_median": median(L["pr_files"]),
        "pr_commits_median": median(L["pr_commits_count"]),
        "time_to_first_review_median": median(L["ttfr"]),
        "time_to_merge_median": median(L["ttm"]),
        "time_to_merge_p90": percentile(L["ttm"], 0.9),
        "changes_requested_received": c["changes_requested_received"],
        "changes_requested_rate": ratio(c["prs_with_cr"], c["prs_opened"]),
        "comments_received_per_pr": ratio(sum(L["comments_received"]), len(L["comments_received"])),
        "feedback_to_commit_median": median(L["fb_commit"]),
        "feedback_to_reply_median": median(L["fb_reply"]),
        "merged_without_approval": c["merged_without_approval"],
        "self_merged": c["self_merged"],
        "base_merges": c["base_merges"],
        "force_pushes": c["force_pushes"],
        "prs_kept_current": c["prs_kept_current"],
        "reviews_given": c["reviews_given"],
        "approvals_given": c["approvals_given"],
        "changes_requested_given": c["changes_requested_given"],
        "review_comments_given": c["review_comments_given"],
        "pr_comments_given": c["pr_comments_given"],
        "prs_reviewed": len(d.sets["prs_reviewed"]),
        "authors_reviewed": len(d.sets["authors_reviewed"]),
        "merges_for_others": c["merges_for_others"],
        "review_requests": c["review_requests"],
        "review_response_rate": ratio(c["review_requests_answered"], c["review_requests"] - c["review_requests_pending"]),
        "review_turnaround_median": median(L["review_turnaround"]),
        "review_turnaround_p90": percentile(L["review_turnaround"], 0.9),
        "commits": c["commits"],
        "pr_commits": c["pr_commits"],
        "active_days": active_days,
        "commits_per_week": c["commits"] / n_weeks,
        "commits_per_active_day": ratio(c["commits"], active_days),
        "commit_gap_median": median(_gaps(L["commit_times"])),
        "longest_streak_days": _longest_streak(d.sets["active_dates"]),
        "lines_added": c["lines_added"],
        "lines_deleted": c["lines_deleted"],
        "commit_size_median": median(L["commit_size"]),
        "off_hours_pct": ratio(c["off_hours"], c["commits"]),
    }


def _activity_score(dev: dict) -> float:
    m = dev["metrics"]
    return m["prs_opened"] * 3 + m["reviews_given"] * 2 + m["commits"] + m["review_comments_given"] * 0.5


def active_in(section: str, m: dict) -> bool:
    """Whether a developer did anything in a section; baselines and ranks only use active peers."""
    if section == "Authoring":
        return bool(m["prs_opened"] or m["prs_merged"])
    if section == "Reviewing":
        return bool(m["reviews_given"] or m["review_comments_given"] or m["review_requests"] or m["merges_for_others"])
    return bool(m["commits"])


def _peers(metric: Metric, developers: dict) -> list:
    return [d["metrics"][metric.key] for d in developers.values()
            if active_in(metric.section, d["metrics"]) and d["metrics"][metric.key] is not None]


def _team_baseline(developers: dict) -> dict:
    med, p75, active = {}, {}, {}
    for metric in METRICS:
        vals = _peers(metric, developers)
        med[metric.key] = median(vals)
        p75[metric.key] = percentile(vals, 0.75)
    for section in ("Authoring", "Reviewing", "Commits"):
        active[section] = sum(1 for d in developers.values() if active_in(section, d["metrics"]))
    return {"median": med, "p75": p75, "size": len(developers), "active": active}


def _ranks(dev: dict, developers: dict) -> dict:
    """Rank 1 = best among peers active in the same section; omitted when not comparable."""
    ranks = {}
    for metric in METRICS:
        v = dev["metrics"][metric.key]
        if metric.better is None or v is None or not active_in(metric.section, dev["metrics"]):
            continue
        peers = _peers(metric, developers)
        if metric.better == "higher":
            ranks[metric.key] = [1 + sum(1 for p in peers if p > v), len(peers)]
        else:
            ranks[metric.key] = [1 + sum(1 for p in peers if p < v), len(peers)]
    return ranks


# ---------------------------------------------------------------- insights

def insights(dev: dict, team: dict) -> list[dict]:
    """Rule-based observations comparing a developer with the team median.

    These are conversation starters, not verdicts — each carries the numbers behind it.
    """
    m, out = dev["metrics"], []

    def over(key, factor, min_team=None):
        v, t = m.get(key), team.get(key)
        return v is not None and t and (min_team is None or t >= min_team) and v > t * factor

    def under(key, factor, min_team=None):
        v, t = m.get(key), team.get(key)
        return v is not None and t and (min_team is None or t >= min_team) and v < t * factor

    def add(kind, text):
        out.append({"kind": kind, "text": text})

    if m["prs_opened"] >= 2 and over("pr_size_median", 2, min_team=20):
        add("improve", f"PRs are large: median {m['pr_size_median']:.0f} lines vs team {team['pr_size_median']:.0f}. "
                       "Smaller PRs usually get reviewed and merged faster.")
    if m["prs_opened"] >= 2 and over("time_to_first_review_median", 2, min_team=1):
        add("watch", f"PRs wait {_h(m['time_to_first_review_median'])} for first feedback vs team {_h(team['time_to_first_review_median'])}. "
                     "Worth checking PR size, description quality, or reviewer assignment.")
    if m["prs_merged"] >= 2 and over("time_to_merge_median", 2, min_team=1):
        add("watch", f"Time to merge is {_h(m['time_to_merge_median'])} vs team {_h(team['time_to_merge_median'])}.")
    if over("feedback_to_commit_median", 2, min_team=1):
        add("improve", f"Takes {_h(m['feedback_to_commit_median'])} to push a change after review feedback vs team {_h(team['feedback_to_commit_median'])}.")
    if m["review_requests"] >= 3 and over("review_turnaround_median", 2, min_team=1):
        add("improve", f"Review turnaround is {_h(m['review_turnaround_median'])} vs team {_h(team['review_turnaround_median'])}; teammates are waiting on these reviews.")
    if m["review_requests"] >= 3 and m["review_response_rate"] is not None and m["review_response_rate"] < 0.6:
        add("improve", f"Answered {m['review_response_rate'] * 100:.0f}% of review requests.")
    if under("reviews_given", 0.4, min_team=3):
        add("improve", f"Gave {m['reviews_given']} reviews vs team median {team['reviews_given']:.0f}. Reviewing more spreads knowledge and unblocks others.")
    if m["merged_without_approval"] >= 2:
        add("watch", f"{m['merged_without_approval']} PRs merged without an approving review.")
    if m["commits"] >= 10 and m["off_hours_pct"] is not None and m["off_hours_pct"] >= 0.35:
        add("watch", f"{m['off_hours_pct'] * 100:.0f}% of commits are on weekends or outside 08:00–19:00; check workload.")
    if m["prs_opened"] >= 3 and m["changes_requested_rate"] is not None and over("changes_requested_rate", 2, min_team=0.05):
        add("watch", f"{m['changes_requested_rate'] * 100:.0f}% of PRs needed changes vs team {team['changes_requested_rate'] * 100:.0f}%.")

    ranks = dev.get("ranks", {})
    for key in ("reviews_given", "review_turnaround_median", "prs_merged", "time_to_merge_median",
                "feedback_to_commit_median", "authors_reviewed", "review_comments_given"):
        r = ranks.get(key)
        if r and r[1] >= 3 and r[0] == 1 and m[key]:
            label = METRIC_BY_KEY[key].label.lower()
            add("strength", f"Best on the team for {label} ({_fmt(key, m[key])}).")
    return out


def _h(v):
    return fmt_hours(v)


def _fmt(key, v):
    kind = METRIC_BY_KEY[key].kind
    return fmt_hours(v) if kind == "hours" else fmt_pct(v) if kind == "pct" else fmt_num(v)
