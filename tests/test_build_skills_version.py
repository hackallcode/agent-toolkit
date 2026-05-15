"""The plugin version that `build-skills.py` stamps into the manifests of a build.

Both CLIs install a new build only when the version changes, so every build of
`dist` needs its own number: `<major>.<minor>` from the manifests and the
first-parent commit count as patch. A clone that cannot count commits is refused,
and a plain build leaves the tracked manifests alone.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

_TOOLKIT = Path(__file__).resolve().parents[1]
_MANIFESTS = (".claude-plugin/plugin.json", ".codex-plugin/plugin.json")

pytestmark = pytest.mark.skipif(not shutil.which("git"), reason="the version is counted by git")

_GIT_ENV = {
    "GIT_AUTHOR_NAME": "test",
    "GIT_AUTHOR_EMAIL": "test@example.com",
    "GIT_COMMITTER_NAME": "test",
    "GIT_COMMITTER_EMAIL": "test@example.com",
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_NOSYSTEM": "1",
}


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, env={**os.environ, **_GIT_ENV}, check=True, capture_output=True)


def _commit(repo: Path, name: str) -> None:
    (repo / name).write_text(name)
    _git(repo, "add", name)
    _git(repo, "commit", "--no-gpg-sign", "-m", name)


def _write_manifests(repo: Path, claude: str, codex: str) -> None:
    for path, version in zip(_MANIFESTS, (claude, codex)):
        manifest = repo / path
        manifest.parent.mkdir(exist_ok=True)
        manifest.write_text(json.dumps({"name": "hacode-skills", "version": version, "description": "Скилы"}))


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """Toolkit sources with two commits on main and a merged two-commit branch: three first-parent commits."""
    repo = tmp_path / "toolkit"
    (repo / "scripts").mkdir(parents=True)
    shutil.copy(_TOOLKIT / "scripts" / "build-skills.py", repo / "scripts")
    shutil.copytree(_TOOLKIT / "modes", repo / "modes")
    _write_manifests(repo, "2.1.0", "2.1.0")
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "add", ".")
    _git(repo, "commit", "--no-gpg-sign", "-m", "sources")
    _commit(repo, "second")
    _git(repo, "checkout", "-q", "-b", "feature")
    _commit(repo, "feature-one")
    _commit(repo, "feature-two")
    _git(repo, "checkout", "-q", "main")
    _git(repo, "merge", "--no-ff", "--no-gpg-sign", "-m", "merge feature", "feature")
    return repo


def _build(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["python3", str(repo / "scripts" / "build-skills.py"), *args], capture_output=True, text=True
    )


def _versions(repo: Path) -> list[str]:
    return [json.loads((repo / path).read_text(encoding="utf-8"))["version"] for path in _MANIFESTS]


def test_stamp_counts_first_parent_commits_into_both_manifests(repo: Path) -> None:
    result = _build(repo, "--stamp-version")

    assert result.returncode == 0, result.stderr
    assert _versions(repo) == ["2.1.3", "2.1.3"]
    claude = json.loads((repo / _MANIFESTS[0]).read_text(encoding="utf-8"))
    assert claude["description"] == "Скилы"


def test_plain_build_leaves_manifests_alone(repo: Path) -> None:
    result = _build(repo)

    assert result.returncode == 0, result.stderr
    assert _versions(repo) == ["2.1.0", "2.1.0"]


def test_shallow_clone_is_refused(repo: Path, tmp_path: Path) -> None:
    shallow = tmp_path / "shallow"
    _git(tmp_path, "clone", "-q", "--depth", "1", f"file://{repo}", str(shallow))

    result = _build(shallow, "--stamp-version")

    assert result.returncode != 0
    assert "shallow" in result.stderr
    assert _versions(shallow) == ["2.1.0", "2.1.0"]


def test_manifests_with_different_minor_are_refused(repo: Path) -> None:
    _write_manifests(repo, "2.1.0", "2.2.0")

    result = _build(repo, "--stamp-version")

    assert result.returncode != 0
    assert _versions(repo) == ["2.1.0", "2.2.0"]
