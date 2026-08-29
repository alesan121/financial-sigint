"""
orchestrator/workers/main.py - Interrupt Controller (FSM Gateway).
DEFCON 2 Version: Secure Management and Deep Packet Inspection (DPI) for Squelch.

Analogy: This is the APIC (Advanced Programmable Interrupt Controller). It
receives signals (HTTP POST) and safely queues them onto the Main Bus.
"""

import asyncio
import hashlib
import logging
import sys
import os
import uuid
from datetime import datetime, timezone
from fastapi import FastAPI, BackgroundTasks, HTTPException
import uvicorn
from cachetools import TTLCache

from orchestrator.graph import run_trading_cycle, resume_trading_cycle
from orchestrator.core.observability import lqa, start_telemetry_server

# Instrumentation configuration for dumping to stdout (Docker logs)
logging.basicConfig(level=logging.INFO, stream=sys.stdout, format="%(message)s")
logger = logging.getLogger(__name__)

telemetry_logger = logging.getLogger("agentops.telemetry")
telemetry_logger.setLevel(logging.INFO)
telemetry_logger.addHandler(logging.StreamHandler(sys.stdout))

app = FastAPI(title="Orchestrator FSM Gateway")

# 🛡️ Concurrency Control Valve
fsm_semaphore = asyncio.Semaphore(1)

# 🔌 BLOCKING DIODE (Squelch)
dedup_filter = TTLCache(maxsize=500, ttl=300)

# 🛰️ IN-FLIGHT TASK REGISTRY (SRE Instrument)
active_tasks = set()

async def watchdog_loop():
    """Time Drift Monitor: HITL Safety Watchdog."""
    from orchestrator.graph import fsm_pipeline, resume_trading_cycle
    from orchestrator.core.config import get_orchestrator_settings

    settings = get_orchestrator_settings()
    timeout_mins = int(os.getenv("HITL_TIMEOUT_MINUTES", "5"))

    logger.info(f"⏱️ [Watchdog] HITL Watchdog active (TTL: {timeout_mins} min).")
    
    while True:
        await asyncio.sleep(60)
        try:
            thread_id = "default_bus"
            config = {"configurable": {"thread_id": thread_id}}
            
            state = await fsm_pipeline.aget_state(config)
            if not state or not state.next:
                continue
                
            if "hitl_escalation" in state.next:
                risk_q = state.values.get("risk_quant", {})
                start_ts_str = risk_q.get("hitl_start_ts")
                
                if start_ts_str:
                    start_ts = datetime.fromisoformat(start_ts_str)
                    now = datetime.now(timezone.utc)
                    elapsed = (now - start_ts).total_seconds() / 60
                    
                    if elapsed >= timeout_mins:
                        ticker = state.values.get("ingress_signal", {}).get("ticker", "UNK")
                        logger.warning(f"⏰ [Watchdog] TTL Expired for {ticker} ({thread_id}). Executing AUTO-REJECT.")
                        await resume_trading_cycle(thread_id, human_decision="REJECTED")

                        from orchestrator.workers.notifier import send_trade_alert
                        await send_trade_alert(f"⏰ *HITL EXPIRED* ({timeout_mins}m)\nTicker: {ticker}\nAction: AUTO-DISCARD (Safety First)", parse_mode=None)

        except Exception as e:
            logger.error(f"⚠️ [Watchdog_Fault] Error in the monitoring bus: {e}")

@app.on_event("startup")
async def startup_event():
    lqa.trace("SYSTEM", "BIOS", "⚡ Starting SIGINT v6.3 power-up sequence...")
    from orchestrator.memory.telemetry import init_telemetry_bus
    await init_telemetry_bus()
    start_telemetry_server(9090)
    asyncio.create_task(watchdog_loop())
    lqa.trace("SYSTEM", "BIOS", "✅ All data and metrics buses are operational.")

@app.get("/health")
async def health_check():
    return {"status": "Healthy", "bus_port": 8001}

@app.get("/inspect")
async def inspect_system():
    """
    APIC Probe: Returns the processor's internal state and bus throughput.
    """
    try:
        from orchestrator.memory.telemetry import get_throughput_stats

        # 1. Data Plane (Historical)
        throughput = await get_throughput_stats(hours=4)

        # 2. Control Plane (Real Time)
        # Compute queue depth based on the semaphore and pending tasks
        # Note: fsm_semaphore._value is internal to asyncio, we use it for SRE visibility
        is_locked = fsm_semaphore.locked()
        
        status = {
            "engine_status": "CONGESTIONED" if is_locked else "CRUISING",
            "server_time": datetime.now(timezone.utc).isoformat(),
            "cache_entries_count": len(dedup_filter),
            "realtime_metrics": {
                "waiting_in_queue": max(0, len(active_tasks) - (1 if is_locked else 0)),
                "bus_lock": is_locked
            },
            "historical_throughput_4h": throughput
        }
        return status
    except Exception as e:
        logger.error(f"💥 [INSPECT_FAULT] Error in diagnostic probe: {e}")
        return {"status": "ERROR", "error": str(e)}

async def _resume_signal_bg(thread_id: str, decision: str):
    logger.info(f"🕹️ [Orchest-Worker] Resuming bus {thread_id} with order: {decision}")
    try:
        await resume_trading_cycle(thread_id, decision)
    except Exception as e:
        logger.error(f"💥 [Orchest-Worker] Critical failure in Override: {e}")

@app.post("/approve/{thread_id}")
async def approve_trade(thread_id: str, background_tasks: BackgroundTasks, decision: str = "BUY"):
    background_tasks.add_task(_resume_signal_bg, thread_id, decision)
    return {"status": "Resume_Signal_Sent"}

async def _process_signal_bg(news_text: str, ingress_signal: dict | None, thread_id: str):
    label = str(ingress_signal.get("ticker", "UNKNOWN") if ingress_signal else news_text)[:20]
    task_id = str(uuid.uuid4())
    active_tasks.add(task_id)
    
    async with fsm_semaphore:
        logger.info(f"[{datetime.now().isoformat()}] 📥 [Orchest-Worker][Bus:{thread_id}] Starting FSM cycle: {label}...")
        try:
            await run_trading_cycle(news_text=news_text, initial_signal=ingress_signal, thread_id=thread_id)
            logger.info(f"🏁 [Orchest-Worker][Bus:{thread_id}] FSM Cycle closed successfully.")
        except Exception as e:
            logger.error(f"💥 [Orchest-Worker][Bus:{thread_id}] Kernel Panic: {e}", exc_info=True)
        finally:
            active_tasks.discard(task_id)

@app.post("/trigger")
async def trigger_fsm(payload: dict, background_tasks: BackgroundTasks):
    news_text = payload.get("text") or payload.get("news_text", "")
    ingress_signal = payload.get("ingress_signal") or {}
    thread_id = payload.get("thread_id", "default_bus")
    
    if "source" in payload and "source" not in ingress_signal:
        ingress_signal["source"] = payload["source"]
    
    ticker = ingress_signal.get("ticker") or payload.get("ticker", "UNK")
    source = ingress_signal.get("source", payload.get("source", "UNK"))

    # 🛡️ SRE FIX: Deep Packet Inspection (DPI) to resolve Hash Collision
    if ticker in ("UNKNOWN", "UNK") and news_text:
        # Generate a digital signature using the first 200 characters of the text
        content_hash = hashlib.md5(news_text[:200].encode('utf-8')).hexdigest()[:8]
        cache_key = f"UNK-{source}-{content_hash}"
    else:
        # If it's a structured signal (Options, Insider), the Ticker is enough
        cache_key = f"{ticker}-{source}"

    if cache_key in dedup_filter:
        logger.info(f"🚫 [Gateway] Squelch: Duplicate pulse detected for {cache_key}. Ignoring.")
        return {"status": "Ignored", "reason": "Duplicate pulse in cooldown"}

    dedup_filter[cache_key] = True

    if not news_text and not ingress_signal:
        raise HTTPException(status_code=400, detail="Missing input signal")

    lqa.trace(thread_id, "INGRESS", f"📡 Signal detected and queued (Source: {ingress_signal.get('source', 'UNKNOWN')})")
    background_tasks.add_task(_process_signal_bg, news_text, ingress_signal, thread_id)
    return {"status": "Queued", "thread_id": thread_id}

if __name__ == "__main__":
    logger.info("⚡ Powering up Orchestrator FSM Gateway (Port 8001)...")
    uvicorn.run(app, host="0.0.0.0", port=8001)
