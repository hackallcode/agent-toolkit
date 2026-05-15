"""Real offline watcher CLIs stop fanout and polling after fatal reads."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
_BASE = "projects/offline%2Fwatch/merge_requests/7"
_DISCUSSIONS = f"{_BASE}/discussions?per_page=100&page=1"
_COMMITS = f"{_BASE}/commits?per_page=100&page=1"
_COMPARE = "projects/offline%2Fwatch/repository/compare?from=base&to=head"
_COMMIT_DISCUSSIONS = (
    "projects/offline%2Fwatch/repository/commits/first/discussions?per_page=100&page=1"
)
_PIPELINES = f"{_BASE}/pipelines?per_page=100&page=1"
_JOBS = "projects/offline%2Fwatch/pipelines/9/jobs?per_page=100&page=1"
_TRACE = "projects/offline%2Fwatch/jobs/11/trace"
_NOTE = {"id": 1, "body": "Review this", "author": {"username": "reviewer"}}
_HEALTHY = (_BASE, {"state": "opened", "sha": "head"})


def _scenario(location: str) -> tuple[list[tuple[str, object]], str]:
    if location == "state":
        return [], _BASE
    if location == "discussions":
        return [_HEALTHY], _DISCUSSIONS
    if location == "page":
        return [_HEALTHY, (_DISCUSSIONS, [{"notes": [_NOTE]}])], _DISCUSSIONS[:-1] + "2"
    if location == "compare":
        note = {
            **_NOTE,
            "position": {
                "new_path": "file.py",
                "new_line": 1,
                "base_sha": "base",
                "head_sha": "head",
            },
        }
        return [
            _HEALTHY,
            (_DISCUSSIONS, [{"notes": [note]}]),
            (_DISCUSSIONS[:-1] + "2", []),
        ], _COMPARE
    comments = [_HEALTHY, (_DISCUSSIONS, [])]
    if location == "commits":
        return comments, _COMMITS
    if location == "commit-fanout":
        return comments + [
            (_COMMITS, [{"id": "first"}, {"id": "second"}]),
            (_COMMITS[:-1] + "2", []),
        ], _COMMIT_DISCUSSIONS
    author = comments + [(_COMMITS, []), (_DISCUSSIONS, [])]
    if location == "trace":
        return author + [
            (_PIPELINES, [{"id": 9, "sha": "head", "status": "failed"}]),
            (_PIPELINES[:-1] + "2", []),
            (_JOBS, [{"id": 11, "name": "offline", "status": "failed"}]),
            (_JOBS[:-1] + "2", []),
        ], _TRACE
    assert location == "approvals"
    return author + [(_PIPELINES, [])], f"{_BASE}/approvals"


def _run_watch(tmp_path, role, failure, location):
    prefix, target = _scenario(location)
    plan = [{"path": path, "body": body} for path, body in prefix]
    attempts = 1 if failure == "429" else 2
    if failure == "429":
        error = "glab: private-response-sentinel (HTTP\n 429) Retry-After: private-header-sentinel"
    else:
        error = 'Get "https://private-url-sentinel/": dial tcp: connection refused'
    plan.extend(
        {"path": target, "error": error, "hang": failure == "timeout"} for _ in range(attempts)
    )
    plan_file = tmp_path / "plan.json"
    plan_file.write_text(json.dumps(plan), encoding="utf-8")
    request_file = tmp_path / "requests.jsonl"
    fake = tmp_path / "glab"
    fake.write_text(
        f"#!{sys.executable}\n"
        "import json, pathlib, sys, time\n"
        f"plan = json.loads(pathlib.Path({str(plan_file)!r}).read_text())\n"
        f"log = pathlib.Path({str(request_file)!r})\n"
        "seen = log.read_text().splitlines() if log.exists() else []\n"
        "with log.open('a') as out:\n"
        "    out.write(json.dumps(sys.argv[1:]) + '\\n')\n"
        "if len(seen) >= len(plan) or sys.argv[1:] != ['api', plan[len(seen)]['path']]:\n"
        "    sys.stderr.write('unexpected offline request')\n"
        "    sys.exit(1)\n"
        "reply = plan[len(seen)]\n"
        "if reply.get('hang'):\n"
        "    time.sleep(30)\n"
        "if 'error' in reply:\n"
        "    sys.stderr.write(reply['error'])\n"
        "    sys.exit(1)\n"
        "print(json.dumps(reply['body']))\n",
        encoding="utf-8",
    )
    fake.chmod(0o755)
    command = [sys.executable, str(_SCRIPTS / f"mr-{role}-watch.py")]
    if failure == "timeout":
        launcher = (
            "import runpy, sys; "
            f"sys.path.insert(0, {str(_SCRIPTS)!r}); "
            "from mr_watch import glab; glab._DEADLINES = (1.0, 1.0); "
            f"runpy.run_path({str(_SCRIPTS / f'mr-{role}-watch.py')!r}, run_name='__main__')"
        )
        command = [sys.executable, "-c", launcher]
    env = {
        "PATH": str(tmp_path),
        "HOME": str(tmp_path),
        "MR_AGENT_TAG": "offline-watch",
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    result = subprocess.run(
        command + ["--mr", "7", "--project", "offline/watch", "--interval", "0"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=5,
    )
    requests = [json.loads(line) for line in request_file.read_text().splitlines()]
    assert result.returncode == 1, result.stderr
    assert requests == [["api", path] for path, _ in prefix] + [["api", target]] * attempts
    assert result.stdout == ""
    assert "Traceback" not in result.stderr
    assert "sentinel" not in result.stderr
    reason = "HTTP 429 rate limit" if failure == "429" else "network/timeout read failed after 2 attempts"
    assert result.stderr.splitlines()[-1] == f"mr-watch: {reason}; stopping the watch"
    return requests


@pytest.mark.parametrize("role", ["author", "review"])
@pytest.mark.parametrize("failure", ["429", "network"])
@pytest.mark.parametrize(
    "location", ["state", "discussions", "page", "compare", "commits", "commit-fanout"]
)
def test_fatal_read_stops_cross_endpoint_fanout_and_future_ticks(tmp_path, role, failure, location):
    _run_watch(tmp_path, role, failure, location)


@pytest.mark.parametrize("failure", ["429", "network"])
@pytest.mark.parametrize("location", ["trace", "approvals"])
def test_author_only_reads_stop_the_whole_watch(tmp_path, failure, location):
    _run_watch(tmp_path, "author", failure, location)


@pytest.mark.parametrize("role", ["author", "review"])
def test_actual_subprocess_timeouts_stop_before_the_next_endpoint(tmp_path, role):
    _run_watch(tmp_path, role, "timeout", "discussions")
