.PHONY: install run demo test lint evaluate browser
install:
	uv sync --frozen --extra dev --extra bilibili
run:
	uv run codiscover-video serve
demo:
	CDV_PROVIDER=demo CDV_LLM=demo CDV_DATA_DIR=data/demo uv run codiscover-video serve
lint:
	uv run ruff check src tests scripts
	uv run ruff format --check src tests scripts
test:
	uv run pytest -q
	node --test integrations/dsh/tests/*.test.mjs
evaluate:
	uv run python scripts/evaluate.py --output data/evaluation.json
browser:
	npm run test:browser
