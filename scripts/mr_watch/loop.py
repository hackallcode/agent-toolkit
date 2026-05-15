"""What both monitors are made of: their arguments, the threads awaiting us, and
the line that ends the watch.

The two entry points differ in what they report, not in how they poll, and each
of them names its own role so nobody has to pick a flag to get the right stream.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable, Iterator
from urllib.parse import quote

from mr_watch.glab import api, default_project, paginated
from mr_watch.render import render_note, where
from mr_watch.threads import ThreadTracker, is_resolved, own_notes_file, writer_tag


class Watch:
    """One MR under watch: where to ask, and whose notes are already answered."""

    def __init__(self, description: str) -> None:
        ap = argparse.ArgumentParser(description=description)
        ap.add_argument("--mr", required=True, help="MR iid")
        ap.add_argument("--project", default=None, help="group/repo (default: origin remote)")
        ap.add_argument("--interval", type=int, default=15, help="poll seconds (default 15)")
        ap.add_argument(
            "--record-separator",
            action="store_true",
            help="frame logical events with NUL for stream wrappers",
        )
        args = ap.parse_args()

        project = args.project or default_project()
        if not project:
            print("mr-watch: could not resolve project (pass --project)", flush=True)
            raise SystemExit(2)

        self.mr = args.mr
        self.project = quote(project, safe="")
        self.interval = args.interval
        self.base = f"projects/{self.project}/merge_requests/{self.mr}"
        self.separator = "\0" if args.record_separator else "\n"
        tag = writer_tag()
        self.tracker = ThreadTracker(own_notes_file(), tag)
        # A tag mismatch with `mr-reply.sh` would replay our own answers, so say it out loud.
        print(
            f"mr-watch: own notes tagged '{tag}' in {own_notes_file()},"
            f" signed {self.tracker.marker}",
            file=sys.stderr,
            flush=True,
        )

    def state(self) -> tuple[str, str, dict]:
        info = api(self.base)
        if not isinstance(info, dict):
            return "", "", {}
        return info.get("state") or "", info.get("sha") or "", info

    def threads(self) -> Iterator[tuple[dict, list, str]]:
        """Every MR discussion as (head note, notes, label) - resolvable review
        threads and plain comments alike, because an automated alert arrives as
        the latter and must not be missed."""

        for disc in paginated(f"{self.base}/discussions"):
            notes = disc.get("notes") or []
            head = notes[0] if notes else {}
            loc = where(head.get("position"))
            label = (
                f"MR!{self.mr} review comment{loc}"
                if head.get("resolvable")
                else f"MR!{self.mr} comment"
            )
            yield head, notes, label

    def report_comments(self, skip: Callable[[dict, list], bool] | None = None) -> None:
        for head, notes, label in self.threads():
            if skip is not None and skip(head, notes):
                continue
            for note in self.tracker.pending(notes, resolved=is_resolved(notes)):
                self.emit(render_note(note, label, self.project))
        for commit in paginated(f"{self.base}/commits"):
            sha = commit.get("id")
            if not sha:
                continue
            path = f"projects/{self.project}/repository/commits/{sha}/discussions"
            for disc in paginated(path):
                for note in self.tracker.pending(disc.get("notes") or []):
                    event = render_note(
                        note, f"MR!{self.mr} commit {sha[:8]} comment", self.project
                    )
                    self.emit(event)

    def emit(self, event: str) -> None:
        print(event, end=self.separator, flush=True)

    def say(self, event: str) -> None:
        self.emit(f"MR!{self.mr} {event}")

    def closed(self, state: str, info: dict) -> bool:
        """The MR left the opened state: say who did it, and the watch is over."""

        if not state or state == "opened":
            return False
        who = info.get("merged_by") or info.get("closed_by") or {}
        name = who.get("username", "?") if isinstance(who, dict) else "?"
        verb = "MERGED" if state == "merged" else state.upper()
        self.say(f"{verb} by {name} - stopping the watch")
        return True
