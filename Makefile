.PHONY: bootstrap up down migrate doctor test coverage lint typecheck audit verify api worker contracts backup restore-drill release verify-release fetch-corpus evaluate

RELEASE_OUTPUT ?= dist/kip-$(shell tr -d '[:space:]' < VERSION)

bootstrap:
	./scripts/bootstrap.sh

up:
	./scripts/dev-up.sh

down:
	./scripts/dev-down.sh

migrate:
	./scripts/migrate.sh

doctor:
	./scripts/doctor.sh

test:
	./scripts/test.sh

coverage:
	uv run pytest --cov --cov-report=term --cov-report=xml

lint:
	uv run ruff check src tests scripts

typecheck:
	uv run mypy src/kip

audit:
	uv run pip-audit --requirement requirements/runtime.txt --no-deps --disable-pip

verify:
	./scripts/verify.sh

api:
	./scripts/api.sh

worker:
	./scripts/worker.sh

contracts:
	./scripts/generate-contracts.sh

backup:
	./scripts/backup.sh

restore-drill:
	@test -n "$(BACKUP_DIR)" || (printf '%s\n' 'usage: make restore-drill BACKUP_DIR=/absolute/backup/path' >&2; exit 2)
	./scripts/restore-drill.sh "$(BACKUP_DIR)"

release:
	./scripts/release-bundle.sh "$(RELEASE_OUTPUT)"

verify-release:
	@test -n "$(BUNDLE)" || (printf '%s\n' 'usage: make verify-release BUNDLE=/path/to/bundle-or-archive' >&2; exit 2)
	./scripts/verify-release.sh "$(BUNDLE)"

fetch-corpus:
	./scripts/fetch_public_corpus.py

evaluate:
	./scripts/kip evaluate run --dataset evaluation/golden/public-government.yaml --variants lexical,vector,hybrid,reranked --output-dir evaluation/reports/public-government
