"""What CI said, and whether the branch moved.

The author acts on the outcome of the head's pipeline, the reviewer on new code.
Only the newest pipeline counts: a push abandons the one that was running, and
its cancellation is not news to whoever pushed."""

from __future__ import annotations

import re

_TERMINAL_PIPELINE = frozenset({"success", "failed", "canceled", "skipped"})
_FAILURE_LINE = re.compile(r"\b(FAILED|ERROR|Error:|error:|AssertionError|Traceback)\b")


def newest_pipeline(pipelines: list[dict]) -> dict | None:
    return max(pipelines, key=lambda p: p.get("id") or 0) if pipelines else None


def failure_lines(trace: str, limit: int = 3) -> list[str]:
    """The lines of a job log worth putting in an event: what failed, not how
    the runner got there. Timestamps and the log's own prefixes are cut, so the
    event stays readable."""

    hits = []
    for line in (trace or "").splitlines():
        text = re.sub(r"^\S+Z \S+ ", "", line).strip()
        if text and _FAILURE_LINE.search(text):
            hits.append(text if len(text) <= 200 else text[:199] + "…")
    return hits[-limit:]


class HeadTracker:
    """Says when the branch of the MR got a new commit.

    The reviewer's cue: a moved head is new code to read, whatever CI thinks of
    it. The first observation is the state the watch starts from, not news.
    """

    def __init__(self) -> None:
        self._sha: str | None = None

    def moved(self, sha: str) -> str | None:
        if not sha or sha == self._sha:
            return None
        previous, self._sha = self._sha, sha
        return sha if previous is not None else None


class PipelineTracker:
    """Reports the outcome of the newest pipeline of the MR, once each.

    Only the newest one is looked at: a push abandons the pipeline that was
    running, and its cancellation is not news - the agent pushed on purpose. So
    the watcher follows the head instead of narrating what it left behind.
    """

    def __init__(self) -> None:
        self._reported: set[int] = set()

    def landed(self, pipelines: list[dict]) -> dict | None:
        newest = newest_pipeline(pipelines)
        if newest is None or newest.get("status") not in _TERMINAL_PIPELINE:
            return None
        pipeline_id = newest.get("id")
        if pipeline_id in self._reported:
            return None
        self._reported.add(pipeline_id)
        return newest
