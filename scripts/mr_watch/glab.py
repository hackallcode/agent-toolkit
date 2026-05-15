"""GitLab access for the watcher scripts.

Auth and transport go through the `glab` CLI, so no token handling lives here.
Imported co-located, because the scripts around it carry dashes in their names
and cannot be imported as modules.

HTTP 429 and exhausted transport reads end the watch. Other unreadable answers
say so on stderr without passing for an empty page."""

from __future__ import annotations

import json
import re
import subprocess
import sys
from collections.abc import Callable


def _complain(path: str, reason: str, body: str = "") -> None:
    """Say what could not be read. stderr, because stdout is the event stream."""

    # Flattened first: glab pads its lines to width, and padding is not a tail.
    flat = " ".join(body.split())
    tail = f" body-tail={flat[-60:]!r}" if flat else ""
    print(f"mr-watch: unreadable answer for {path}: {reason}{tail}", file=sys.stderr)


# A long first wait, because a job log or a wide `compare` needs it; a short second.
_DEADLINES = (60, 20)
# Asking again cannot help: a 4xx is ours to fix, and a 429 only gets a worse answer.
_ANSWERED_FOR_GOOD = re.compile(r"HTTP 4\d\d\b")
_HTTP_STATUS = re.compile(r"HTTP (\d{3})\b")


class WatchReadError(RuntimeError):
    """A read failure that must stop all requests in this watch."""


class RateLimitError(WatchReadError):
    """GitLab refused further requests with HTTP 429."""


class TransportError(WatchReadError):
    """The bounded transport attempts did not produce an answer."""


def _says_not_now(text: str) -> bool:
    """Whether a failure is worth asking about again.

    Everything a failure can be called is asked again unless `glab` named a 4xx.
    The statuses are a closed set, printed by `glab` itself; what the transport
    calls its own failures is an open list that changes without us.

    `glab` wraps transport errors by width even into a pipe, splitting the words
    across lines, so the text is flattened before it is read.
    """

    return not _ANSWERED_FOR_GOOD.search(" ".join(text.split()))


def _read(
    path: str, unusable: Callable[[subprocess.CompletedProcess[str]], str]
) -> subprocess.CompletedProcess[str] | None:
    """Ask `glab` for a path, once more when the answer means «not now».

    `unusable` says why an answer cannot be used, or "" when it can. That verdict
    belongs here rather than at the caller: a body that arrived cut in half is as
    much a «not now» as a connection that died mid-read.
    """

    reasons: list[str] = []
    body = ""
    transport_failed = False
    for deadline in _DEADLINES:
        try:
            out = subprocess.run(
                ["glab", "api", path],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=deadline,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            reasons.append(type(exc).__name__)
            transport_failed = True
            continue
        status = _HTTP_STATUS.search(" ".join(out.stderr.split())) if out.returncode else None
        if status is not None and status.group(1) == "429":
            raise RateLimitError("HTTP 429 rate limit; stopping the watch")
        transport_failed |= bool(out.returncode and status is None)
        why = unusable(out)
        if not why:
            return out
        reasons.append(why)
        body = out.stderr if out.returncode else out.stdout
        # A 404 answers the same way however often it is asked; a cut body does not.
        if out.returncode and not _says_not_now(out.stderr):
            break
    if transport_failed:
        raise TransportError(
            f"network/timeout read failed after {len(reasons)} attempts; stopping the watch"
        )
    _complain(path, ", then ".join(reasons), body)


def _failed(out: subprocess.CompletedProcess[str]) -> str:
    """Why a call failed, in its own words - the exit code is always 1."""

    if not out.returncode:
        return ""
    said = " ".join(out.stderr.split())
    # Go names the request first and the cause after `": "`, and the request is
    # already in the complaint.
    return said.rsplit('": ', 1)[-1][-120:] if said else f"exit={out.returncode}"


def api(path: str) -> object:
    """Parse JSON; fatal read failures raise, other unreadable answers return None."""

    answer: object = None

    def unusable(out: subprocess.CompletedProcess[str]) -> str:
        nonlocal answer
        if failed := _failed(out):
            return failed
        try:
            answer = json.loads(out.stdout)
        except json.JSONDecodeError as exc:
            # A body that ends mid-character lands here: half an answer, not a wrong one.
            return f"unparsable json ({exc})"
        return ""

    return answer if _read(path, unusable) is not None else None


def raw(path: str) -> str:
    """An endpoint answering plain text - a job log, a file at a commit - which
    JSON parsing would only choke on."""

    out = _read(path, _failed)
    return "" if out is None else out.stdout


def paginated(path_no_page: str) -> list:
    """Fetch every page of a list endpoint (per_page=100). `path_no_page` must
    already carry its own `?`-prefixed query string or none."""

    sep = "&" if "?" in path_no_page else "?"
    items: list = []
    page = 1
    while True:
        target = f"{path_no_page}{sep}per_page=100&page={page}"
        chunk = api(target)
        if chunk is None:
            # Not «no more pages» - «this page did not arrive».
            _complain(target, f"page {page} lost, {len(items)} items so far")
            break
        if not isinstance(chunk, list) or not chunk:
            break
        items.extend(chunk)
        page += 1
        if page > 100:  # hard backstop against a runaway loop
            break
    return items


def default_project() -> str | None:
    try:
        url = subprocess.run(
            ["git", "config", "--get", "remote.origin.url"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
        ).stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return None
    # git@gitlab.com:group/repo.git  or  https://gitlab.com/group/sub/repo(.git)
    url = url.removesuffix(".git")
    scp = re.match(r"[^/@]+@[^/:]+:(.+)$", url)  # scp-like ssh
    if scp:
        return scp.group(1)
    https = re.match(r"[a-z]+://[^/]+/(.+)$", url)  # strip scheme + host
    if https:
        return https.group(1)
    return url or None


def diff_between(project: str, base: str, head: str, path: str) -> str:
    """The diff of one path between two commits.

    A comment's numbers were taken on a particular version of the diff, which the
    note names itself (`base_sha` → `head_sha`). The MR's diff today has moved on:
    the same numbers there point at other lines entirely.
    """

    if not (project and base and head and path):
        return ""
    answer = api(f"projects/{project}/repository/compare?from={base}&to={head}")
    for change in (answer.get("diffs") or []) if isinstance(answer, dict) else []:
        if path in (change.get("new_path"), change.get("old_path")):
            return change.get("diff") or ""
    return ""
