# Generated contracts

Run `./scripts/generate-contracts.sh` after changing public Pydantic models or the FastAPI surface. CI checks that generated files are current.

Do not invoke `scripts/generate_contracts.py` directly. The wrapper sources `scripts/common.sh`, which loads `.env` and selects the configuration and project interpreter; a bare `python` call runs against a different interpreter and configuration and can write contracts that do not match the deployment.

Never hand-edit the generated files in this directory.
