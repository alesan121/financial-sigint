import asyncio
import time

import httpx

WEBHOOK_URL = "http://localhost:8001/trigger"

# Generate 20 signals of different types
SCENARIOS = [
    "Tech breakthrough in AI by {ticker}",
    "Financial earnings miss for {ticker}",
    "Sudden CEO resignation at {ticker}",
    "Market bubble fears growing for {ticker}",
]
TICKERS = ["AAPL", "NVDA", "TSLA", "MSFT", "GOOGL"]


async def send_burst(client, i):
    ticker = TICKERS[i % len(TICKERS)]
    scenario = SCENARIOS[i % len(SCENARIOS)].format(ticker=ticker)
    payload = {"news_text": scenario, "source": f"Heavy-Stress-Test-{i}"}
    t0 = time.perf_counter()
    try:
        resp = await client.post(WEBHOOK_URL, json=payload, timeout=5)
        elapsed = (time.perf_counter() - t0) * 1000
        print(
            f"[Burst {i:02d}] Ticker: {ticker} | Status: {resp.status_code} | Latency: {elapsed:.2f}ms"
        )
        return resp.status_code
    except Exception as e:
        print(f"[Burst {i:02d}] ❌ FAIL: {e}")
        return 500


async def main():
    print("🚀 Starting a barrage of 20 parallel signals...")
    async with httpx.AsyncClient() as client:
        tasks = [send_burst(client, i) for i in range(20)]
        results = await asyncio.gather(*tasks)

    success = results.count(200)
    failed = len(results) - success
    print(f"\n📊 RESULTS: {success} OK, {failed} FAILED")


if __name__ == "__main__":
    asyncio.run(main())
