# Operations Runbook

Staged startup procedure for `docker compose up`. Bringing every service up at once overwhelms the local LLM and the free-tier data APIs on cold start, so services are started in waves, waiting for each stage to stabilize before starting the next.

## Phase 0: Discharge the Capacitors (SRAM Purge)

Before anything else, clear out leftovers from previous sessions so `scout_seen.db` doesn't ignore fresh news at market close.

```powershell
# 1. Full power cut
docker compose down

# 2. Physical purge of records (non-volatile memory)
rm -Force ./data/telemetry/telemetry.db* -ErrorAction SilentlyContinue
rm -Force ./data/scout/scout_seen.db* -ErrorAction SilentlyContinue

# 3. Reset the inference engine (free VRAM)
Stop-Process -Name "ollama*" -Force -ErrorAction SilentlyContinue
```

## Phase 1: Main Bus Ignition (Core Handshake)

Estimated wait time: 15 seconds.

Bring up the logical "backplane" first. Redis and the Orchestrator need to be ready to receive the first pulses.

```powershell
docker compose up -d --build orchestrator redis sigint-dashboard position-monitor telegram-bot
```

> The Orchestrator must initialize its SQLite tables and establish the "keep-alive" with Ollama before it can accept traffic. Inject signals too early and you'll get a 502 Bad Gateway.

## Phase 2: Staged Antenna Connection (Sensor Inrush)

Apply a progressive duty cycle so the Ingress Controller isn't overwhelmed on startup.

### Step 2.1: The High-Fidelity Radar (Options and Insiders)

Wait time: 30 seconds.

```powershell
docker compose up -d options-scout insider-scout
```

Detects whether the "whales" are positioning for the close or after-hours. The wait accounts for `options-scout` running a heavy watchlist scan on startup.

### Step 2.2: The Macro Seismograph (FRED and Short Interest)

Wait time: 15 seconds.

```powershell
docker compose up -d fred-scout short-interest-scout
```

Syncs the current VIX and short-squeeze levels. Low CPU load.

### Step 2.3: The Broadband Receiver (RSS)

Final step — no further wait needed.

```powershell
docker compose up -d rss-scout
```

Captures the latest breaking news flow. Started last because it injects the most "noise" relative to signal.

### Step 2.4: Feedback-Loop Workers (Earnings)

```powershell
docker compose up -d earnings-scout
```

## Phase 3: Status Verification (BIST)

Once everything is up, certify the bus is in flight state:

```powershell
Invoke-RestMethod -Uri "http://localhost:8001/inspect" | ConvertTo-Json
```

Look for:

- `"engine_status": "CRUISING"`
- `"waiting_in_queue": 0`
