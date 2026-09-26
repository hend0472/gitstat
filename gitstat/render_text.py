"""Terminal, Markdown, CSV and JSON renderers."""

from __future__ import annotations

import csv
import io
import json
import os
import sys

from .common import fmt_hours, fmt_num, fmt_pct
from .metrics import METRIC_BY_KEY, METRICS

SPARK = "▁▂▃▄▅▆▇█"

# Columns for the team overview table: (metric key, short header)
OVERVIEW = [
    ("prs_opened", "PRs"),
    ("prs_merged", "Merged"),
    ("time_to_merge_median", "TTM"),
    ("time_to_first_review_median", "1st fb"),
    ("reviews_given", "Reviews"),
    ("approvals_given", "Approve"),
    ("changes_requested_given", "ReqChg"),
    ("review_comments_given", "Comments"),
    ("review_turnaround_median", "Rev TAT"),
    ("commits", "Commits"),
    ("active_days", "Days"),
]


def fmt(key: str, value) -> str:
    kind = METRIC_BY_KEY[key].kind
    if kind == "hours":
        return fmt_hours(value)
    if kind == "pct":
        return fmt_pct(value)
    return fmt_num(value)


def sparkline(values: list[int]) -> str:
    hi = max(values, default=0)
    if not hi:
        return "·" * len(values)
    return "".join("·" if v == 0 else SPARK[min(len(SPARK) - 1, int(v / hi * (len(SPARK) - 1) + 0.5))] for v in values)


class _Style:
    def __init__(self, enabled: bool):
        self.on = enabled

    def _w(self, code, s):
        return f"\x1b[{code}m{s}\x1b[0m" if self.on else s

    def bold(self, s): return self._w("1", s)
    def dim(self, s): return self._w("2", s)
    def green(self, s): return self._w("32", s)
    def yellow(self, s): return self._w("33", s)
    def red(self, s): return self._w("31", s)
    def cyan(self, s): return self._w("36", s)


def _table(headers: list[str], rows: list[list[str]], align_left: int = 1) -> list[str]:
    widths = [max(len(h), *(len(r[i]) for r in rows)) if rows else len(h) for i, h in enumerate(headers)]

    def line(cells):
        return "  ".join(c.ljust(w) if i < align_left else c.rjust(w) for i, (c, w) in enumerate(zip(cells, widths)))

    return [line(headers), line(["─" * w for w in widths])] + [line(r) for r in rows]


def _compare(key: str, value, team) -> str:
    """Arrow showing whether a value is better/worse than the team median."""
    m = METRIC_BY_KEY[key]
    if value is None or team is None or m.better is None or team == 0:
        return ""
    r = value / team
    if 0.8 <= r <= 1.25:
        return "≈"
    good = (r > 1) == (m.better == "higher")
    return ("▲" if r > 1 else "▼") + ("+" if good else "-")


def render_text(report: dict, repo: str, detail: list[str] | None, color: bool | None = None) -> str:
    if color is None:
        color = sys.stdout.isatty() and not os.environ.get("NO_COLOR")
    st = _Style(color)
    out = []
    s = report["repo_summary"]
    out.append(st.bold(f"gitstat · {repo}") + st.dim(f"   {report['since'][:10]} → {report['until'][:10]}  ({len(report['weeks'])} weeks)"))
    out.append(
        f"{s['contributors']} contributors · {s['prs_opened']} PRs opened · {s['prs_merged']} merged · "
        f"{s['reviews']} reviews · {s['commits']} commits · median time to merge {fmt_hours(s['time_to_merge_median'])} · "
        f"median wait for first feedback {fmt_hours(s['time_to_first_review_median'])}"
    )
    out.append("")

    devs = report["developers"]
    headers = ["Developer"] + [h for _, h in OVERVIEW] + ["Weekly activity"]
    rows = []
    for login, d in devs.items():
        weekly = [a + b + c for a, b, c in zip(d["weekly"]["commits"], d["weekly"]["prs_opened"], d["weekly"]["reviews"])]
        rows.append([login] + [fmt(k, d["metrics"][k]) for k, _ in OVERVIEW] + [sparkline(weekly)])
    team = report["team"]["median"]
    rows.append([st.dim("team median")] + [fmt(k, team[k]) for k, _ in OVERVIEW] + [""])
    out.append(st.bold("Team overview"))
    out.extend(_table(headers, rows))
    out.append(st.dim("TTM = median time to merge · 1st fb = median wait for first review/comment · Rev TAT = median review turnaround after being requested"))

    for login in detail or []:
        if login not in devs:
            out.append("")
            out.append(st.yellow(f"No activity found for '{login}' in this window."))
            continue
        out.append("")
        out.extend(_render_dev(devs[login], report, st))
    return "\n".join(out)


def _render_dev(d: dict, report: dict, st: _Style) -> list[str]:
    team = report["team"]["median"]
    out = [st.bold(st.cyan(f"━━ {_who(d)} ━━"))]
    w = d["weekly"]
    for name, key in (("Commits", "commits"), ("PRs opened", "prs_opened"), ("Reviews", "reviews")):
        out.append(f"  {name:<11} {sparkline(w[key])}  {st.dim('total ' + str(sum(w[key])))}")
    out.append("")
    for section in ("Authoring", "Reviewing", "Commits"):
        rows = []
        for m in (m for m in METRICS if m.section == section):
            v = d["metrics"][m.key]
            rank = d["ranks"].get(m.key)
            arrow = _compare(m.key, v, team[m.key])
            arrow = st.green(arrow) if arrow.endswith("+") else st.red(arrow) if arrow.endswith("-") else st.dim(arrow)
            rows.append([m.label, fmt(m.key, v), fmt(m.key, team[m.key]), arrow, f"{rank[0]}/{rank[1]}" if rank else ""])
        out.append(st.bold(f"  {section}"))
        out.extend("  " + line for line in _table(["Metric", "Value", "Team median", "", "Rank"], rows))
        out.append("")
    if d["insights"]:
        out.append(st.bold("  Observations"))
        icon = {"strength": st.green("✓"), "improve": st.yellow("→"), "watch": st.red("!")}
        for ins in d["insights"]:
            out.append(f"  {icon[ins['kind']]} {ins['text']}")
        out.append("")
    if d["prs"]:
        out.append(st.bold("  Recent PRs"))
        rows = [[f"#{p['number']}", p["title"][:60], p["state"].lower(), fmt_num(p["size"]),
                 fmt_hours(p["hours_to_first_review"]), fmt_hours(p["hours_to_merge"])] for p in d["prs"][:10]]
        out.extend("  " + line for line in _table(["PR", "Title", "State", "Lines", "1st feedback", "Merge"], rows, align_left=3))
    return out


def _who(d: dict) -> str:
    """'Jane Doe (@a123456)' when a display name is in use, otherwise just the login."""
    gh = d.get("github_login")
    return f"{d['login']} (@{gh})" if gh and gh != d["login"] else d["login"]


def render_markdown(report: dict, repo: str, detail: list[str] | None) -> str:
    s = report["repo_summary"]
    team = report["team"]["median"]
    out = [f"# Contribution report: {repo}", "",
           f"_{report['since'][:10]} → {report['until'][:10]} · {len(report['weeks'])} weeks_", "",
           f"- **Contributors:** {s['contributors']}",
           f"- **PRs opened / merged:** {s['prs_opened']} / {s['prs_merged']}",
           f"- **Reviews:** {s['reviews']}",
           f"- **Commits:** {s['commits']}",
           f"- **Median time to merge:** {fmt_hours(s['time_to_merge_median'])}",
           f"- **Median wait for first feedback:** {fmt_hours(s['time_to_first_review_median'])}", "",
           "## Team overview", ""]
    heads = ["Developer"] + [METRIC_BY_KEY[k].label for k, _ in OVERVIEW]
    out.append("| " + " | ".join(heads) + " |")
    out.append("|" + "|".join(["---"] + ["---:"] * len(OVERVIEW)) + "|")
    for login, d in report["developers"].items():
        out.append("| " + " | ".join([login] + [fmt(k, d["metrics"][k]) for k, _ in OVERVIEW]) + " |")
    out.append("| _team median_ | " + " | ".join(fmt(k, team[k]) for k, _ in OVERVIEW) + " |")

    for login in detail or []:
        d = report["developers"].get(login)
        if not d:
            continue
        out += ["", f"## {_who(d)}", ""]
        for section in ("Authoring", "Reviewing", "Commits"):
            out += [f"### {section}", "", "| Metric | Value | Team median | Rank |", "|---|---:|---:|---:|"]
            for m in (m for m in METRICS if m.section == section):
                rank = d["ranks"].get(m.key)
                out.append(f"| {m.label} | {fmt(m.key, d['metrics'][m.key])} | {fmt(m.key, team[m.key])} | "
                           f"{f'{rank[0]}/{rank[1]}' if rank else ''} |")
            out.append("")
        if d["insights"]:
            out += ["### Observations", ""]
            out += [f"- **{i['kind']}:** {i['text']}" for i in d["insights"]]
    return "\n".join(out) + "\n"


def render_csv(report: dict) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["name", "github_login"] + [m.key for m in METRICS])
    for login, d in report["developers"].items():
        writer.writerow([login, d.get("github_login", login)] + ["" if d["metrics"][m.key] is None else round(d["metrics"][m.key], 3)
                                   for m in METRICS])
    return buf.getvalue()


def render_json(report: dict, repo: str) -> str:
    return json.dumps({"repo": repo, **report}, indent=2, default=str)
