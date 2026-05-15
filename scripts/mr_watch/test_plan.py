"""The checklist of the test-plan comment (`modes/build.md`).

The list is edited in place rather than answered, so what there is to report is
the difference between two versions of one note: a box ticked or cleared, the
list gone complete, and a tick whose commit the branch has moved past."""

from __future__ import annotations

import re

_CHECKLIST_ITEM = re.compile(r"^\s*[-*]\s*\[(?P<mark>[ xX])\]\s*(?P<text>.+?)\s*$")
_TEST_PLAN_HEADING = re.compile(r"^\s*#{1,6}\s*(?:test plan|тест-план)\b", re.IGNORECASE)
# The commit a tick was taken on, at the end of the item: `- [x] … - 8b4cd395`.
_CHECKED_AT = re.compile(r"[-–—]\s*`?(?P<sha>[0-9a-f]{7,40})`?\s*$")


def is_test_plan(body: str) -> bool:
    return any(_TEST_PLAN_HEADING.match(line) for line in (body or "").splitlines())


def parse_checklist(body: str) -> list[dict]:
    """Checklist items of a test-plan comment, in order: the check itself,
    whether it is ticked, the commit the tick names, and the last heading above
    it (which is what separates the checks we run from the ones a human does).

    A check long enough to wrap continues on indented lines, and they are part
    of it - the commit of the tick usually ends up there. The commit is then
    split off the text, so appending it to a tick is not read as a different
    check.
    """

    section = ""
    raw: list[tuple[str, str, bool]] = []
    for line in (body or "").splitlines():
        item = _CHECKLIST_ITEM.match(line)
        if item is None:
            if line[:1].isspace() and raw:
                head_section, text, checked = raw[-1]
                raw[-1] = (head_section, f"{text} {line.strip()}", checked)
                continue
            heading = "" if line[:1].isspace() else line.strip().strip("*#: ")
            if heading:
                section = heading
            continue
        raw.append((section, item.group("text"), item.group("mark") in "xX"))

    items: list[dict] = []
    for item_section, text, checked in raw:
        text = " ".join(text.split())
        at = _CHECKED_AT.search(text)
        items.append(
            {
                "section": item_section,
                "text": text[: at.start()].strip() if at else text,
                "checked": checked,
                "sha": at.group("sha") if at else "",
            }
        )
    return items


def authors_bookkeeping(head_body: str, thread_notes: list, own: set[str]) -> bool:
    """Whether a thread is the author's test-plan bookkeeping and nothing else.

    A reviewer acts on code, not on the author's checklist, and a monitor
    restart would otherwise read that list out again. Once we have written in
    the thread ourselves - asking for a check, say - the author's answer to us
    is review traffic and belongs in the stream.
    """

    if not is_test_plan(head_body):
        return False
    return not any(str(note.get("id")) in own for note in thread_notes)


class TestPlanTracker:
    """Turns edits of the test-plan checklist into events.

    The first poll only takes a baseline, so a monitor restart does not replay
    every tick already in the list. Completeness is a state, not a transition:
    an already-complete list is announced once per run, because that is the cue
    to resolve the thread and it must survive a restart.
    """

    def __init__(self) -> None:
        self._checked: dict[str, bool] | None = None
        self._complete = False
        self._stale_seen: set[tuple[str, str]] = set()

    def update(self, body: str, head_sha: str = "") -> list[str]:
        items = parse_checklist(body)
        if not items:
            return []
        events: list[str] = []
        if self._checked is not None:
            for item in items:
                was = self._checked.get(item["text"])
                if was is None or was == item["checked"]:
                    continue
                # Our own tick names the commit it holds for; a human clicks the
                # box and leaves the text alone, and that is the tick to report.
                if item["checked"] and item["sha"]:
                    continue
                where = f"[{item['section']}] " if item["section"] else ""
                verb = "checked" if item["checked"] else "unchecked"
                events.append(f"TEST PLAN {verb}: {where}{item['text']}")
        self._checked = {item["text"]: item["checked"] for item in items}
        complete = all(self._checked.values())
        if complete and not self._complete:
            events.append(
                "TEST PLAN all items checked - resolve the thread once the MR is approved"
            )
        self._complete = complete
        return events + self._stale(items, head_sha)

    def _stale(self, items: list[dict], head_sha: str) -> list[str]:
        """Ticks taken on a commit that is no longer the head: the branch moved
        under them, so those checks say nothing about what would be merged."""

        if not head_sha:
            return []
        events = []
        for item in items:
            if not item["checked"] or not item["sha"] or head_sha.startswith(item["sha"]):
                continue
            key = (item["text"], head_sha)
            if key in self._stale_seen:
                continue
            self._stale_seen.add(key)
            events.append(
                f"TEST PLAN stale: {item['text']} - checked at {item['sha']}, "
                f"head is {head_sha[:8]}"
            )
        return events
