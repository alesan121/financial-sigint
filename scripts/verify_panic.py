import asyncio

import httpx


async def main():
    url = "http://localhost:8001/trigger"
    payload = {
        "news_text": "URGENT: The Federal Reserve unexpectedly hikes interest rates by 100bps amidst banking collapse fears. Markets are in freefall, but Apple shows strong cash reserves.",
        "source": "Manual-Verification-Panic",
    }
    async with httpx.AsyncClient() as client:
        resp = await client.post(url, json=payload, timeout=5)
        print(f"Status: {resp.status_code}")
        print(f"Response: {resp.json()}")


if __name__ == "__main__":
    asyncio.run(main())
