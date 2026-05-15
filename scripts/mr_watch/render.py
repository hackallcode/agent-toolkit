"""How a comment reaches the event stream.

A reviewer's line breaks carry meaning, and the lines a comment covers are the
context needed to act on it without opening GitLab. Those lines come from the
commit the comment is on, and from the side of the diff it was drawn on: a
selection can start among deleted lines and end among added ones, and the two
sides number their files independently.
"""

from __future__ import annotations

from mr_watch.glab import diff_between
from mr_watch.hunks import window


def clean_body(text: str, limit: int = 1500) -> str:
    """Reviewer comment body with its line breaks preserved - they carry meaning
    (lists, ```suggestion``` blocks, multi-line notes), and collapsing them to
    one line hides the reviewer's formatting. Trailing per-line whitespace is
    trimmed and the total length capped."""

    body = "\n".join(line.rstrip() for line in (text or "").splitlines()).strip("\n")
    return body if len(body) <= limit else body[: limit - 1] + "…"


def endpoints(position: dict | None) -> tuple[str, tuple[str, int] | None, tuple[str, int] | None]:
    """The path a comment is on and the two ends of its selection as
    `(side, number)`.

    GitLab anchors a comment at its end (`new_line`/`old_line`) and carries the
    start in `line_range.start`; either end can sit on the old side of the diff (a
    removed line) or the new one, and a single-line comment has one end twice.
    """

    if not isinstance(position, dict):
        return "", None, None
    path = position.get("new_path") or position.get("old_path") or ""

    def end_of(anchor: dict | None) -> tuple[str, int] | None:
        if not isinstance(anchor, dict):
            return None
        for side, key in (("new", "new_line"), ("old", "old_line")):
            if isinstance(anchor.get(key), int):
                return side, anchor[key]
        return None

    line_range = position.get("line_range")
    if isinstance(line_range, dict):
        return (
            path,
            end_of(line_range.get("start")),
            end_of(line_range.get("end")) or end_of(position),
        )
    anchor = end_of(position)
    return path, anchor, anchor


def where(position: dict | None) -> str:
    """The place a comment names, as the event announces it: `path:+64`, or
    `path:-53..+66` when the selection runs from a removed line to an added one."""

    path, start, end = endpoints(position)
    if not path or (start is None and end is None):
        return ""
    marks = []
    for side, number in [e for e in (start, end) if e is not None]:
        marks.append(f"{'+' if side == 'new' else '-'}{number}")
    if len(marks) == 2 and marks[0] == marks[1]:
        marks = marks[:1]
    return f" {path}:{'..'.join(marks)}"


def diff_context(position: dict | None, project: str = "", span: int = 3) -> str:
    """The piece of diff a comment was written on: everything its selection
    covers, marked with →, plus a few lines around it, in the diff's own `+`/`-`
    shape and with the numbers of both sides, the way GitLab shows them: a removed
    line carries the old number, an added one the new, an untouched one both.

    The numbers come from the comment and so does the diff they were taken on:
    the note names its two commits (`base_sha` → `head_sha`). The MR's diff today
    is a different diff - the same numbers there point at other lines - and a file
    read whole says nothing about which line was added and which removed.
    """

    path, start, end = endpoints(position)
    if not path or not project:
        return ""
    refs = position or {}
    diff = diff_between(project, refs.get("base_sha", ""), refs.get("head_sha", ""), path)
    out = []
    for hit, line in window(diff, start, end, span):
        old, new = str(line["old"] or ""), str(line["new"] or "")
        out.append(f"    {'→' if hit else ' '} {old:>5} {new:>5} {line['text']}")
    return "\n" + "\n".join(out) if out else ""


def render_note(note: dict, prefix: str, project: str = "") -> str:
    author = (note.get("author") or {}).get("username", "?")
    ctx = diff_context(note.get("position"), project)
    body = clean_body(note.get("body", ""))
    # A multi-line body gets its own indented block so the reviewer's line breaks survive.
    if "\n" in body:
        body = "\n" + "\n".join("    " + line for line in body.split("\n"))
        return f"{prefix} [{author}]:{body}{ctx}"
    return f"{prefix} [{author}]: {body}{ctx}"
