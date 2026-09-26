"""Shared helpers: time parsing, statistics, formatting, subprocess wrapper."""

from __future__ import annotations

import json
import re
import statistics
import subprocess
import sys
from datetime import datetime, timedelta, timezone


class GitstatError(RuntimeError):
    pass


def run(cmd: list[str], cwd: str | None = None, input_text: str | None = None) -> str:
    try:
        proc = subprocess.run(
            cmd, cwd=cwd, input=input_text, capture_output=True, text=True, check=False
        )
    except FileNotFoundError as exc:
        raise GitstatError(f"command not found: {cmd[0]}") from exc
    if proc.returncode != 0:
        raise GitstatError(f"{' '.join(cmd[:3])} failed: {proc.stderr.strip() or proc.stdout.strip()}")
    return proc.stdout


def run_json(cmd: list[str], cwd: str | None = None):
    return json.loads(run(cmd, cwd=cwd))


def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


# ---------------------------------------------------------------- time

def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


_REL = re.compile(r"^(\d+)\s*([dwmy])$")


def parse_when(value: str, ref: datetime | None = None) -> datetime:
    """Accept '90d', '12w', '6m', '1y', or an ISO date/datetime."""
    ref = ref or now_utc()
    m = _REL.match(value.strip().lower())
    if m:
        n, unit = int(m.group(1)), m.group(2)
        days = {"d": 1, "w": 7, "m": 30, "y": 365}[unit] * n
        return ref - timedelta(days=days)
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def hours_between(a: datetime, b: datetime) -> float:
    return (b - a).total_seconds() / 3600.0


def week_start(dt: datetime) -> datetime:
    d = dt.astimezone(timezone.utc)
    d = d.replace(hour=0, minute=0, second=0, microsecond=0)
    return d - timedelta(days=d.weekday())


# ---------------------------------------------------------------- stats

def median(xs):
    xs = [x for x in xs if x is not None]
    return statistics.median(xs) if xs else None


def mean(xs):
    xs = [x for x in xs if x is not None]
    return statistics.fmean(xs) if xs else None


def percentile(xs, p: float):
    xs = sorted(x for x in xs if x is not None)
    if not xs:
        return None
    k = (len(xs) - 1) * p
    lo, hi = int(k), min(int(k) + 1, len(xs) - 1)
    return xs[lo] + (xs[hi] - xs[lo]) * (k - lo)


def ratio(num, den):
    return (num / den) if den else None


# ---------------------------------------------------------------- formatting

def fmt_hours(h) -> str:
    if h is None:
        return "–"
    if h < 1 / 60:
        return "<1m"
    if h < 1:
        return f"{h * 60:.0f}m"
    if h < 48:
        return f"{h:.1f}h"
    return f"{h / 24:.1f}d"


def fmt_num(v, digits: int = 1) -> str:
    if v is None:
        return "–"
    if isinstance(v, int) or float(v).is_integer():
        return f"{int(v):,}"
    return f"{v:,.{digits}f}"


def fmt_pct(v) -> str:
    return "–" if v is None else f"{v * 100:.0f}%"


def is_bot(login: str | None, typename: str | None = None) -> bool:
    if not login:
        return False
    return typename == "Bot" or login.endswith("[bot]") or login.endswith("-bot")
