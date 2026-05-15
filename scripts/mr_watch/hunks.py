"""The piece of diff a comment was written on.

A comment names its ends: a line on the old side, a line on the new one, or the
same line twice. What it covers is everything between those ends **in diff
order** - removed lines, added lines and the unchanged ones in between - because
that is the selection a reader drew. The two sides number their own files, so
only the diff can say which line sits between which.
"""

from __future__ import annotations

import re

_HUNK = re.compile(r"^@@ -(?P<old>\d+)(?:,\d+)? \+(?P<new>\d+)(?:,\d+)? @@")


def numbered(diff: str) -> list[dict]:
    """Every line of a unified diff with the numbers it carries: `{"text", "old",
    "new"}`, where a side is None when the line does not exist there."""

    out: list[dict] = []
    old = new = 0
    for line in (diff or "").split("\n"):
        header = _HUNK.match(line)
        if header:
            old, new = int(header.group("old")), int(header.group("new"))
            continue
        if not out and not old and not new:
            continue  # preamble before the first hunk
        if line.startswith("-"):
            out.append({"text": line, "old": old, "new": None})
            old += 1
        elif line.startswith("+"):
            out.append({"text": line, "old": None, "new": new})
            new += 1
        elif line.startswith("\\"):
            out.append({"text": line, "old": None, "new": None})
        else:
            out.append({"text": line, "old": old, "new": new})
            old += 1
            new += 1
    return out


def _index_of(lines: list[dict], end: tuple[str, int] | None) -> int | None:
    if end is None:
        return None
    side, number = end
    for i, line in enumerate(lines):
        if line.get(side) == number:
            return i
    return None


def window(
    diff: str,
    start: tuple[str, int] | None,
    end: tuple[str, int] | None,
    span: int = 3,
) -> list[tuple[bool, dict]]:
    """The diff lines a comment covers plus `span` lines around them, in diff
    order: `(is_marked, line)`, each line keeping the numbers it carries.

    Both ends on one side means the comment is about that side's lines, and only
    those are marked - a removed line between two added ones is not what `+65..+68`
    names. Ends on different sides can only mean the run between them, since that
    is what a reader dragged over.

    An end the diff does not carry is dropped rather than guessed at: a selection
    reaching outside the fetched hunks still shows the end that is there.
    """

    lines = numbered(diff)
    ends = [e for e in (start, end) if e is not None]
    if not ends:
        return []

    if len(ends) == 2 and ends[0][0] == ends[1][0]:
        side = ends[0][0]
        low, high = sorted(number for _side, number in ends)
        hits = [
            i
            for i, line in enumerate(lines)
            if line[side] is not None and low <= line[side] <= high
        ]
    else:
        found = [i for i in (_index_of(lines, start), _index_of(lines, end)) if i is not None]
        hits = list(range(min(found), max(found) + 1)) if found else []

    if not hits:
        return []
    lo, hi = max(0, min(hits) - span), min(len(lines), max(hits) + span + 1)
    return [(i in hits, lines[i]) for i in range(lo, hi)]
