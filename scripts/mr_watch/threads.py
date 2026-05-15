"""Whose note a thread is waiting on.

The reviewer and the pushing agent can be the same forge user, so a note is
ours by record or by signature, not by author: `mr-reply.sh` writes
`<note id> <writer tag>` and ends the note with our marker, and either one under
our own tag counts. A thread awaits us until its latest note is ours.

Run as a script, it prints the marker for the current writer tag; that is how
`mr-reply.sh` signs a note, so the two cannot spell it differently."""

from __future__ import annotations

import hashlib
import os
import pathlib
import subprocess


def own_notes_file() -> pathlib.Path:
    # Outside the repo, so the record of our replies survives branch switches.
    configured = os.environ.get("MR_AGENT_NOTES")
    return pathlib.Path(configured or "~/.agent-toolkit/mr-agent-notes").expanduser()


def writer_tag() -> str:
    # Every agent here appends to one notes file, so "ours" needs a per-agent tag.
    tag = os.environ.get("MR_AGENT_TAG", "").strip()
    if tag:
        return tag
    try:
        root = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
        ).stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        root = ""
    # The working-tree root: a session owns its worktree, and `mr-reply.sh` derives it the same way.
    return root or os.getcwd()


def agent_id(tag: str, host: str | None = None) -> str:
    # Another machine keeps no record of our notes, so the id carries the host as well as the tag.
    host = os.uname().nodename if host is None else host
    return hashlib.sha256(f"{host}\n{tag}".encode()).hexdigest()[:12]


def agent_marker(tag: str, host: str | None = None) -> str:
    return f"<!-- agent-toolkit:{agent_id(tag, host)} -->"


def signed_by(body: str, marker: str) -> bool:
    # Only the closing line signs: review tooling quotes a marker back inside its own comment.
    lines = [line.strip() for line in body.splitlines() if line.strip()]
    return bool(lines) and lines[-1] == marker


def own_note_ids(notes_file: pathlib.Path, tag: str) -> set[str]:
    # A record is `<note id> <writer tag>`; another tag, or none, means other tooling wrote it.
    try:
        lines = notes_file.read_text("utf-8").splitlines()
    except OSError:
        return set()
    ids = set()
    for line in lines:
        note_id, _, line_tag = line.strip().partition(" ")
        if note_id and line_tag.strip() == tag:
            ids.add(note_id)
    return ids


def is_resolved(thread_notes: list) -> bool:
    head = thread_notes[0] if thread_notes else {}
    if not head.get("resolvable"):
        return False
    return any(note.get("resolved") for note in thread_notes if note.get("resolvable"))


class ThreadTracker:
    """Hands out the notes of a thread that still await our action."""

    def __init__(self, notes_file: pathlib.Path, tag: str) -> None:
        self.notes_file = notes_file
        self.marker = agent_marker(tag)
        self._tag = tag
        self._seen: set[str] = set()

    def own_ids(self, thread_notes: list) -> set[str]:
        """Our notes in a thread: recorded under our tag, or signed with our marker."""

        signed = {
            str(note.get("id"))
            for note in thread_notes
            if signed_by(note.get("body") or "", self.marker)
        }
        # Re-read per poll so a reply posted after the watcher started counts.
        return own_note_ids(self.notes_file, self._tag) | signed

    def pending(self, thread_notes: list, *, resolved: bool = False) -> list[dict]:
        notes = [note for note in thread_notes if not note.get("system")]
        # A resolved thread stays unmarked, so re-opening it hands out its notes again.
        if not notes or resolved:
            return []
        own = self.own_ids(notes)
        known = self._seen | own
        fresh = [note for note in notes if str(note.get("id")) not in known]
        self._seen.update(str(note.get("id")) for note in fresh)
        return fresh


if __name__ == "__main__":
    print(agent_marker(writer_tag()))
