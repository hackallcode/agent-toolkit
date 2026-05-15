"""The frontmatter of every built `SKILL.md` is YAML that the plugin loaders can read.

A description is free text and may contain `: `, which ends a plain YAML scalar,
so a skill whose description has it would not load.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

_TOOLKIT = Path(__file__).resolve().parents[1]


def test_every_mode_builds_a_skill_with_readable_frontmatter(tmp_path: Path) -> None:
    (tmp_path / "scripts").mkdir()
    shutil.copy(_TOOLKIT / "scripts" / "build-skills.py", tmp_path / "scripts")
    shutil.copytree(_TOOLKIT / "modes", tmp_path / "modes")
    subprocess.run(["python3", str(tmp_path / "scripts" / "build-skills.py")], check=True, capture_output=True)

    skills = sorted((tmp_path / "skills").glob("*/SKILL.md"))
    assert {path.parent.name for path in skills} == {path.stem for path in (_TOOLKIT / "modes").glob("*.md")} - {"common"}
    for path in skills:
        frontmatter = yaml.safe_load(path.read_text(encoding="utf-8").split("---\n")[1])
        assert frontmatter["name"] == path.parent.name
        assert frontmatter["description"].startswith(f"Режим {path.parent.name.upper()} - ")
