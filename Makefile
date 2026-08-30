# stapel-vocabularies — contract emission + drift gate (contract-pipeline.md §2-3).
#
# This module emits its OWN contract triad (schema.json + flows.json +
# errors.json) + capabilities.json, from a single-module {vocabularies + core}
# Django instance mounted at the canonical /vocabularies/api/v1/ prefix (see
# _codegen.py / _codegen_settings.py / codegen_urls.py).
#
# PYTHON must have the module + its deps importable (the workspace venv, or a
# CI venv). The authoritative CI gate is tests/test_contract.py (run under
# pytest); these targets are the dev-loop convenience.
PYTHON ?= python3

.PHONY: contract contract-check test lint migration-lint

# Emit the contract triad + capabilities.json + llms.txt (the fifth artifact,
# stapel_tools.llms_txt) into docs/. README.md is the sixth: assembled by
# stapel_tools.readme from docs/readme.md plus everything emitted above —
# edit docs/readme.md, never README.md.
contract:
	$(PYTHON) -m stapel_vocabularies._codegen --out docs
	$(PYTHON) -m stapel_vocabularies._capabilities --out docs
	$(PYTHON) -m stapel_tools.llms_txt . --out docs --budget 4000
	$(PYTHON) -m stapel_tools.readme .

# Drift gate: regenerate into a temp dir and diff against the committed docs/*.
contract-check:
	@tmp=$$(mktemp -d); \
	$(PYTHON) -m stapel_vocabularies._codegen --out "$$tmp" || { rm -rf "$$tmp"; exit 1; }; \
	$(PYTHON) -m stapel_vocabularies._capabilities --out "$$tmp" || { rm -rf "$$tmp"; exit 1; }; \
	$(PYTHON) -m stapel_tools.llms_txt . --out "$$tmp" --budget 4000 || { rm -rf "$$tmp"; exit 1; }; \
	rc=0; \
	for f in schema.json flows.json errors.json capabilities.json llms.txt; do \
		if ! diff -q "docs/$$f" "$$tmp/$$f" >/dev/null 2>&1; then \
			echo "DRIFT: docs/$$f is stale — run 'make contract' and commit it"; \
			diff "docs/$$f" "$$tmp/$$f" | head -20; rc=1; \
		fi; \
	done; \
	rm -rf "$$tmp"; \
	$(PYTHON) -m stapel_tools.readme . --check || rc=1; \
	if [ $$rc -eq 0 ]; then echo "contract-check: docs/{schema,flows,errors,capabilities,llms.txt} + README.md up to date"; fi; \
	exit $$rc

test:
	$(PYTHON) -m pytest tests/ -v

lint:
	ruff check . --select E,F,W --ignore E501

# Expand/contract gate for Django migrations (release-management.md §3).
migration-lint:
	$(PYTHON) -m stapel_tools.migration_lint . --strict
