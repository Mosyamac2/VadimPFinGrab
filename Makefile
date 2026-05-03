PYTHON ?= python3
VENV   ?= .venv
PIP    := $(VENV)/bin/pip
PY     := $(VENV)/bin/python

.PHONY: install test lint typecheck clean slo-smoke

install:
	@if [ ! -x "$(PY)" ]; then $(PYTHON) -m venv $(VENV); fi
	$(PIP) install --upgrade pip
	$(PIP) install -e ".[dev]"

test:
	$(PY) -m pytest

lint:
	$(PY) -m ruff check src tests

typecheck:
	$(PY) -m mypy src

clean:
	rm -rf build dist *.egg-info
	rm -rf .pytest_cache .mypy_cache .ruff_cache
	find . -type d -name __pycache__ -prune -exec rm -rf {} +

# Patch 46: production health-check. Run on the live VPS once a day:
#   make slo-smoke
# Verifies that evolution_ticks exists, MEMORY.md parses, canary
# baseline is recent, and settings.evolve.json sandbox is intact.
slo-smoke:
	$(PY) -m pytest tests/evolve/test_slo_smoke.py -q
