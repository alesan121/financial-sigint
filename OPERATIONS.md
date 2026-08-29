🛠️ PHASE 0: Discharge the Capacitors (SRAM Purge)

Before anything else, we clear out leftovers from previous sessions so `scout_seen.db` doesn't ignore fresh news at market close.
PowerShell

# 1. Full power cut
docker compose down

# 2. Physical purge of records (non-volatile memory)
rm -Force ./data/telemetry/telemetry.db* -ErrorAction SilentlyContinue
rm -Force ./data/scout/scout_seen.db* -ErrorAction SilentlyContinue

# 3. Reset the inference engine (free VRAM)
Stop-Process -Name "ollama*" -Force -ErrorAction SilentlyContinue

⚡ PHASE 1: Main Bus Ignition (Core Handshake)

Estimated wait time: 15 seconds.

We bring up the logical "backplane." Redis and the Orchestrator need to be ready to receive the first pulses.
PowerShell

docker compose up -d --build orchestrator redis sigint-dashboard position-monitor sigint-telegram-bot

    Why wait? The Orchestrator must initialize its SQLite tables and establish the "Keep-Alive" with Ollama. If we inject signals too early, we'll get a 502 Bad Gateway.

📡 PHASE 2: Staged Antenna Connection (Sensor Inrush)

Here we apply a progressive duty cycle so we don't overwhelm the Ingress Controller.
Step 2.1: The High-Fidelity Radar (Options and Insiders)

Wait time: 30 seconds.
PowerShell

docker compose up -d options-scout insider-scout

    Mission: Detect whether the "Whales" are positioning for the close or after-hours. We wait 30s because options-scout runs a heavy WATCHLIST scan on startup.

Step 2.2: The Macro Seismograph (FRED and Short Interest)

Wait time: 15 seconds.
PowerShell

docker compose up -d fred-scout short-scout

    Mission: Sync the current VIX and squeeze levels. This is a low-CPU load.

Step 2.3: The Broadband Receiver (RSS)

Wait time: Final step.
PowerShell

docker compose up -d rss-scout

    Mission: Capture the latest breaking news flow. We turn this on last because it injects the most "noise."

🔬 PHASE 3: Status Verification (BIST)

Once everything is up, run this command to certify the bus is in Flight State:
PowerShell

Invoke-RestMethod -Uri "http://localhost:8001/inspect" | ConvertTo-Json

    Look for: "engine_status": "CRUISING".

    Look for: "waiting_in_queue": 0.
