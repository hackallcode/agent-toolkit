"""What `mr-reply.sh` refuses to do with a review thread and with the MR itself.

Boxes are counted in the head note of a thread: a reply carrying them takes the
plan into the feed, where nobody adds it up. A resolve over unticked items
passes the plan off as done. The head note of a thread is rewritten, and the thread put
back to open, by whoever wrote it, and a body that never arrived is not written
at all. And the title and the description of the MR are English, which nothing
checked before.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"

pytestmark = pytest.mark.skipif(not shutil.which("jq"), reason="the script reads answers with jq")

_FAKE_GLAB = """#!/usr/bin/env bash
echo "$@" >>"$CALLS"
case "$*" in
  *--method\\ PUT*) echo '{}' ;;
  *-X\\ POST*) echo '{"id": "тред-новый", "notes": [{"id": 1}]}' ;;
  *--method\\ POST*) tee -a "$POSTED" | jq -c '{id: "тред-новый", notes: [{id: 1, position: .position}]}' ;;
  *diffs*) cat "${DIFFS:-/dev/null}" ;;
  *discussions*) cat "$DISCUSSION" ;;
  *) cat "$MR" ;;
esac
"""

_MR_ENGLISH = '{"title": "Hold the rules in the script", "description": "A body in English."}'


def _run(
    tmp_path: Path,
    args: list[str],
    body: str,
    discussion: str,
    mr: str = _MR_ENGLISH,
    locale: str | None = None,
    plain_awk: bool = False,
    tag: str = "тест",
    notes: str = "77 тест\n",
    stdin: str | None = None,
    diffs: str | None = None,
    extra_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    glab = fake_bin / "glab"
    glab.write_text(_FAKE_GLAB)
    glab.chmod(0o755)
    if plain_awk:
        # `gawk --traditional` reads `{7,}` as its own text, which is what a
        # stripped-down awk does - the one place the difference shows.
        awk = fake_bin / "awk"
        awk.write_text('#!/bin/sh\nexec gawk --traditional "$@"\n')
        awk.chmod(0o755)
    (tmp_path / "discussion.json").write_text(discussion)
    (tmp_path / "mr.json").write_text(mr)
    (tmp_path / "body.md").write_text(body)
    (tmp_path / "notes").write_text(notes)
    env = {
        **os.environ,
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "CALLS": str(tmp_path / "calls.txt"),
        "DISCUSSION": str(tmp_path / "discussion.json"),
        "MR": str(tmp_path / "mr.json"),
        # Named outright, so the test does not read the remote of whatever
        # checkout it happens to run in.
        "MR_PROJECT": "group/repo",
        "MR_AGENT_NOTES": str(tmp_path / "notes"),
        "MR_AGENT_TAG": tag,
        "POSTED": str(tmp_path / "posted.json"),
    }
    if diffs is not None:
        (tmp_path / "diffs.json").write_text(diffs)
        env["DIFFS"] = str(tmp_path / "diffs.json")
    env.update(extra_env or {})
    if locale is not None:
        env["LC_ALL"] = locale
    return subprocess.run(
        [str(_SCRIPTS / "mr-reply.sh"), "394", *args],
        capture_output=True,
        text=True,
        env=env,
        input=stdin,
    )


def _marker(tag: str) -> str:
    # The script spells the marker by running the module, so the test asks the module too.
    return subprocess.run(
        [sys.executable, str(_SCRIPTS / "mr_watch" / "threads.py")],
        capture_output=True,
        text=True,
        env={**os.environ, "MR_AGENT_TAG": tag},
        check=True,
    ).stdout.strip()


_PLAN_DONE = '{"notes": [{"id": 77, "body": "**Автоматические проверки**\\n- [x] всё сделано - abc1234"}]}'
_PLAN_LEFT = (
    '{"notes": [{"id": 77, "body": "**Автоматические проверки**\\n- [x] одно - abc1234\\n- [ ] другое"}]}'
)
_PLAN_NO_SHA = (
    '{"notes": [{"id": 77, "body": "**Автоматические проверки**\\n- [x] прогнал, а чем - не сказал"}]}'
)
_PLAN_MANUAL = (
    '{"notes": [{"id": 77, "body": "**Ручные проверки**\\n- [x] владелец посмотрел"}]}'
)
_PLAN_SOMEONE_ELSE = (
    '{"notes": [{"id": 999, "body": "**Автоматические проверки**\\n- [x] всё сделано - abc1234"}]}'
)


def test_a_thread_with_an_unchecked_item_is_not_resolved(tmp_path: Path) -> None:
    result = _run(tmp_path, ["--resolve", "тред-1"], "", _PLAN_LEFT)

    assert result.returncode == 1
    assert "left unchecked" in result.stderr
    assert "resolved=true" not in (tmp_path / "calls.txt").read_text()


def test_a_thread_with_everything_checked_is_resolved(tmp_path: Path) -> None:
    result = _run(tmp_path, ["--resolve", "тред-1"], "", _PLAN_DONE)

    assert result.returncode == 0
    assert "resolved=true" in (tmp_path / "calls.txt").read_text()


def test_a_reply_may_not_carry_checkboxes(tmp_path: Path) -> None:
    result = _run(tmp_path, ["тред-1", str(tmp_path / "body.md")], "- [x] сделано\n", _PLAN_DONE)

    assert result.returncode == 1
    assert "head note" in result.stderr


def test_the_head_of_a_thread_may_carry_them(tmp_path: Path) -> None:
    result = _run(tmp_path, ["--new", str(tmp_path / "body.md")], "- [ ] план\n", _PLAN_DONE)

    assert result.returncode == 0


def test_a_thread_somebody_else_opened_is_not_resolved(tmp_path: Path) -> None:
    """Somebody else's thread is resolved by its author: they check the fix,
    while all we see is our own report of it."""

    result = _run(tmp_path, ["--resolve", "тред-1"], "", _PLAN_SOMEONE_ELSE)

    assert result.returncode == 1
    assert "not ours" in result.stderr


def test_an_automatic_tick_without_a_sha_blocks_the_resolve(tmp_path: Path) -> None:
    """A tick without a sha does not say which state of the branch the check
    belongs to: the branch moves on and the tick passes an old run off as new."""

    result = _run(tmp_path, ["--resolve", "тред-1"], "", _PLAN_NO_SHA)

    assert result.returncode == 1
    assert "naming no run" in result.stderr


def test_a_manual_tick_needs_no_sha(tmp_path: Path) -> None:
    """An item under `Ручные проверки` is ticked by the human, who has no sha to name."""

    result = _run(tmp_path, ["--resolve", "тред-1"], "", _PLAN_MANUAL)

    assert result.returncode == 0


def test_a_long_dash_is_refused(tmp_path: Path) -> None:
    result = _run(tmp_path, ["тред-1", str(tmp_path / "body.md")], "ответ — с тире\n", _PLAN_DONE)

    assert result.returncode == 1
    assert "long dash" in result.stderr


def test_the_marker_is_added_when_it_is_missing(tmp_path: Path) -> None:
    """The marker for the human is added by the script: it cannot be forgotten."""

    _run(tmp_path, ["тред-1", str(tmp_path / "body.md")], "короткий ответ\n", _PLAN_DONE)

    assert _marker("тест") in (tmp_path / "calls.txt").read_text()


def test_the_marker_is_not_stacked_over_itself(tmp_path: Path) -> None:
    """`--show` hands back a body that already ends with the marker, and a
    round trip through `--edit` must not put another one under it."""

    body = f"ответ\n\n{_marker('тест')}"
    result = _run(tmp_path, ["--edit", "тред-1", str(tmp_path / "body.md")], body, _PLAN_DONE)

    assert result.returncode == 0, result.stderr
    assert (tmp_path / "calls.txt").read_text().count(_marker("тест")) == 1


def test_a_marker_of_our_own_is_no_body(tmp_path: Path) -> None:
    """The emptiness check reads the body through the marker: the marker alone
    would wipe the note and pass for a plan."""

    result = _run(tmp_path, ["--edit", "тред-1", str(tmp_path / "body.md")], f"\n{_marker('тест')}\n", _PLAN_DONE)

    assert result.returncode == 1
    assert "empty body" in result.stderr
    assert "PUT" not in (tmp_path / "calls.txt").read_text()


def test_a_russian_description_stops_every_note(tmp_path: Path) -> None:
    """The MR is read by people who do not read Russian, and the description is
    written once: the refusal comes before the note, not at review."""

    russian = '{"title": "Fine", "description": "А описание осталось русским."}'
    result = _run(tmp_path, ["тред-1", str(tmp_path / "body.md")], "reply\n", _PLAN_DONE, russian)

    assert result.returncode == 1
    assert "not English" in result.stderr
    assert "POST" not in (tmp_path / "calls.txt").read_text()


def test_a_russian_title_stops_every_note(tmp_path: Path) -> None:
    russian = '{"title": "Держим правила в скрипте", "description": "An English body."}'
    result = _run(tmp_path, ["--resolve", "тред-1"], "", _PLAN_DONE, russian)

    assert result.returncode == 1
    assert "not English" in result.stderr
    assert "resolved=true" not in (tmp_path / "calls.txt").read_text()


def test_a_quoted_russian_block_is_allowed(tmp_path: Path) -> None:
    """A description quotes logs and screens of ours, and those are not English."""

    quoted = (
        '{"title": "Fine", "description": '
        '"An English body.\\n\\n```\\nбот молчит после переезда\\n```\\n"}'
    )
    result = _run(tmp_path, ["тред-1", str(tmp_path / "body.md")], "reply\n", _PLAN_DONE, quoted)

    assert result.returncode == 0


def test_a_russian_description_in_lowercase_is_refused(tmp_path: Path) -> None:
    """Cyrillic without a capital letter is still Cyrillic, and a check whose
    grep gave up says nothing while letting the note through."""

    russian = '{"title": "Fine", "description": "правим скрипт по-русски"}'
    result = _run(
        tmp_path, ["тред-1", str(tmp_path / "body.md")], "reply\n", _PLAN_DONE, russian, "C"
    )

    assert result.returncode == 1
    assert "not English" in result.stderr
    assert "grep:" not in result.stderr


def test_a_russian_body_without_a_dash_passes(tmp_path: Path) -> None:
    """The two dashes are searched as fixed strings: read as a range of bytes
    they swallow Cyrillic, and a plain Russian reply stops going out."""

    result = _run(
        tmp_path, ["тред-1", str(tmp_path / "body.md")], "ответ без тире\n", _PLAN_DONE, locale="C"
    )

    assert result.returncode == 0, result.stderr
    assert "grep:" not in result.stderr


def test_a_reviewer_still_writes_in_a_russian_mr(tmp_path: Path) -> None:
    """Reviewing, we write in somebody's MR and cannot rewrite its title: a
    refusal there would silence the very comment asking for the rewrite."""

    russian = '{"title": "Держим правила в скрипте", "description": "An English body."}'
    result = _run(
        tmp_path,
        ["тред-1", str(tmp_path / "body.md")],
        "reply\n",
        _PLAN_DONE,
        russian,
        tag="review-mr-394",
    )

    assert result.returncode == 0
    assert "not English" in result.stderr
    assert _marker("review-mr-394") in (tmp_path / "calls.txt").read_text()


def test_a_thread_written_under_another_tag_is_not_ours(tmp_path: Path) -> None:
    """The file of notes is shared between the author and the reviewer, so the
    id alone says nothing: the thread of a reviewer stays theirs to resolve."""

    result = _run(
        tmp_path,
        ["--resolve", "тред-1"],
        "",
        _PLAN_DONE,
        notes="77 review-mr-394\n",
    )

    assert result.returncode == 1
    assert "not ours" in result.stderr
    assert "resolved=true" not in (tmp_path / "calls.txt").read_text()


def test_a_manual_group_is_read_without_stars(tmp_path: Path) -> None:
    """The plan is written by hand, and the heading is prose: a human tick under
    `Ручные проверки:` must not be read as an automatic one with no run."""

    plan = '{"notes": [{"id": 77, "body": "Ручные проверки:\\n- [x] владелец посмотрел"}]}'
    result = _run(tmp_path, ["--resolve", "тред-1"], "", plan)

    assert result.returncode == 0, result.stderr


def test_a_hex_word_in_the_middle_is_not_a_run(tmp_path: Path) -> None:
    """`- [x] <проверка> - <sha>` is the form: a hex-looking word anywhere in the
    line would let a tick claim a run it never had."""

    plan = (
        '{"notes": [{"id": 77, "body": "**Автоматические проверки**\\n- [x] deface0 и никакого прогона"}]}'
    )
    result = _run(tmp_path, ["--resolve", "тред-1"], "", plan)

    assert result.returncode == 1
    assert "naming no run" in result.stderr


@pytest.mark.skipif(not shutil.which("gawk"), reason="nothing here to play an awk without intervals")
def test_a_tick_with_a_sha_is_read_where_awk_knows_no_intervals(tmp_path: Path) -> None:
    """`{7,}` in a stripped-down awk matches its own text, and every automatic
    tick then reads as one without a run behind it."""

    result = _run(tmp_path, ["--resolve", "тред-1"], "", _PLAN_DONE, locale="C", plain_awk=True)

    assert result.returncode == 0, result.stderr
    assert "resolved=true" in (tmp_path / "calls.txt").read_text()


def test_a_thread_somebody_else_opened_is_not_edited(tmp_path: Path) -> None:
    """The plan is rewritten in place, and the file of notes says whose head note
    that is: the thread of a reviewer stays theirs to write in."""

    edit = ["--edit", "тред-1", str(tmp_path / "body.md")]
    result = _run(tmp_path, edit, "- [ ] план\n", _PLAN_SOMEONE_ELSE)

    assert result.returncode == 1
    assert "not ours" in result.stderr
    assert "PUT" not in (tmp_path / "calls.txt").read_text()


def test_a_head_note_written_under_another_tag_is_not_edited(tmp_path: Path) -> None:
    """The file is shared between the author and the reviewer, so the id alone
    says nothing: a head note recorded by the reviewer is theirs to rewrite."""

    edit = ["--edit", "тред-1", str(tmp_path / "body.md")]
    result = _run(tmp_path, edit, "- [ ] план\n", _PLAN_DONE, notes="77 review-mr-394\n")

    assert result.returncode == 1
    assert "not ours" in result.stderr
    assert "PUT" not in (tmp_path / "calls.txt").read_text()


def test_the_head_note_of_our_thread_is_rewritten_with_its_boxes(tmp_path: Path) -> None:
    """Boxes are counted in the head note, so the body that goes over it carries
    them: the guard keeping them out of a reply must not reach here. The note is
    in the file already, and writing it a second time would count it twice."""

    edit = ["--edit", "тред-1", str(tmp_path / "body.md")]
    result = _run(tmp_path, edit, "- [x] прогнал - abc1234\n", _PLAN_DONE)

    calls = (tmp_path / "calls.txt").read_text()
    assert result.returncode == 0, result.stderr
    assert "--method PUT projects/group%2Frepo/merge_requests/394/notes/77" in calls
    assert "- [x] прогнал - abc1234" in calls
    assert result.stdout.strip() == "77"
    assert (tmp_path / "notes").read_text() == "77 тест\n"


def test_an_edited_body_is_read_from_stdin_as_well(tmp_path: Path) -> None:
    """Naming no file is how a body is piped in, and that path stays open to the
    mode that rewrites the plan."""

    result = _run(tmp_path, ["--edit", "тред-1"], "", _PLAN_DONE, stdin="- [ ] новая проверка\n")

    assert result.returncode == 0, result.stderr
    assert "- [ ] новая проверка" in (tmp_path / "calls.txt").read_text()


def test_a_body_file_that_is_not_there_writes_nothing(tmp_path: Path) -> None:
    """A path with a typo in it must not reach the note: read as no file at all
    it would put an empty body over the plan, ticks of a human and all."""

    edit = ["--edit", "тред-1", str(tmp_path / "typo.md")]
    result = _run(tmp_path, edit, "", _PLAN_DONE, stdin="")

    assert result.returncode != 0
    assert "PUT" not in (tmp_path / "calls.txt").read_text()


def test_an_empty_body_does_not_go_over_the_plan(tmp_path: Path) -> None:
    """The marker the script adds is not a body: what is left after it has to
    say something, or the note is wiped."""

    result = _run(tmp_path, ["--edit", "тред-1", str(tmp_path / "body.md")], "", _PLAN_DONE)

    assert result.returncode == 1
    assert "empty body" in result.stderr
    assert "PUT" not in (tmp_path / "calls.txt").read_text()


def test_a_long_dash_in_an_edited_note_is_refused(tmp_path: Path) -> None:
    edit = ["--edit", "тред-1", str(tmp_path / "body.md")]
    result = _run(tmp_path, edit, "план — с тире\n", _PLAN_DONE)

    assert result.returncode == 1
    assert "long dash" in result.stderr


def test_the_head_note_is_read_back_whoever_wrote_it(tmp_path: Path) -> None:
    """The body is read from the forge before it is rewritten, and reading is
    what the thread of somebody else is for: it answers with their plan too."""

    result = _run(tmp_path, ["--show", "тред-1"], "", _PLAN_SOMEONE_ELSE)

    assert result.returncode == 0, result.stderr
    assert "- [x] всё сделано - abc1234" in result.stdout


def test_a_thread_somebody_else_opened_is_not_re_opened(tmp_path: Path) -> None:
    result = _run(tmp_path, ["--unresolve", "тред-1"], "", _PLAN_SOMEONE_ELSE)

    assert result.returncode == 1
    assert "not ours" in result.stderr
    assert "resolved=false" not in (tmp_path / "calls.txt").read_text()


def test_a_thread_under_another_tag_is_not_re_opened(tmp_path: Path) -> None:
    """Whose the thread is comes from the tag beside the id, not from the id
    being in the file at all."""

    result = _run(tmp_path, ["--unresolve", "тред-1"], "", _PLAN_DONE, notes="77 review-mr-394\n")

    assert result.returncode == 1
    assert "not ours" in result.stderr
    assert "resolved=false" not in (tmp_path / "calls.txt").read_text()


def test_our_thread_is_re_opened_over_an_unchecked_item(tmp_path: Path) -> None:
    """An item left unticked is the reason to re-open a thread, so it cannot be
    a reason to refuse."""

    result = _run(tmp_path, ["--unresolve", "тред-1"], "", _PLAN_LEFT)

    assert result.returncode == 0, result.stderr
    assert "resolved=false" in (tmp_path / "calls.txt").read_text()
    assert result.stdout.strip() == "тред-1"


_MR_WITH_REFS = (
    '{"title": "Hold the rules in the script", "description": "A body in English.",'
    ' "diff_refs": {"base_sha": "b1", "start_sha": "s1", "head_sha": "h1"}}'
)


def _posted(tmp_path: Path) -> dict:
    # The first body posted: a second one is the retry with the old side named.
    return json.JSONDecoder().raw_decode((tmp_path / "posted.json").read_text())[0]["position"]


def test_an_inline_comment_on_a_renamed_file_names_its_old_path(tmp_path: Path) -> None:
    """The base side of a renamed file has the old name; with new_path twice the
    comment does not land."""

    diffs = '[{"old_path": "src/old.py", "new_path": "src/new.py", "renamed_file": true}]'
    result = _run(
        tmp_path, ["--line", "src/new.py:12", str(tmp_path / "body.md")], "заметка\n", "{}",
        mr=_MR_WITH_REFS, diffs=diffs,
    )

    assert result.returncode == 0, result.stderr
    position = _posted(tmp_path)
    assert (position["old_path"], position["new_path"], position["new_line"]) == (
        "src/old.py", "src/new.py", 12)


def test_an_inline_comment_on_a_file_kept_in_place_names_it_twice(tmp_path: Path) -> None:
    result = _run(
        tmp_path, ["--line", "src/a.py:3", str(tmp_path / "body.md")], "заметка\n", "{}",
        mr=_MR_WITH_REFS,
    )

    assert result.returncode == 0, result.stderr
    position = _posted(tmp_path)
    assert position["old_path"] == position["new_path"] == "src/a.py"
    assert "line_range" not in position


def test_a_range_is_anchored_on_its_last_line(tmp_path: Path) -> None:
    result = _run(
        tmp_path, ["--line", "src/a.py:60-64", str(tmp_path / "body.md")], "заметка\n", "{}",
        mr=_MR_WITH_REFS,
    )

    assert result.returncode == 0, result.stderr
    position = _posted(tmp_path)
    code = hashlib.sha1(b"src/a.py").hexdigest()
    assert position["new_line"] == 64
    assert position["line_range"] == {
        "start": {"line_code": f"{code}__60", "type": "new", "new_line": 60},
        "end": {"line_code": f"{code}__64", "type": "new", "new_line": 64},
    }


def test_a_long_reply_is_refused(tmp_path: Path) -> None:
    result = _run(tmp_path, ["тред-1", str(tmp_path / "body.md")], "а" * 1501, _PLAN_DONE)

    assert result.returncode == 1
    assert "limit is 1500" in result.stderr
    assert not (tmp_path / "calls.txt").exists() or "POST" not in (tmp_path / "calls.txt").read_text()


def test_a_long_reply_goes_with_a_named_reason(tmp_path: Path) -> None:
    result = _run(
        tmp_path, ["тред-1", str(tmp_path / "body.md")], "а" * 1501, _PLAN_DONE,
        extra_env={"MR_ALLOW_LONG": "автор просил разбор каждого случая целиком"},
    )

    assert result.returncode == 0, result.stderr


def test_a_reason_does_not_lift_the_ceiling(tmp_path: Path) -> None:
    result = _run(
        tmp_path, ["тред-1", str(tmp_path / "body.md")], "а" * 6001, _PLAN_DONE,
        extra_env={"MR_ALLOW_LONG": "автор просил разбор каждого случая целиком"},
    )

    assert result.returncode == 1
    assert "ceiling of 6000" in result.stderr


def test_a_placeholder_is_not_a_reason(tmp_path: Path) -> None:
    result = _run(
        tmp_path, ["тред-1", str(tmp_path / "body.md")], "а" * 1501, _PLAN_DONE,
        extra_env={"MR_ALLOW_LONG": "1"},
    )

    assert result.returncode == 1


def test_a_long_plan_in_the_head_note_is_not_counted(tmp_path: Path) -> None:
    plan = "**Автоматические**\n" + "- [ ] проверка\n" * 200
    result = _run(tmp_path, ["--new", str(tmp_path / "body.md")], plan, _PLAN_DONE)

    assert result.returncode == 0, result.stderr

