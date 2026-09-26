PYTHON ?= python3
REPO   ?= .
SINCE  ?= 90d
OUT    ?= gitstat-report.html
CACHE  := $(or $(XDG_CACHE_HOME),$(HOME)/.cache)/gitstat

RUN := PYTHONPATH=$(CURDIR) $(PYTHON) -m gitstat --path $(REPO) --since $(SINCE)

.DEFAULT_GOAL := help
.PHONY: help install uninstall reinstall test check report html csv clean clean-cache

help: ## Show available targets
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*## "} {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'
	@echo ""
	@echo "  Variables: REPO=path/to/clone SINCE=30d OUT=file.html AUTHOR=login"

install: ## Install the gitstat command with pipx (editable)
	pipx install --editable .

uninstall: ## Remove the pipx install
	pipx uninstall gitstat

reinstall: ## Reinstall (after changing pyproject.toml)
	pipx install --editable --force .

test: ## Run the unit tests
	$(PYTHON) -m unittest discover -s tests

check: test ## Byte-compile every module and run the tests
	$(PYTHON) -m compileall -q gitstat tests

report: ## Terminal report for REPO (add AUTHOR=login for detail)
	$(RUN) $(if $(AUTHOR),--author $(AUTHOR))

html: ## HTML dashboard for REPO, written to OUT
	$(RUN) --format html -o $(OUT) $(if $(AUTHOR),--author $(AUTHOR))

csv: ## CSV export for REPO, written to gitstat-report.csv
	$(RUN) --format csv -o gitstat-report.csv

clean: ## Remove build artefacts and bytecode
	rm -rf build dist *.egg-info
	find . -name __pycache__ -type d -prune -exec rm -rf {} +

clean-cache: ## Delete the cached GitHub PR data
	rm -rf "$(CACHE)"
