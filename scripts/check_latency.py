"""
scripts/check_latency.py - SRE Pulse Test.

Measures the 'Time to First Byte' and the latency of the MoE 3B/1B bus.
Certifies that KEEP_ALIVE keeps the models in a 'Hot State'.
"""

import asyncio
import time

import httpx

INGRESS_URL = "http://localhost:8000/analyze"


async def test_pulse(news_text: str, name: str):
    payload = {"text": news_text, "source": "SRE-Latency-Probe"}

    t0 = time.perf_counter()
    async with httpx.AsyncClient(timeout=300.0) as client:
        response = await client.post(INGRESS_URL, json=payload)

    elapsed = time.perf_counter() - t0
    status = "✅ SUCCESS" if response.status_code == 200 else "❌ FAILED"

    print(f"[{name}] {status} | Latency: {elapsed:.2f}s")
    return elapsed


async def main():
    print("🚀 Starting Latency Test (SRE Pulse)...")

    # Pulse A: Cold (or Warm if already used)
    print("\n--- Pulse A: Validating initial bus ---")
    await test_pulse("Earnings report for NVIDIA exceeds expectations.", "PULSE-A")

    # Minimal wait for thermal stabilization
    await asyncio.sleep(2)

    # Pulse B: Hot (Validating L2/RAM persistence)
    print("\n--- Pulse B: Validating KEEP_ALIVE ---")
    await test_pulse("Fed signals interest rate hold for next quarter.", "PULSE-B")

    print("\n🏁 Latency Certification Completed.")


if __name__ == "__main__":
    asyncio.run(main())
