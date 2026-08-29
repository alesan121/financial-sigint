"""
SIGINT Supreme Diagnostic v3.0 - Fly-by-Wire Certification.
Mission: Inject signals with different confidence "voltages" to
verify automatic routing, manual routing (HITL), and discard.
"""
import httpx
import asyncio
import uuid

GATEWAY_URL = "http://localhost:8001/trigger"

# --- SIGNAL BANK (Test Signals) ---

TEST_SIGNALS = [
    {
        "name": "⚡ PULSE_ALPHA (Auto-Trade)",
        "text": "URGENT: NVIDIA ($NVDA) confirms 100% revenue growth beat and new 10-year AI partnership with US Gov. Sector-wide buy signal.",
        "expected": "AUTO-EXECUTION (High Confidence > 0.85)"
    },
    {
        "name": "🌀 PULSE_GRAY (HITL Validation)",
        "text": "RUMOR: Microsoft ($MSFT) might be exploring a smaller specialized battery company in Europe, but details are vague and unconfirmed.",
        "expected": "HITL ESCALATION (Ambiguous 0.5 - 0.85)"
    },
    {
        "name": "🔇 THERMAL_NOISE (Discard)",
        "text": "The local park in Seattle announced a new flower exhibition starting next Monday. Admission is free for all residents.",
        "expected": "DISCARD (Low Confidence < 0.5)"
    }
]

async def fire_signal(signal: dict):
    print(f"\n--- 📡 Injecting: {signal['name']} ---")
    print(f"📝 Text: {signal['text'][:100]}...")

    # Generate a unique thread_id for this data channel
    thread_id = f"CERT-{uuid.uuid4().hex[:6].upper()}"

    payload = {
        "text": signal["text"],
        "source": "Supreme-Diagnostic-v3",
        "thread_id": thread_id
    }

    async with httpx.AsyncClient() as client:
        try:
            resp = await client.post(GATEWAY_URL, json=payload, timeout=5.0)
            if resp.status_code == 200:
                print(f"✅ [ACK] Signal accepted by the bus. Thread: {thread_id}")
                print(f"🎯 Expected result: {signal['expected']}")
            else:
                print(f"❌ [FAIL] Gateway rejected the signal: {resp.status_code}")
        except Exception as e:
            print(f"💥 [CRITICAL] Connection failure with the Gateway: {e}")

async def run_diagnostic():
    print("🚀 [MISSION CONTROL] Starting SIGINT Certification v3.0...")
    print("🧪 Verifying Fly-by-Wire Logic and Persistence.")

    for sig in TEST_SIGNALS:
        await fire_signal(sig)
        print("⏳ Waiting 10s for ALU cooldown...")
        await asyncio.sleep(10) # Pause to avoid saturating Ollama

    print("\n🏁 [TEST END] Now audit your Telegram and Dashboard logs.")

if __name__ == "__main__":
    asyncio.run(run_diagnostic())
