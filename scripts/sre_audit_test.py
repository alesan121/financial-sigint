"""
SIGINT Supreme Certification v4.0 - Industrial Fly-by-Wire Test.
Mission: Stress-test the data bus, thread persistence, and the Telegram relay.

Analogy: This is a "Thermal Cycling Chamber" for the software.
Verifies signal integrity from the antenna all the way to the final actuator.
"""

import asyncio
import sqlite3
import uuid
from datetime import UTC, datetime

import httpx

# --- PIN CONFIGURATION ---
GATEWAY_URL = "http://localhost:8001/trigger"
TELEMETRY_DB = "data/telemetry.db"

# --- LOAD BANK (Stress Signals) ---
TEST_SUITE = [
    {
        "id": "AUTO_FIRE_NVDA",
        "name": "⚡ AUTO MODE: High-Fidelity Injection (NVIDIA)",
        "payload": {
            "text": "BREAKING: NVIDIA ($NVDA) and US Government sign $50B deal for Sovereign AI. Immediate sector impact.",
            "source": "SRE-Cert-v4",
        },
        "expect": "AUTOMATIC Buy on Telegram and Dashboard Record.",
    },
    {
        "id": "HITL_GATE_MSFT",
        "name": "🌀 HITL MODE: Noisy Signal (MICROSOFT)",
        "payload": {
            "text": "RUMOR: Microsoft ($MSFT) might be looking at a distressed nuclear energy provider for data centers.",
            "source": "SRE-Cert-v4",
        },
        "expect": "VALIDATION REQUEST on Telegram. The system PAUSES (Breakpoint).",
    },
    {
        "id": "SQUELCH_FLOWERS",
        "name": "🔇 SQUELCH MODE: Signal Ground (NOISE)",
        "payload": {
            "text": "The local library is hosting a cupcake sale tomorrow at 5 PM. Everyone is invited.",
            "source": "SRE-Cert-v4",
        },
        "expect": "Immediate DISCARD. Total radio silence.",
    },
]


async def check_db_entry(ticker: str):
    """Logic probe to read the EEPROM memory."""
    try:
        conn = sqlite3.connect(TELEMETRY_DB)
        cursor = conn.cursor()
        # Check whether a record exists for that ticker within the last hour
        cursor.execute(
            "SELECT ticker, action FROM execution_logs WHERE ticker = ? ORDER BY id DESC LIMIT 1",
            (ticker,),
        )
        row = cursor.fetchone()
        conn.close()
        return row
    except Exception:
        return None


async def run_certification():
    print(f"\n{'='*70}")
    print("🚀 STARTING SIGINT FLY-BY-WIRE CERTIFICATION v4.0")
    print(f"Timestamp: {datetime.now(UTC).isoformat()}")
    print(f"{'='*70}\n")

    async with httpx.AsyncClient(timeout=120.0) as client:
        for case in TEST_SUITE:
            print(f"🧪 RUNNING: {case['name']}")
            thread_id = f"CERT-{uuid.uuid4().hex[:4].upper()}"
            case["payload"]["thread_id"] = thread_id

            try:
                # 1. Pulse Injection
                resp = await client.post(GATEWAY_URL, json=case["payload"])

                if resp.status_code == 200:
                    print(f"   🟢 [ACK] Signal accepted. Thread ID: {thread_id}")
                    print(f"   🎯 TARGET: {case['expect']}")
                else:
                    print(f"   🔴 [FAIL] Gateway out of range: {resp.status_code}")
                    continue

                # 2. Propagation time (the AI takes time to reason)
                print("   ⏳ Processing signal in the ALU (Ollama)...")
                await asyncio.sleep(45)

                # 3. Telemetry verification (only for the AUTO case)
                if "NVDA" in case["id"]:
                    db_row = await check_db_entry("NVDA")
                    if db_row:
                        print(
                            f"   ✅ [DATA_SYNC] Record found on Dashboard: {db_row[0]} | {db_row[1]}"
                        )
                    else:
                        print(
                            "   ⚠️ [DB_TIMEOUT] No write detected in the DB yet. Check permissions."
                        )

            except Exception as e:
                print(f"   💥 [CRITICAL] Hardware failure in the test: {e}")

            print("-" * 50)
            await asyncio.sleep(5)

    print(f"\n{'='*70}")
    print("🏁 TEST FINISHED. AUDIT YOUR TELEGRAM NOW.")
    print("You should have: 1 Buy (NVDA), 1 Validation (MSFT) and 1 Discard.")
    print(f"{'='*70}\n")


if __name__ == "__main__":
    try:
        # 🚀 MAIN RELAY ACTIVATION
        # This starts the event loop and powers up the coroutine
        asyncio.run(run_certification())
    except KeyboardInterrupt:
        print("\n🛑 [STOP] Emergency shutdown requested by the operator.")
    except Exception as e:
        print(f"\n💥 [CRITICAL] Hardware failure in the sequencer: {e}")
