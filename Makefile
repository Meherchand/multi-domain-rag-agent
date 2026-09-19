.PHONY: help install install-dev demo index api ui streamlit mcp test lint fmt scan-secrets docker-build docker-up docker-down clean

PYTHON ?= python3
VENV   := .venv
BIN    := $(VENV)/bin

help: ## Show this help
	@echo "Multi-Domain RAG Agent"
	@echo ""
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

$(BIN)/python:
	$(PYTHON) -m venv $(VENV)
	$(BIN)/pip install --upgrade pip

install: $(BIN)/python ## Install runtime dependencies
	$(BIN)/pip install -r requirements.txt

install-dev: $(BIN)/python ## Install runtime + development dependencies
	$(BIN)/pip install -r requirements-dev.txt

demo: install ## Index the demo corpus and ask a question, fully offline
	DEMO_MODE=true INIT_MODE=index $(BIN)/python cli.py ask \
		"How long is inventory reserved for an unpaid order?" --domains order_service

index: ## Index every knowledge domain in the corpus directory
	$(BIN)/python cli.py index

api: ## Run the HTTP API on :8000
	$(BIN)/uvicorn api_app:app --host 0.0.0.0 --port 8000 --reload

ui: ## Run the Chainlit UI on :8502 (needs the API)
	$(BIN)/chainlit run chainlit_app.py --host 0.0.0.0 --port 8502

streamlit: ## Run the Streamlit UI on :8501 (needs the API)
	$(BIN)/streamlit run streamlit_app.py

mcp: ## Run the MCP server over stdio
	$(BIN)/python mcp_app.py

test: ## Run the test suite (no external services required)
	$(BIN)/python -m pytest -q

lint: ## Check formatting and lint rules
	$(BIN)/python -m ruff check .
	$(BIN)/python -m ruff format --check .

fmt: ## Apply formatting and safe lint fixes
	$(BIN)/python -m ruff check --fix .
	$(BIN)/python -m ruff format .

scan-secrets: ## Fail if anything secret-shaped is committed
	@./scripts/scan_secrets.sh

docker-build: ## Build the container image
	docker build -t multi-domain-rag-agent:local .

docker-up: ## Start the API and UI with docker compose
	docker compose up -d api ui

docker-down: ## Stop the compose stack
	docker compose down

clean: ## Remove caches and the virtualenv
	rm -rf $(VENV) .pytest_cache .ruff_cache .mypy_cache htmlcov .coverage
	find . -type d -name __pycache__ -prune -exec rm -rf {} +

.DEFAULT_GOAL := help
