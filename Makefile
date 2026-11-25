PY := engine/.venv/bin/python
RUFF := uvx ruff@0.17.6

.PHONY: bootstrap check test lint verify-install release-dry

bootstrap:
	@./scripts/bootstrap.sh

check: lint test

lint:
	@$(RUFF) check --select E9,F --output-format concise engine scripts

test:
	@$(PY) -m pytest engine/tests -q

verify-install:
	@./scripts/verify-install.sh

release-dry:
	@$(PY) scripts/release.py --next
