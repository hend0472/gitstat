"""Time buckets for trend charts: calendar weeks, or sprints anchored on a known start date."""

from __future__ import annotations

import re
from bisect import bisect_right
from datetime import date, datetime, timedelta, timezone

from .common import GitstatError, week_start


class Periods:
    """Consecutive, equal-length buckets covering [since, until]."""

    def __init__(self, kind: str, days: int, first_start: datetime, until: datetime):
        self.kind, self.days = kind, days
        self.starts: list[datetime] = []
        cur = first_start
        while cur <= until:
            self.starts.append(cur)
            cur += timedelta(days=days)
        self.until = until

    def index(self, t: datetime) -> int | None:
        i = bisect_right(self.starts, t) - 1
        if i < 0 or t > self.until:
            return None
        return i

    def __len__(self) -> int:
        return len(self.starts)

    @property
    def since(self) -> datetime:
        return self.starts[0]

    def to_json(self) -> list[dict]:
        out = []
        for s in self.starts:
            end = s + timedelta(days=self.days)
            # Sprints start at local midnight, so label them in local time; weeks are UTC.
            ls = s.astimezone() if self.kind == "sprint" else s
            last_day = (ls + timedelta(days=self.days) - timedelta(seconds=1)).date()
            label = ls.strftime("%m/%d") if self.kind == "week" else f"{ls:%m/%d}–{last_day:%m/%d}"
            out.append({
                "start": s.isoformat(), "end": end.isoformat(),
                "label": label, "short": ls.strftime("%m/%d"),
                "in_progress": end > self.until,
            })
        return out


def weekly(since: datetime, until: datetime) -> Periods:
    return Periods("week", 7, week_start(since), until)


def sprints(anchor: datetime, days: int, since: datetime, until: datetime) -> Periods:
    """Sprints of `days` length, one of which starts at `anchor` (past or future).

    The first bucket is the sprint containing `since`, so the window is aligned to
    sprint boundaries and no sprint is partially counted at the start.
    """
    if days < 1:
        raise GitstatError("sprint length must be at least 1 day")
    k = (since - anchor) // timedelta(days=days)
    return Periods("sprint", days, anchor + k * timedelta(days=days), until)


_LENGTH = re.compile(r"^\s*(\d+)\s*([dw]?)\s*$", re.I)


def parse_length(value) -> int:
    """'14', '14d' or '2w' -> days."""
    if isinstance(value, int):
        return value
    m = _LENGTH.match(str(value))
    if not m:
        raise GitstatError(f"can't read sprint length '{value}'; use e.g. 14d or 2w")
    n = int(m.group(1))
    return n * 7 if m.group(2).lower() == "w" else n


def parse_anchor(value: str) -> datetime:
    """A sprint start date, taken as local midnight so boundaries match the team's calendar."""
    try:
        d = date.fromisoformat(str(value)[:10])
    except ValueError as exc:
        raise GitstatError(f"can't read sprint start '{value}'; use YYYY-MM-DD") from exc
    return datetime(d.year, d.month, d.day).astimezone().astimezone(timezone.utc)
