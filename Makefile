# =============================================================================
# Makefile - Development cycle automation
#
# Hardware analogy: this Makefile is the "lab control panel."
# Each 'make <target>' is like pressing a button on the oscilloscope or the
# spectrum analyzer: it runs one concrete, predictable action.
#
# Usage: make <target>  |  Example: make install
# =============================================================================

.PHONY: help install pre-commit-install pull-model lint format test test-cov run docker-build docker-up docker-down clean

# Default configuration
PYTHON        = python
POETRY        = poetry
MODEL         ?= llama3.2:latest
OLLAMA_URL    ?= http://localhost:11434
GATEWAY_PORT  = 8001

# Default target: shows help
.DEFAULT_GOAL := help

help: ## Shows this help
	@echo ""
	@echo "Financial SIGINT - Ingress (ADC) - Available commands:"
	@echo "============================================================"
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-20s\033[0m %s\n", $$1, $$2}'
	@echo ""

# =============================================================================
# SETUP
# =============================================================================

install: ## Installs all dependencies (prod + dev) with Poetry into .venv
	@echo ">>> Configuring Poetry to create .venv inside the project directory..."
	$(POETRY) config virtualenvs.in-project true
	@echo ">>> Installing dependencies..."
	$(POETRY) install --with dev
	@echo ">>> Dependencies installed into .venv/"

pre-commit-install: ## Installs the pre-commit hooks (commit-msg + pre-push)
	$(POETRY) run pre-commit install --hook-type commit-msg --hook-type pre-push

pull-model: ## [IMPORTANT] Downloads the Ollama model before first use
	@echo ">>> Checking that Ollama is running at $(OLLAMA_URL)..."
	@curl -sf $(OLLAMA_URL)/api/tags > /dev/null || \
		(echo "ERROR: Ollama is not running. Start Ollama first." && exit 1)
	@echo ">>> Downloading model '$(MODEL)' from Ollama (this may take several minutes)..."
	ollama pull $(MODEL)
	@echo ">>> Model '$(MODEL)' ready to use."

# =============================================================================
# CODE QUALITY (mirrors the checks run in .github/workflows/python-check.yaml)
# =============================================================================

format: ## Formats the code with Black
	$(POETRY) run black .

lint: ## Runs all linters: ruff, black (check), flake8, bandit
	@echo ">>> [1/4] Ruff (linting + imports)..."
	$(POETRY) run ruff check .
	@echo ">>> [2/4] Black (format)..."
	$(POETRY) run black --check .
	@echo ">>> [3/4] Flake8 (style)..."
	$(POETRY) run pflake8 .
	@echo ">>> [4/4] Bandit (security)..."
	$(POETRY) run bandit -c pyproject.toml -r .
	@echo ">>> Quality pipeline completed."

# =============================================================================
# TESTS
# =============================================================================

test: ## Runs the tests with pytest (does NOT require Ollama running)
	@echo ">>> Running tests with pytest..."
	$(POETRY) run pytest tests/ -v --tb=short

test-cov: ## Runs tests with a coverage report, matching the gate enforced in CI
	$(POETRY) run pytest tests/ -v --tb=short --cov=src --cov-report=term-missing --cov-fail-under=8

# =============================================================================
# LOCAL EXECUTION (without Docker)
# =============================================================================

run: ## Starts the orchestrator gateway locally with hot-reload (requires .env and native Ollama)
	@echo ">>> Make sure .env is configured and Ollama is running on Windows."
	@echo ">>> Tip: in your local .env, use OLLAMA_BASE_URL=http://localhost:11434"
	$(POETRY) run fastapi dev src/orchestrator/workers/main.py --port $(GATEWAY_PORT)

# =============================================================================
# DOCKER
# =============================================================================

docker-build: ## Builds the production Docker image (multi-stage)
	@echo ">>> Building image 'financial-sigint:latest'..."
	docker build -t financial-sigint:latest --target runtime .

docker-up: ## Starts the full stack (see the staged startup order in README.md)
	@echo ">>> Requires Ollama running natively on the host and a populated .env file."
	docker compose up --build

docker-up-detached: ## Starts the stack in detached (background) mode
	docker compose up --build -d

docker-down: ## Stops and removes the stack's containers
	docker compose down

docker-logs: ## Shows the logs of all services in the stack
	docker compose logs -f

# =============================================================================
# CLEANUP
# =============================================================================

clean: ## Removes build artifacts: __pycache__, .pytest_cache, .ruff_cache
	@echo ">>> Cleaning build artifacts and caches..."
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".pytest_cache" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".ruff_cache" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name "htmlcov" -exec rm -rf {} + 2>/dev/null || true
	find . -name ".coverage" -delete 2>/dev/null || true
	@echo ">>> Cleanup completed."
