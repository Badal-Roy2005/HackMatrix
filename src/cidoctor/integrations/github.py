"""GitHub integration for CI Doctor.

Formats analysis reports as Markdown PR comments and posts/updates them via
the GitHub REST API. All untrusted text from logs is escaped before it ever
reaches Markdown.
"""

from __future__ import annotations

import html
import json
import os
import re
from typing import TYPE_CHECKING

import httpx

if TYPE_CHECKING:
    from cidoctor.core.analysis import AnalysisReport

COMMENT_MARKER = "<!-- cidoctor:comment -->"
MAX_BODY_CHARS = 60_000


def _escape_markdown(text: str) -> str:
    """Neutralize HTML, Markdown injection, and @mentions in untrusted text."""
    text = html.escape(text, quote=False)
    text = text.replace("@", "@\u200b")  # zero-width space after @ to break mentions
    text = text.replace("`", "\u200b`")  # break code-span injection
    return text


def _escape_code_span(text: str) -> str:
    """Escape text for safe use inside a single-code-span."""
    return _escape_markdown(text).replace("`", "\u200b`")


def format_comment(report: AnalysisReport) -> str:
    """Build a Markdown comment body from an :class:`AnalysisReport`."""
    run = report.run
    failed = [r for r in run.results if r.status in {"failed", "error"}]
    passed = [r for r in run.results if r.status == "passed"]
    skipped = [r for r in run.results if r.status == "skipped"]

    lines: list[str] = []
    lines.append(COMMENT_MARKER)
    lines.append("")
    lines.append(f"## CI Doctor report for run `{_escape_code_span(run.id)}`")
    lines.append("")
    lines.append(f"- **Commit:** `{_escape_code_span(run.commit)}`")
    lines.append(f"- **Branch:** `{_escape_code_span(run.branch)}`")
    lines.append(f"- **Created:** {run.created_at.isoformat()}")
    lines.append("")
    lines.append("### Summary")
    lines.append("")
    lines.append(f"- Total tests: {len(run.results)}")
    lines.append(f"- Passed: {len(passed)}")
    lines.append(f"- Failed: {len(failed)}")
    lines.append(f"- Errors: {len([r for r in run.results if r.status == 'error'])}")
    lines.append(f"- Skipped: {len(skipped)}")
    lines.append("")

    # Failure groups table (max 10).
    if report.failure_groups:
        lines.append("### Failure groups")
        lines.append("")
        lines.append("| Fingerprint | Count | Sample message | Tests |")
        lines.append("|---|---|---|---|")
        for g in report.failure_groups[:10]:
            tests = ", ".join(_escape_code_span(t) for t in g.tests[:5])
            if len(g.tests) > 5:
                tests += f" … (+{len(g.tests) - 5} more)"
            lines.append(
                f"| `{_escape_code_span(g.fingerprint)}` | {g.count} | "
                f"`{_escape_code_span(g.sample_message)[:120]}` | {tests} |"
            )
        lines.append("")

    # Flaky tests.
    if report.flaky_tests:
        lines.append("### Flaky tests")
        lines.append("")
        lines.append("| Test | Runs | Pass rate | Verdict | Confidence |")
        lines.append("|---|---|---|---|---|")
        for v in report.flaky_tests:
            lines.append(
                f"| `{_escape_code_span(v.test)}` | {v.runs} | {v.pass_rate:.2f} | "
                f"{v.verdict} | {v.confidence:.2f} |"
            )
        lines.append("")

    # Diagnosis.
    if report.diagnosis:
        d = report.diagnosis
        lines.append("### Diagnosis")
        lines.append("")
        lines.append(f"- **Root cause:** {_escape_markdown(d.root_cause)}")
        lines.append(f"- **Category:** {d.category}")
        lines.append(f"- **Confidence:** {d.confidence:.2f}")
        if d.suggested_fix:
            lines.append(f"- **Suggested fix:** {_escape_markdown(d.suggested_fix)}")
        if d.sources:
            lines.append(
                "- **Sources:** "
                + ", ".join(_escape_code_span(s) for s in d.sources[:5])
            )
        lines.append("")

    # Collapsed details with full failure messages.
    lines.append("<details>")
    lines.append("<summary>Full failure details</summary>")
    lines.append("")
    if failed:
        for r in failed[:20]:
            lines.append(f"#### `{_escape_code_span(r.name)}`")
            lines.append("")
            lines.append(f"- Class: `{_escape_code_span(r.classname)}`")
            lines.append(f"- Duration: {r.duration_s}s")
            if r.message:
                lines.append(f"- Message: `{_escape_code_span(r.message)[:500]}`")
            if r.trace:
                lines.append("```")
                lines.append(_escape_markdown(r.trace)[:2000])
                lines.append("```")
            lines.append("")
    lines.append("</details>")
    lines.append("")

    body = "\n".join(lines)
    if len(body) > MAX_BODY_CHARS:
        body = body[: MAX_BODY_CHARS - 100] + "\n\n… [truncated] …\n"
    return body


def _detect_pr_number() -> int | None:
    """Detect the PR number from GitHub Actions environment."""
    event_path = os.environ.get("GITHUB_EVENT_PATH")
    if event_path:
        try:
            with open(event_path, "r", encoding="utf-8") as fh:
                event = json.load(fh)
            pr = event.get("pull_request", {}).get("number")
            if pr:
                return int(pr)
        except (OSError, json.JSONDecodeError, ValueError):
            pass

    ref = os.environ.get("GITHUB_REF", "")
    # refs/pull/123/merge or refs/pull/123/head
    match = re.match(r"refs/pull/(\d+)/", ref)
    if match:
        return int(match.group(1))
    return None


def post_or_update_comment(
    repo: str,
    pr_number: int | None,
    body: str,
    token: str,
) -> None:
    """Post or update a CI Doctor comment on a GitHub PR.

    Lists existing comments, finds one with the COMMENT_MARKER and PATCHes it,
    otherwise POSTs a new comment. On a 403 (read-only token on a fork PR)
    logs a warning and exits 0 without crashing.
    """
    import logging

    logger = logging.getLogger("cidoctor")
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }

    if pr_number is None:
        logger.warning("Could not detect PR number; skipping comment post.")
        return

    comments_url = (
        f"https://api.github.com/repos/{repo}/issues/{pr_number}/comments"
    )

    try:
        resp = httpx.get(comments_url, headers=headers, timeout=30.0)
    except httpx.RequestError as exc:
        logger.warning("Failed to list GitHub comments: %s", exc)
        return

    if resp.status_code == 403:
        logger.warning(
            "GitHub token lacks write access (fork PR?); skipping comment post."
        )
        return
    if resp.status_code not in (200, 201):
        logger.warning(
            "Failed to list GitHub comments (status %s); skipping.",
            resp.status_code,
        )
        return

    existing_id: int | None = None
    for comment in resp.json():
        if COMMENT_MARKER in comment.get("body", ""):
            existing_id = comment.get("id")
            break

    if existing_id is not None:
        url = f"https://api.github.com/repos/{repo}/issues/comments/{existing_id}"
        method = "PATCH"
    else:
        url = comments_url
        method = "POST"

    try:
        resp = httpx.request(
            method, url, headers=headers, json={"body": body}, timeout=30.0
        )
    except httpx.RequestError as exc:
        logger.warning("Failed to post GitHub comment: %s", exc)
        return

    if resp.status_code == 403:
        logger.warning(
            "GitHub token lacks write access (fork PR?); comment not posted."
        )
        return
    if resp.status_code not in (200, 201):
        logger.warning(
            "Failed to %s GitHub comment (status %s).", method, resp.status_code
        )
        return


__all__ = ["_detect_pr_number", "format_comment", "post_or_update_comment"]
