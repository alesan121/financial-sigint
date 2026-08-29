"""
scripts/macro_test_v1.py - Macro Bus Validation v6.1.
Mission: Verify that DIRECT news flows through the Extractor and Technical TA.
"""

import asyncio
import uuid

import httpx

GATEWAY_URL = "http://localhost:8001/trigger"


async def test_macro_signal():
    print("\n" + "=" * 80)
    print("🚀 SIGINT MACRO TEST v1.0 | VALIDATING UNIFIED BUS")
    print("=" * 80 + "\n")

    payload = {
        "text": "FED Chair Powell suggests interest rates may stay higher for longer. Inflation remains sticky.",
        "source": "MACRO-TEST",
        "thread_id": f"MACRO-{uuid.uuid4().hex[:4].upper()}",
    }

    async with httpx.AsyncClient(timeout=180.0) as client:
        print("🧪 TEST: FED Macro Signal -> Expected Proxy: TLT or SPY")
        try:
            resp = await client.post(GATEWAY_URL, json=payload)
            if resp.status_code in (200, 202):
                print("   ✅ Pulse injected. Check the Docker logs to trace the FSM.")
            else:
                print(f"   ❌ [GATEWAY_FAULT] HTTP {resp.status_code}: {resp.text}")
        except Exception as e:
            print(f"   💥 [CONN_FAULT] Error connecting to the Gateway: {e}")

    print("\n" + "=" * 80)
    print("🏁 MACRO TEST FINISHED.")
    print("=" * 80 + "\n")


if __name__ == "__main__":
    asyncio.run(test_macro_signal())
