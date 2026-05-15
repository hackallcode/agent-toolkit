#!/usr/bin/env python3
"""Собрать skills/** из modes/**.

Скилл должен быть самодостаточным: агент читает его файлы по соседству с `SKILL.md`, а не по пути
в корень плагина, который в момент чтения неизвестен. Поэтому тело режима становится самим `SKILL.md`,
а `common.md` копируется рядом.

Источник правды - `modes/**`. `skills/**` в основной ветке не хранится: CI собирает его и публикует
в ветку `dist`, откуда плагин ставится по URL репозитория.

    scripts/build-skills.py                   собрать
    scripts/build-skills.py --stamp-version   собрать и проставить версию сборки в манифесты плагина
    scripts/build-skills.py --clean           удалить собранное
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MODES = ROOT / "modes"
SKILLS = ROOT / "skills"
MANIFESTS = (ROOT / ".claude-plugin" / "plugin.json", ROOT / ".codex-plugin" / "plugin.json")

# Описание видно в списке скилов, поэтому оно живёт здесь, а не в самом режиме: в `modes/<mode>.md`
# заголовка такого рода нет, а выдумывать его из первой строки - гадание.
DESCRIPTIONS = {
    "build": "Режим BUILD - разработка фич, исправление багов и комментариев в MR. Режим по умолчанию для задач реализации.",
    "check": "Режим CHECK - аудит репозитория на соответствие docs, context и общим правилам: поиск несоответствий и багов без их исправления.",
    "ui": "Режим UI - редизайн экрана, который выглядит сгенерированным: аудит признаков слопа, направление от продукта и проходы по слоям интерфейса.",
    "docs": "Режим DOCS - изменение и дополнение требований в docs/**: описание поведения продукта без привязки к коду.",
    "review": "Режим REVIEW - ревью кода в merge request сабагентом на каждый MR, с публикацией замечаний тредами в MR.",
    "cleanup": "Режим CLEANUP - удаление мусора после ребейзов, разработки и правок по комментариям в MR, без изменения поведения.",
}

GENERATED = "<!-- Собрано из modes/{mode}.md командой `make skills/build`. Не править вручную. -->"

PRELUDE = """> **Как читать пути ниже.** `common.md` лежит рядом с этим файлом - прочитай его первым, он
> обязателен во всех режимах. Ссылка вида `modes/<режим>.md` означает соседний скилл с тем же именем.
> `scripts/…` и `AGENTS.md` лежат в корне плагина, на два уровня выше каталога этого файла.
> Если репозиторий подключил инструкции сабмодулем, работаем по сабмодулю, а не по скиллу.
"""


def skill_text(mode: str) -> str:
    body = (MODES / f"{mode}.md").read_text(encoding="utf-8")
    # JSON string is a valid YAML scalar; a plain one breaks on ": " inside the description.
    description = json.dumps(DESCRIPTIONS[mode], ensure_ascii=False)
    head = f"---\nname: {mode}\ndescription: {description}\n---\n\n"
    return head + GENERATED.format(mode=mode) + "\n\n" + PRELUDE + "\n" + body


def files() -> dict[Path, str]:
    common = (MODES / "common.md").read_text(encoding="utf-8")
    out: dict[Path, str] = {}
    for mode in DESCRIPTIONS:
        out[SKILLS / mode / "SKILL.md"] = skill_text(mode)
        out[SKILLS / mode / "common.md"] = common
    return out


def build_version() -> str:
    """`<major>.<minor>` from the manifests plus the first-parent commit count of HEAD as patch.

    Both CLIs update a plugin only when its version changes, so every build needs its own number,
    and the same revision must always get the same one.
    """
    bases = set()
    for manifest in MANIFESTS:
        version = json.loads(manifest.read_text(encoding="utf-8"))["version"]
        match = re.fullmatch(r"(\d+)\.(\d+)\.\d+", version)
        if match is None:
            raise SystemExit(f"{manifest.relative_to(ROOT)}: version {version!r} is not <major>.<minor>.<patch>")
        bases.add(f"{match[1]}.{match[2]}")
    if len(bases) != 1:
        raise SystemExit(f"plugin manifests disagree on <major>.<minor>: {sorted(bases)}")
    # A shallow clone counts only the fetched commits, and the version would go back.
    if _git("rev-parse", "--is-shallow-repository") != "false":
        raise SystemExit("shallow clone: the commit count is truncated, fetch the full history")
    return f"{bases.pop()}.{_git('rev-list', '--count', '--first-parent', 'HEAD')}"


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, check=True, capture_output=True, text=True).stdout.strip()


def stamp_version(version: str) -> None:
    for manifest in MANIFESTS:
        data = json.loads(manifest.read_text(encoding="utf-8"))
        data["version"] = version
        manifest.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    if "--clean" in sys.argv[1:]:
        shutil.rmtree(SKILLS, ignore_errors=True)
        print("skills/ удалён")
        return 0
    version = build_version() if "--stamp-version" in sys.argv[1:] else None
    # Каталог пересобирается целиком: переименованный или удалённый режим не должен пережить сборку
    # как файл-сирота, который плагин всё ещё покажет скиллом.
    shutil.rmtree(SKILLS, ignore_errors=True)
    for path, text in files().items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    print(f"собрано скилов: {len(DESCRIPTIONS)}")
    if version is not None:
        stamp_version(version)
        print(f"версия плагина: {version}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
