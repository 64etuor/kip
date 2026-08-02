.PHONY: bootstrap up down migrate doctor test verify api worker contracts backup fetch-corpus evaluate

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

fetch-corpus:
	./scripts/fetch_public_corpus.py

evaluate:
	./scripts/kip evaluate run --dataset evaluation/golden/public-government.yaml --variants lexical,vector,hybrid,reranked --output-dir evaluation/reports/public-government
