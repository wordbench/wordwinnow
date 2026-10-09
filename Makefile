.DEFAULT_GOAL := help

# NOTE:
# Every third-party tool runs through the project's own locked environment.
#
# A bare `ruff` or `pytest` resolves to whatever the shell happens to offer, and a run against a global Ruff and ty
# below the floors this manifest declares reports green while proving nothing.
#
# `--locked` also fails when `uv.lock` no longer describes the manifest, so the gate cannot pass on a lock that is out
# of date.
RUN := uv run --locked

# NOTE:
# The checkers run through `$(RUN)` for the interpreter, not for a package.
#
# They import nothing outside the standard library, but they do need the language version the manifest requires, and a
# bare `python3` can be older than that.
CHECK := $(RUN) python3 tools/check_conventions.py

CHECK_COMMIT := $(RUN) python3 tools/check_commit_message.py

# NOTE:
# The Markdown the checker reads: everything a person reads, in reading order, except `data/reference/ATTRIBUTION.md`,
# which reproduces licensors' own wording.
#
# `NOTICE` is prose too, but it has no extension, so the checker would read it as Python; it is reviewed by hand.
PROSE := README.md docs data/corpus/README.md

# NOTE:
# The rest of what the checker reads: the notebook, cell by cell, and the record of downloaded artifacts with the
# files that could download one, which §6 of the standard reaches.
NOTEBOOKS := notebooks

SUPPLY_CHAIN := SHA256SUMS Makefile Dockerfile docker-compose.yml .github/workflows

# NOTE:
# Where the standard's own checkout lives, for the optional drift check.
#
# Make takes `HOME` from the environment, so the default names the same place for every user without naming a user,
# and `CONVENTIONS=path` points the check at any other checkout.
CONVENTIONS ?= $(HOME)/Projects/engineering-conventions

# NOTE:
# The commit of the standard the vendored files were copied from, the one its v1.1.0 tag names.
#
# A commit rather than the tag, because a tag can be moved and a commit cannot: the drift check compares the vendored
# files with exactly these bytes, whatever the checkout or its tags have moved on to.
CONVENTIONS_REF := a37a6fed29e4f98f1a963db8ab769cde952a6c33

# WARN:
# The secret scanner is the one gate tool the locked environment cannot hold: gitleaks is a Go program with no Python
# distribution, so it is installed beside uv, from its release archive or a package manager, and found on `PATH`.
#
# The version is held here, and in `ci.yml`, because another release brings another default rule set, and the same
# history could then pass on one machine and fail on another.
GITLEAKS ?= gitleaks

GITLEAKS_VERSION := 8.30.1

GITLEAKS_RELEASE := https://github.com/gitleaks/gitleaks/releases/tag/v$(GITLEAKS_VERSION)

.PHONY: help setup nltk-data model format format-check lint typecheck conventions test test-integration secrets \
	verify check-commits drift hooks build up up-full down demo

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  %-18s %s\n", $$1, $$2}'

setup: ## Create the environment this gate runs in
	uv sync --locked

nltk-data: ## Download the NLTK corpora the linguistic adapters need
	$(RUN) wordwinnow doctor --install-nltk-data

model: ## Train the shipped level model from the reference lists
	$(RUN) wordwinnow ml train

format: ## Apply formatting
	$(RUN) ruff format .

format-check: ## Check formatting without changing files
	$(RUN) ruff format --check .

lint: ## Run the linter
	$(RUN) ruff check .

typecheck: ## Run the type checker
	$(RUN) ty check

conventions: ## Run the vendored checker, positive control first
	$(CHECK) --self-test
	$(CHECK) $(PROSE) $(NOTEBOOKS) wordwinnow tests alembic/env.py $(SUPPLY_CHAIN)

test: ## Run the tests that need no backing service
	$(RUN) pytest -m "not integration and not live"

test-integration: ## Run the tests that need the backing services (`make up` first)
	$(RUN) pytest -m integration

# NOTE:
# The scan reads what Git holds, every commit and the staged changes, and never the working tree: an ignored `.env` is
# where a developer may legitimately keep the key, and only what Git holds can be pushed.
#
# Every finding is redacted, so the scan cannot print the secret it reports.
#
# The positive control runs first: one planted key, in no file, that this project's own rule must report, so a rule
# that stopped matching, or a configuration gitleaks stopped reading, fails here instead of passing as a clean scan.
secrets: ## Scan every commit and the staged changes for leaked secrets, positive control first
	@$(GITLEAKS) version 2> /dev/null | grep -qx '$(GITLEAKS_VERSION)' \
		|| { echo "  gitleaks $(GITLEAKS_VERSION) is needed on PATH: $(GITLEAKS_RELEASE)"; exit 1; }
	@printf 'WORDWINNOW_NYT_API_KEY=%s\n' "$$(printf 'positive-control-%s' 0000000000)" \
		| $(GITLEAKS) stdin --config .gitleaks.toml --no-banner --no-color --redact --verbose 2>&1 \
		| grep -q 'wordwinnow-nyt-api-key' \
		|| { echo "  FAIL: the secret scan did not report a planted key"; exit 1; }
	@echo "  ok    the secret scan reports a planted key"
	@if ! git rev-parse --is-inside-work-tree > /dev/null 2>&1; then \
		echo "  no Git repository yet: no commit and nothing staged to scan"; \
	else \
		if git rev-parse --verify --quiet HEAD > /dev/null; then \
			$(GITLEAKS) git --redact --no-banner --log-opts=--all . || exit 1; \
		fi; \
		$(GITLEAKS) git --staged --redact --no-banner . || exit 1; \
	fi

verify: format-check lint typecheck conventions test secrets ## Everything the gate can prove, in one shot

check-commits: ## Check the messages of the commits not yet on origin/main
	@for commit in $$(git rev-list origin/main..HEAD 2>/dev/null || git rev-list HEAD); do \
		git log -1 --pretty=%B $$commit | $(CHECK_COMMIT) - || exit 1; \
	done

drift: ## Prove the vendored files match the standard at its pinned commit (CONVENTIONS=path)
	@git -C "$(CONVENTIONS)" rev-parse --verify --quiet "$(CONVENTIONS_REF)^{commit}" > /dev/null 2>&1 \
		|| { echo "  no checkout of the standard holding $(CONVENTIONS_REF) at $(CONVENTIONS); pass CONVENTIONS=path"; \
		exit 1; }
	@for pair in vendor/tools/check_conventions.py:tools/check_conventions.py \
		vendor/tools/check_commit_message.py:tools/check_commit_message.py vendor/taplo/taplo.toml:.taplo.toml; do \
		git -C "$(CONVENTIONS)" show "$(CONVENTIONS_REF):$${pair%%:*}" | diff -u - "$${pair#*:}" || exit 1; \
	done
	@echo "  the vendored files match $(CONVENTIONS) at $(CONVENTIONS_REF)"

hooks: ## Point this clone at the repository's Git hooks
	git config core.hooksPath .githooks

build: ## Build the wheel and the source distribution into dist/
	uv build

up: ## Start the backing services (postgres, redis, kafka, clickhouse)
	docker compose up -d --wait postgres redis kafka clickhouse

up-full: ## Start everything, including the application services and observability
	docker compose --profile full up -d --build --wait

down: ## Stop and remove every container and volume
	docker compose --profile full down -v

demo: ## Analyze the sample story locally, without the dictionary
	$(RUN) wordwinnow analyze data/corpus/a-scandal-in-bohemia.txt --local --level A2 --no-dictionary
