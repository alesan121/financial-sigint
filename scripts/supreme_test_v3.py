"""
scripts/supreme_test_v3.py - SIGINT Final Certification v6.0 (Golden Bull)
Mission: Validate bus continuity using a high-fidelity bullish signal.
Strategy: Inject a pristine "Growth" news item to eliminate the judge's semantic veto.
"""
import asyncio
import httpx
import sqlite3
import os
import sys
import time

# --- CONNECTION PINS ---
GATEWAY_URL = "http://localhost:8001/trigger"
TELEMETRY_DB = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "data", "telemetry", "telemetry.db"))

def get_last_db_id() -> int:
    """Reads the current pointer from EEPROM memory."""
    try:
        conn = sqlite3.connect(TELEMETRY_DB)
        cursor = conn.cursor()
        cursor.execute("SELECT MAX(id) FROM execution_logs")
        row = cursor.fetchone()
        conn.close()
        return row[0] if row and row[0] else 0
    except Exception as e:
        print(f"⚠️ Warning: Could not read the DB (may be the first run): {e}")
        return 0

async def poll_telemetry(last_id: int, ticker_expected: str, timeout: int = 300):
    """Read probe: monitors the output bus in real time."""
    start_time = time.time()
    print(f"   [...] Monitoring Output Bus (Waiting for pulse from {ticker_expected})...", end="")

    while (time.time() - start_time) < timeout:
        if os.path.exists(TELEMETRY_DB):
            try:
                conn = sqlite3.connect(TELEMETRY_DB)
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT * FROM execution_logs WHERE id > ? AND ticker = ? ORDER BY id DESC LIMIT 1",
                    (last_id, ticker_expected)
                )
                row = cursor.fetchone()
                conn.close()

                if row:
                    print(" [🟢 VOLTAGE DETECTED]")
                    return dict(row)
            except sqlite3.OperationalError:
                pass

        await asyncio.sleep(3)
        print(".", end="", flush=True)

    print(" [🔴 TIMEOUT]")
    return None

async def final_cert():
    print(f"\n{'='*80}\n🚀 SIGINT E2E TEST v6.0 | CONTINUITY CERTIFICATION (GOLDEN BULL)\n{'='*80}\n")

    last_db_id = get_last_db_id()

    # 🔌 SEMANTIC WELD: 100% Bullish News
    # Being a growth-and-dividends scenario, we eliminate the Judge's "panic" bias.
    payload = {
        "text": "NVIDIA (NVDA) announces record-breaking Q4 earnings, surpassing all street expectations. The board approves a massive $50B share buyback program and a dividend increase. Growth guidance for AI chips raised by 40%.",
        "source": "SRE-GOLDEN-BULL",
        "ingress_signal": {
            "ticker": "NVDA",
            "sentiment": "bullish",
            "impact_score": 0.95,
            "stoch_confidence": 0.98
        }
    }

    print(f"📡 Injecting Signal -> Source: {payload['source']} | Ticker: NVDA | Sentiment: BULLISH")

    async with httpx.AsyncClient(timeout=60.0) as client:
        print("🚀 Launching certification pulse...")
        try:
            r = await client.post(GATEWAY_URL, json=payload)
            print(f"📡 Gateway Bus Status: HTTP {r.status_code}")

            if r.status_code in (200, 202):
                data = await poll_telemetry(last_db_id, "NVDA")
                if data:
                    action = data.get('action')
                    # An APPROVED, LONG or EXECUTED means the relay closed successfully
                    color = "✅" if action in ["APPROVED", "LONG", "EXECUTED"] else "⚠️"

                    print(f"\n   {color} BLACK BOX RECORD (ID: {data.get('id')}):")
                    print(f"      - Ticker         : {data.get('ticker')}")
                    print(f"      - Final Action   : {action} (Looking for APPROVED)")
                    print(f"      - Kelly / Alloc  : {float(data.get('kelly_fraction',0)*100):.2f}% / ${float(data.get('allocation_usd',0)):,.2f}")
                    print(f"      - RR Ratio       : {data.get('reward_risk_ratio')}")
                    print(f"      - Judge's Reason : {data.get('reason')}")

                    if action in ["APPROVED", "LONG", "EXECUTED"]:
                        print(f"\n🏆 CERTIFICATION COMPLETE: The data bus has full continuity.")
                    else:
                        print(f"\n❌ SIGNAL BLOCKED: Check the Meta-Judge logs for the veto reason.")
            else:
                print(f"❌ INJECTION FAILURE: {r.text}")

        except Exception as e:
            print(f"❌ SHORT CIRCUIT IN THE TEST: {e}")

    print(f"\n{'='*80}\n🏁 SEQUENCE FINISHED.\n{'='*80}\n")

if __name__ == "__main__":
    asyncio.run(final_cert())
