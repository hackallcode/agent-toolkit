"""What a fatal read is in `glab`, and what pagination makes of a page.

HTTP 429 ends the watch on the first answer; a transport that produced no
answer after its two bounded attempts ends it too. A named 4xx is answered
for good and only said out loud. A short page is not an end marker: only an
empty page - or a page that did not arrive - ends the walk.

The subprocess is real: the fakes below replace argv only, so the reading
side - encoding, errors, newline translation - is the one production runs.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


def _glab():
    # The package is imported co-located, because the script in front of it carries a dash.
    spec = importlib.util.spec_from_file_location(
        "mr_watch.glab", _SCRIPTS / "mr_watch" / "glab.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _answering_in_turn(monkeypatch, glab, *answers: tuple[bytes, bytes, int]) -> list:
    """Each call gets the next scripted answer as (stdout, stderr, code)."""

    original = subprocess.run
    seen: list = []

    def run(args, **kwargs):
        payload, err, code = answers[len(seen)]
        seen.append(args)
        script = (
            f"import sys; sys.stdout.buffer.write({payload!r}); "
            f"sys.stderr.buffer.write({err!r}); sys.exit({code})"
        )
        return original([sys.executable, "-c", script], **kwargs)

    monkeypatch.setattr(glab.subprocess, "run", run)
    return seen


def test_a_rate_limit_ends_the_watch_without_a_retry(monkeypatch, capsys) -> None:
    """A 429 only gets a worse answer for asking, so the very first one stops
    everything - no second attempt, no complaint on stderr."""

    glab = _glab()
    seen = _answering_in_turn(
        monkeypatch, glab, (b"", b"glab: API failed (HTTP 429) Too Many Requests", 1)
    )

    with pytest.raises(glab.RateLimitError, match="HTTP 429 rate limit"):
        glab.api("/whatever")
    assert len(seen) == 1
    assert capsys.readouterr().err == ""


def test_a_dead_network_ends_the_watch_after_both_attempts(monkeypatch, capsys) -> None:
    """A nonzero exit that names no HTTP status is a transport failure; two of
    them stop the watch instead of passing for an empty answer."""

    glab = _glab()
    refused = (b"", b'Get "https://private.invalid/": dial tcp: connection refused', 1)
    seen = _answering_in_turn(monkeypatch, glab, refused, refused)

    with pytest.raises(glab.TransportError, match="after 2 attempts"):
        glab.api("/whatever")
    assert len(seen) == 2
    assert capsys.readouterr().err == ""


def test_a_timeout_after_a_server_error_ends_the_watch(monkeypatch, capsys) -> None:
    """A 5xx is worth one more ask; the ask hanging is a transport failure,
    and its exhaustion stops the watch."""

    glab = _glab()
    monkeypatch.setattr(glab, "_DEADLINES", (0.2, 0.2))
    original = subprocess.run
    seen: list = []

    def run(args, **kwargs):
        seen.append(args)
        script = (
            "import time; time.sleep(30)"
            if len(seen) == 1
            else "import sys; sys.stderr.write('glab: HTTP 502'); sys.exit(1)"
        )
        return original([sys.executable, "-c", script], **kwargs)

    monkeypatch.setattr(glab.subprocess, "run", run)

    with pytest.raises(glab.TransportError, match="after 2 attempts"):
        glab.api("/whatever")
    assert len(seen) == 2
    assert capsys.readouterr().err == ""


def test_a_named_4xx_is_answered_for_good_and_only_said_out_loud(monkeypatch, capsys) -> None:
    """A 404 answers the same way however often it is asked: one attempt, a
    complaint on stderr, an empty answer - and the watch lives on."""

    glab = _glab()
    seen = _answering_in_turn(monkeypatch, glab, (b"", b"glab: API failed (HTTP 404)", 1))

    assert glab.api("/whatever") is None
    assert len(seen) == 1
    assert "HTTP 404" in capsys.readouterr().err


def _serving_pages(monkeypatch, glab, pages: dict[str, str]) -> list:
    """`glab api <path>` answers with the scripted body for exactly that path."""

    original = subprocess.run
    seen: list = []

    def run(args, **kwargs):
        seen.append(args[2])
        body = pages[args[2]]
        script = f"import sys; sys.stdout.write({body!r})"
        return original([sys.executable, "-c", script], **kwargs)

    monkeypatch.setattr(glab.subprocess, "run", run)
    return seen


def test_a_short_page_is_not_the_last_page(monkeypatch) -> None:
    """GitLab can return fewer than `per_page` items while a next page exists,
    so the walk ends on an empty page - and takes the short page's items."""

    glab = _glab()
    seen = _serving_pages(
        monkeypatch,
        glab,
        {
            "/things?per_page=100&page=1": '["a", "b", "c"]',
            "/things?per_page=100&page=2": '["d", "e"]',
            "/things?per_page=100&page=3": "[]",
        },
    )

    assert glab.paginated("/things") == ["a", "b", "c", "d", "e"]
    assert seen == [
        "/things?per_page=100&page=1",
        "/things?per_page=100&page=2",
        "/things?per_page=100&page=3",
    ]


def test_a_lost_page_ends_the_walk_with_what_arrived(monkeypatch, capsys) -> None:
    """A page that never arrives is not «no more pages»: the walk stops, says
    so on stderr, and hands over the items read before it."""

    glab = _glab()
    original = subprocess.run
    seen: list = []

    def run(args, **kwargs):
        seen.append(args[2])
        body = '["a"]' if args[2].endswith("page=1") else ""
        script = (
            f"import sys; sys.stdout.write({body!r})"
            if body
            else "import sys; sys.stderr.write('glab: HTTP 500'); sys.exit(1)"
        )
        return original([sys.executable, "-c", script], **kwargs)

    monkeypatch.setattr(glab.subprocess, "run", run)

    assert glab.paginated("/things") == ["a"]
    assert "page 2 lost, 1 items so far" in capsys.readouterr().err
