"""The per-agent marker: how it is spelled and what counts as a signature.

`mr-reply.sh` ends every note with the marker of its writer, so a watcher on
another machine - one that holds no record of our notes - can still tell our
notes from everybody else's comments. Only the closing line signs, because
review tooling quotes a marker back inside its own comment.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


def _threads():
    # The package is imported co-located, because the script in front of it carries a dash.
    spec = importlib.util.spec_from_file_location(
        "mr_watch.threads", _SCRIPTS / "mr_watch" / "threads.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_id_names_the_agent(tmp_path: Path) -> None:
    """The id carries the host and the writer tag: another machine, worktree or
    role runs under the same account, and its notes must not read as ours."""

    threads = _threads()

    ours = threads.agent_id("tag", "host-a")
    assert ours == threads.agent_id("tag", "host-a")
    assert ours != threads.agent_id("tag", "host-b")
    assert ours != threads.agent_id("other-tag", "host-a")
    assert len(ours) == 12
    int(ours, 16)


def test_the_marker_is_an_html_comment_of_the_id() -> None:
    threads = _threads()

    assert threads.agent_marker("tag", "host-a") == f"<!-- agent-toolkit:{threads.agent_id('tag', 'host-a')} -->"


def test_only_the_closing_line_signs() -> None:
    threads = _threads()
    marker = threads.agent_marker("tag", "host-a")

    assert threads.signed_by(f"ответ\n\n{marker}", marker)
    assert threads.signed_by(f"{marker}\n", marker)
    assert not threads.signed_by("", marker)
    assert not threads.signed_by(f"body\n> {marker}\nafter", marker)
    assert not threads.signed_by(f"> {marker}", marker)
    assert not threads.signed_by(f"body\n{threads.agent_marker('other', 'host-a')}", marker)


def test_a_note_is_ours_by_record_or_by_signature(tmp_path: Path) -> None:
    """A record under our tag owns a note on this machine; the marker owns it
    anywhere. A quoted marker, another agent's marker and the bare marker of
    older notes own nothing."""

    threads = _threads()
    notes_file = tmp_path / "notes"
    notes_file.write_text("11 тест\n22 чужой-тег\n")
    tracker = threads.ThreadTracker(notes_file, "тест")

    ours = tracker.marker
    other = threads.agent_marker("другой-агент", "host-a")
    thread = [
        {"id": 11, "body": "головная заметка"},
        {"id": 12, "body": f"ответ\n\n{ours}"},
        {"id": 13, "body": f"> {ours}"},
        {"id": 14, "body": f"замечание\n\n{other}"},
        {"id": 15, "body": "старая заметка\n<!-- agent -->"},
    ]

    assert tracker.own_ids(thread) == {"11", "12"}
    # The tracker's own marker is the one of its tag on this host.
    assert tracker.marker == threads.agent_marker("тест")


def test_the_tracker_hands_out_what_awaits_us(tmp_path: Path) -> None:
    """A thread waits on us until its latest note is ours - by record or by
    signature; what we have already seen is not handed out twice."""

    threads = _threads()
    notes_file = tmp_path / "notes"
    tracker = threads.ThreadTracker(notes_file, "тест")
    ours = threads.agent_marker("тест")
    thread = [
        {"id": 21, "body": "вопрос ревьюера"},
        {"id": 22, "body": f"наш ответ\n\n{ours}"},
        {"id": 23, "body": "встречный вопрос"},
    ]

    first = tracker.pending(thread)
    assert [note["id"] for note in first] == [21, 23]

    # The signature counts even with no record file at all, and a second poll
    # of the same thread says nothing new.
    thread_answered = thread[:2]
    assert tracker.pending(thread_answered) == []
