"""Self-contained HTML dashboard: templates/ are inlined with the report data embedded as JSON."""

from __future__ import annotations

import html
import json
from pathlib import Path

TEMPLATES = Path(__file__).parent / "templates"


def render_html(report: dict, repo: str, detail: list[str] | None) -> str:
    detail = detail or []
    data = {
        **report,
        "repo": repo,
        "initial": detail[0] if detail else None,
        "compare": detail[1] if len(detail) > 1 else None,
    }
    payload = json.dumps(data, default=str).replace("</", "<\\/")
    page = (TEMPLATES / "report.html").read_text()
    # Replace data last so nothing inside the payload is mistaken for a placeholder.
    return (page.replace("__TITLE__", html.escape(f"gitstat · {repo}"))
                .replace("__CSS__", (TEMPLATES / "report.css").read_text())
                .replace("__JS__", (TEMPLATES / "report.js").read_text())
                .replace("__DATA__", payload))
