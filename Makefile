# Скилы ставятся из ветки `dist` - это основная ветка плюс собранный skills/**, который публикует CI.
# Собирать руками нужно только при разработке самого тулкита: цель `skills/local`.
REPO_URL := https://github.com/hackallcode/agent-toolkit.git
DIST_BRANCH := dist
REPO_DIR := $(abspath $(dir $(lastword $(MAKEFILE_LIST))))
MARKETPLACE := agent-toolkit
PLUGIN_NAME := hacode-skills
PLUGIN := $(PLUGIN_NAME)@$(MARKETPLACE)

.PHONY: skills skills/build skills/clean skills/local \
	skills/local/claude skills/local/codex \
	skills/install skills/update skills/delete \
	skills/install/claude skills/install/codex \
	skills/update/claude skills/update/codex \
	skills/delete/claude skills/delete/codex

skills: skills/install

skills/install: skills/install/claude skills/install/codex

skills/update: skills/update/claude skills/update/codex

skills/delete: skills/delete/claude skills/delete/codex

skills/install/claude:
	@claude plugin marketplace add "$(REPO_URL)#$(DIST_BRANCH)"
	@claude plugin install $(PLUGIN)

skills/install/codex:
	@codex plugin marketplace add $(REPO_URL) --ref $(DIST_BRANCH)
	@codex plugin add $(PLUGIN)

skills/update/claude:
	@claude plugin marketplace update $(MARKETPLACE)
	@claude plugin update $(PLUGIN)

skills/update/codex:
	@codex plugin marketplace upgrade $(MARKETPLACE)

skills/delete/claude:
	@claude plugin uninstall $(PLUGIN)
	@claude plugin marketplace remove $(MARKETPLACE)

skills/delete/codex:
	@codex plugin remove $(PLUGIN)
	@codex plugin marketplace remove $(MARKETPLACE)

# --- разработка тулкита ---

skills/build:
	@$(REPO_DIR)/scripts/build-skills.py

skills/clean:
	@$(REPO_DIR)/scripts/build-skills.py --clean

# Проверить правки режимов до пуша: marketplace берётся из этого клона со свежесобранными скилами.
# Имя marketplace одно и то же, поэтому прежняя установка сначала снимается.
skills/local: skills/local/claude skills/local/codex

skills/local/claude: skills/build
	-@claude plugin uninstall $(PLUGIN) >/dev/null 2>&1
	-@claude plugin marketplace remove $(MARKETPLACE) >/dev/null 2>&1
	@claude plugin marketplace add "$(REPO_DIR)"
	@claude plugin install $(PLUGIN)

skills/local/codex: skills/build
	-@codex plugin remove $(PLUGIN) >/dev/null 2>&1
	-@codex plugin marketplace remove $(MARKETPLACE) >/dev/null 2>&1
	@codex plugin marketplace add "$(REPO_DIR)"
	@codex plugin add $(PLUGIN)
