# Financial SIGINT

`Financial SIGINT` is an air-gapped, local-first pipeline that turns financial news, insider filings, options flow and macro data into trading signals — analyzed entirely by a locally-hosted LLM (Ollama), with zero calls to external inference APIs.

## Table of Contents

- [Financial SIGINT](#financial-sigint)
  - [Table of Contents](#table-of-contents)
  - [🏗️ System Architecture](#️-system-architecture)
    - [📡 Ingress (ADC) — Signal Acquisition](#-ingress-adc--signal-acquisition)
    - [🧠 Orchestrator — Decision Engine](#-orchestrator--decision-engine)
  - [Prerequisites](#prerequisites)
  - [Initialize the `dev` Environment](#initialize-the-dev-environment)
    - [🧪 Code Quality Checks](#-code-quality-checks)
  - [🐳 Docker Deployment](#-docker-deployment)
    - [Prepare your Environment](#prepare-your-environment)
    - [Staged Startup](#staged-startup)
  - [☸️ Kubernetes Deployment (Helm)](#️-kubernetes-deployment-helm)
    - [Chart Structure](#chart-structure)
    - [External Prerequisites](#external-prerequisites)
    - [Secrets](#secrets)
    - [Install / Upgrade](#install--upgrade)
    - [Known Limitation: Shared SQLite Volume](#known-limitation-shared-sqlite-volume)
  - [🛡️ Software Craftsmanship \& Values](#️-software-craftsmanship--values)

## 🏗️ System Architecture

The system is split into two microservices connected by a signal bus, following an Analog-to-Digital Converter metaphor: raw, noisy market data comes in, clean structured signals come out.

### 📡 Ingress (ADC) — Signal Acquisition

Six independent scout workers poll public data sources on their own schedule and convert unstructured input into structured JSON signals via the local LLM:

| Scout | Signal source | Focus |
| :--- | :--- | :--- |
| **RSS Scout** | Financial news feeds | Real-time headline sentiment |
| **Insider Scout** | SEC Form 4 filings (Finnhub) | High-conviction insider buys |
| **Earnings Scout** | Earnings calendar | Upcoming earnings surprises |
| **FRED Scout** | Federal Reserve Economic Data | Macro regime (rates, VIX) |
| **Short Interest Scout** | FINRA short interest | Short-squeeze candidates |
| **Options Scout** | Options flow | Unusual volume / smart money |

### 🧠 Orchestrator — Decision Engine

A LangGraph finite-state machine consumes Ingress signals and runs a full trading decision cycle:

- **Multi-Agent Debate**: Bear and Bull agents argue both sides of every signal before a **Meta-Judge** rules on conviction.
- **Risk Engine**: portfolio-level guards — max open positions, sector concentration cap, drawdown kill-switch, correlation cap against existing positions.
- **Execution & Position Monitor**: places and tracks paper/live trades via Alpaca, continuously polling open positions.
- **Human-In-The-Loop (HITL)**: high-impact orders can require interactive approval via the Telegram bot before executing.
- **Adaptive PoP**: a P-controller that dynamically tunes the minimum probability-of-profit threshold based on realized win rate.
- **Backtesting Engine**: replay historical signals against the same decision graph before risking capital.
- **Dashboard**: real-time visual monitoring of the engine state, queue depth and signal flow.

## Prerequisites

This project has been tested with the following environment:

| **Tool**       | **Version** |
| --------------- | ----------- |
| Python          | `>=3.12`    |
| Poetry          | `2.3.2`     |
| Docker          | `>=28.5.2`  |
| Docker Compose  | `>=v2.40.3` |
| Ollama          | latest      |

## Initialize the `dev` Environment

Copy the template file and populate it with your own credentials:

```bash
cp .env.example .env
```

Open the generated `.env` file and set your own values. Below is a reference of the main configurable variables (see `.env.example` for the full, commented list):

| Variable | Default | Description |
| :--- | :--- | :--- |
| `OLLAMA_BASE_URL` | `http://host.docker.internal:11434` | Local Ollama endpoint used for all inference. |
| `OLLAMA_MODEL` | `llama3.2` | Model used to convert raw text into structured signals. |
| `ALPACA_API_KEY` / `ALPACA_SECRET_KEY` | _(required)_ | Alpaca Markets paper-trading credentials. |
| `ALPACA_PAPER` | `true` | **Never** flip to `false` without an explicit, reviewed decision. |
| `FINNHUB_API_KEY` | _(required)_ | Free-tier key for the Insider Scout (SEC Form 4 data). |
| `FRED_API_KEY` | _(optional)_ | Federal Reserve Economic Data key for the macro scout. |
| `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` | _(required)_ | Feedback-loop channel: alerts and HITL order approval. |
| `RISK_MAX_POSITIONS` | `5` | Portfolio guard: max simultaneous open positions. |
| `RISK_MAX_DRAWDOWN_PCT` | `0.10` | Kill-switch: halts execution past this drawdown. |
| `HITL_ENABLED` | `true` | Require interactive Telegram approval before executing orders. |

Now, install Poetry and the project dependencies:

```bash
pip install poetry
poetry config virtualenvs.in-project true
poetry install --with dev
```

Install the `pre-commit` configuration (Conventional Commits are enforced via Commitizen on every commit and push):

```bash
poetry run pre-commit install --hook-type commit-msg --hook-type pre-push
```

Pull the local model before first use:

```bash
ollama pull llama3.2
```

🚀 You're now ready to start developing features in this project!

### 🧪 Code Quality Checks

Before any contribution, ensure the code is clean:

```bash
poetry run black --check .
poetry run ruff check .
poetry run pflake8 .
poetry run bandit -c pyproject.toml -r .
poetry run pytest tests/ -v
```

Run with coverage, matching the gate enforced in CI:

```bash
poetry run pytest --cov=src --cov-report=term-missing --cov-fail-under=8
```

Or simply:

```bash
make lint
make test
```

## 🐳 Docker Deployment

### Prepare your Environment

> [!IMPORTANT]
> Don't forget to prepare your `.env` file following the steps above before starting any container.

### Staged Startup

Services are brought up in stages to avoid overwhelming the local LLM and the free-tier data APIs on cold start (see [OPERATIONS.md](OPERATIONS.md) for the fully detailed, staged runbook):

```bash
# 1. Core bus: orchestrator, cache and observability
docker compose up -d --build orchestrator redis sigint-dashboard position-monitor telegram-bot

# 2. High-fidelity scouts (heaviest watchlist scans)
docker compose up -d options-scout insider-scout

# 3. Macro scouts (lighter load)
docker compose up -d fred-scout short-interest-scout

# 4. Real-time news feed (last, it's the noisiest)
docker compose up -d rss-scout
```

Check the engine status once everything is up:

```bash
curl http://localhost:8001/inspect
```

Look for `"engine_status": "CRUISING"` and `"waiting_in_queue": 0`.

To stop everything (named volumes with persistent data are preserved):

```bash
docker compose down
```

## ☸️ Kubernetes Deployment (Helm)

The Helm chart lives in `charts/financial-sigint/`. It deploys **only the two network-facing services** — the orchestrator gateway (API) and the Streamlit dashboard.

> [!NOTE]
> The seven scout workers, the position monitor, and the Telegram bot are intentionally **not** part of this chart. They are long-running background processes that write to a shared SQLite file (`telemetry.db`) via bind mounts — an access pattern that does not tolerate multi-pod concurrency safely. They stay on Docker Compose (see above) until telemetry storage moves to a real database; porting them to Kubernetes as-is would just be cargo-culting a pattern the app isn't built for.

### Chart Structure

```bash
charts/financial-sigint/
├── Chart.yaml                  # Metadata and versioning
├── values.yaml                 # All tunable parameters
└── templates/
    ├── _helpers.tpl            # Shared template helpers
    ├── configmap.yaml          # Non-sensitive env vars (mirrors .env.example)
    ├── externalsecret.yaml     # ESO ExternalSecret (enabled via values)
    ├── pvc.yaml                # Shared telemetry.db volume
    ├── deployment.yaml         # Gateway + Dashboard Deployments
    ├── service.yaml            # Gateway + Dashboard Services (ClusterIP)
    └── ingress.yaml            # NGINX Ingress (host-based routing)
```

### External Prerequisites

The chart does not install these — provision them separately and point `values.yaml`'s `config` block at them:

- **Redis** — semantic cache for the ingress ADC.
- **A reachable Ollama endpoint** — this app calls a self-hosted LLM, not a hosted API; there is no Kubernetes manifest for Ollama itself in this repo.

### Secrets

Never commit plaintext secrets. Either populate a Kubernetes Secret named `financial-sigint-secrets` out of band, or set `externalSecret.enabled: true` in `values.yaml` to sync `ALPACA_API_KEY`, `ALPACA_SECRET_KEY`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `FINNHUB_API_KEY`, and `FRED_API_KEY` from Vault (or another backend) via the [External Secrets Operator](https://external-secrets.io/).

### Install / Upgrade

```bash
helm upgrade --install financial-sigint ./charts/financial-sigint \
  --namespace financial-sigint --create-namespace \
  --set image.tag=v0.1.0 \
  --set config.OLLAMA_BASE_URL="http://ollama.default.svc.cluster.local:11434" \
  --set config.REDIS_URI="redis://redis.default.svc.cluster.local:6379/0"
```

### Known Limitation: Shared SQLite Volume

The gateway (writer) and dashboard (reader) share one PVC for `telemetry.db`. That volume defaults to `ReadWriteOnce`, which only guarantees both pods can mount it if the cluster schedules them on the same node. If your storage class doesn't support `ReadWriteMany`, keep `gateway.replicaCount` and `dashboard.replicaCount` at `1`.

## 🛡️ Software Craftsmanship & Values

- **100% Local Inference:** all LLM calls run through Ollama — no data or prompts ever leave the machine.
- **Adversarial Signal Validation:** every trading idea is challenged by an opposing agent before it reaches the risk engine.
- **Defense in Depth on Capital:** portfolio-level guards (position cap, sector cap, drawdown kill-switch, correlation cap) sit between every signal and real execution.
- **Human-In-The-Loop by Default:** no order executes without an interactive Telegram approval unless explicitly configured otherwise.
- **Well-Crafted Software:** typed, linted, and security-scanned (Bandit, Trivy, OSV, Gitleaks) on every pull request. Unit tests cover the safety-critical DSP logic (Kelly sizing, PoP fusion, portfolio guards); integration and stress tests exist for the FSM but are a work in progress as the graph evolves — see [tests/](tests/).
