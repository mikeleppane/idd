.PHONY: help install format fmt format-check lint fix typecheck test cov validate-health check clean ci

# `make` with no target prints the help table.
.DEFAULT_GOAL := help

VENV   := .venv
PY     := $(VENV)/bin/python
PIP    := $(PY) -m pip
RUFF   := $(VENV)/bin/ruff
MYPY   := $(VENV)/bin/mypy
PYTEST := $(VENV)/bin/pytest

# PyPI override — corp default index requires auth; use public PyPI.
PIP_INDEX := --index-url https://pypi.org/simple/

help: ## Show this help (auto-generated from doc-comment annotations)
	@awk 'BEGIN { FS = ":.*##"; printf "Usage: make <target>\n\nTargets:\n" } \
	      /^[a-zA-Z][a-zA-Z0-9_-]*:.*##/ { printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2 }' \
	      $(MAKEFILE_LIST)

install: ## Create venv (if missing) and install forge-tools with dev extras
	@test -d $(VENV) || python3.12 -m venv $(VENV) || python3 -m venv $(VENV)
	@$(PIP) install --upgrade pip $(PIP_INDEX)
	@$(PIP) install -e ".[dev]" $(PIP_INDEX)

format: ## Apply ruff formatter
	@$(RUFF) format tools tests hooks

fmt: format ## Alias for `format`

format-check: ## Verify formatting (no writes); fails if `make format` would change anything
	@$(RUFF) format --check tools tests hooks

lint: ## Run ruff lint (no fixes)
	@$(RUFF) check tools tests hooks

fix: ## Run ruff lint with --fix
	@$(RUFF) check --fix tools tests hooks

typecheck: ## Run mypy strict
	@$(MYPY) tools tests hooks

test: ## Run pytest
	@$(PYTEST) -v

cov: ## Run pytest with coverage
	@$(PYTEST) --cov=tools --cov-report=term-missing

validate-health: ## Run /forge:validate --target health on the current repo
	@$(PY) -m tools.validate --target health

check: format-check lint typecheck test validate-health ## Full local quality gate — run before every commit

ci: check ## Alias for the composite quality gate (mirrors CI)

clean: ## Remove caches (ruff, mypy, pytest, pyc, coverage)
	@rm -rf .ruff_cache .mypy_cache .pytest_cache htmlcov .coverage
	@find . -type d -name __pycache__ -prune -exec rm -rf {} +
	@find . -type d -name "*.egg-info" -prune -exec rm -rf {} +
