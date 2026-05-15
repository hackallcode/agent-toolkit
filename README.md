# agent-toolkit

Инструкции для агентов: общие правила разработки и режимы работы. К стеку не привязаны - описывают метод
работы, а конкретику проекта (команду тестов, раскладку исходников, трекер) агент выясняет по самому
репозиторию.

Подключить их можно двумя способами, и они не конфликтуют:

- **Скиллами** - плагин `hacode-skills` для Claude Code и Codex CLI. Ставится разработчику, а не проекту,
  поэтому работает в любом репозитории, где вы открыли сессию.
- **Сабмодулем** - инструкции становятся частью проекта, их видят все агенты и все разработчики. Так
  подключают командные репозитории.

Если подключено и то и другое, инструкции берутся из сабмодуля: он привязан к ревизии проекта, а скилл -
к той, что стоит у разработчика.

## Содержание

- [Режимы](#режимы)
- [Подключение скиллами](#подключение-скиллами)
  * [Обновление и удаление](#обновление-и-удаление)
- [Подключение сабмодулем](#подключение-сабмодулем)
  * [Обновление](#обновление)
  * [Клонирование проекта с сабмодулем](#клонирование-проекта-с-сабмодулем)
- [Источники](#источники)

## Режимы

Скилл на режим, по имени режима:

| Режим     | Для чего                                                                          |
| --------- | --------------------------------------------------------------------------------- |
| `build`   | разработка фич, исправление багов и комментариев в MR - режим по умолчанию         |
| `check`   | аудит на соответствие требованиям и правилам: поиск несоответствий без исправлений |
| `docs`    | изменение и дополнение требований                                                  |
| `ui`      | редизайн экрана, который выглядит сгенерированным, без изменения функций           |
| `review`  | ревью кода в merge request                                                         |
| `cleanup` | удаление мусора после ребейзов и правок, без изменения поведения                   |

Каждый режим приносит с собой общие правила - они обязательны везде, и агент читает их первыми.

## Подключение скиллами

Ставим из ветки `dist` - в ней лежат собранные скилы. Репозиторий публичный, поэтому адрес везде
HTTPS.

Claude Code:

```
claude plugin marketplace add "https://github.com/hackallcode/agent-toolkit.git#dist"
claude plugin install hacode-skills@agent-toolkit
```

Codex CLI:

```
codex plugin marketplace add https://github.com/hackallcode/agent-toolkit.git --ref dist
codex plugin add hacode-skills@agent-toolkit
```

Проверить: `/skills` в Claude Code, `codex plugin list` в Codex CLI - плагин `hacode-skills@agent-toolkit`
должен быть в статусе `installed`.

Если репозиторий склонирован, то же самое делает `make skills/install` - и по отдельности
`make skills/install/claude`, `make skills/install/codex`.

### Обновление и удаление

```
claude plugin update hacode-skills@agent-toolkit
codex plugin marketplace upgrade agent-toolkit
```

```
claude plugin uninstall hacode-skills@agent-toolkit
claude plugin marketplace remove agent-toolkit

codex plugin remove hacode-skills@agent-toolkit
codex plugin marketplace remove agent-toolkit
```

Из клона: `make skills/update` и `make skills/delete`.

## Подключение сабмодулем

Из корня проекта:

```
git submodule add https://github.com/hackallcode/agent-toolkit.git agents
cp agents/templates/AGENTS.md AGENTS.md
ln -s AGENTS.md CLAUDE.md
git add AGENTS.md CLAUDE.md .gitmodules agents
```

`AGENTS.md` в проекте - короткая точка входа, которая отсылает агента в сабмодуль; весь объём инструкций
живёт в `agents/`. Имя папки сабмодуля можно выбрать своё - тогда поправь путь в скопированном `AGENTS.md`.

Если папок требований и рабочего контекста в проекте ещё нет, заведи их из заготовок:

```
mkdir -p docs context
cp agents/templates/docs/README.md docs/README.md
cp agents/templates/context/README.md context/README.md
cat agents/templates/gitignore >> .gitignore
```

Правило в `.gitignore` работает по принципу «запрещено всё, кроме перечисленного»: из `context/` в git
уезжают только файлы, которые нужны команде и переживают задачу. План активной задачи, заметки об
экспериментах и стендовые секреты остаются локальными.

Что это за заготовки - [`templates/README.md`](templates/README.md).

### Обновление

```
git submodule update --remote agents
git add agents && git commit -m "Update agent instructions"
```

### Клонирование проекта с сабмодулем

```
git clone --recurse-submodules <url проекта>
```

Для уже склонированного проекта: `git submodule update --init --recursive`.

---

Разработка самого тулкита - [`CONTRIBUTING.md`](CONTRIBUTING.md).
