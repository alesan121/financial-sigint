"""
SRE Diagnostic Tool: Telegram Output Relay Test.
This component checks whether the output bus to the Telegram API
has electrical continuity and the tokens are valid.
"""
import asyncio
import os
import httpx
from dotenv import load_dotenv

load_dotenv()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

async def test_relay():
    print(f"📡 [DEBUG] Checking Telegram output relay...")
    print(f"🔑 Token detected: {TELEGRAM_TOKEN[:5]}...{TELEGRAM_TOKEN[-5:] if TELEGRAM_TOKEN else 'None'}")

    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": "🚨 SIGINT SRE ALERT: Output relay verified. The radio works.",
        "parse_mode": "Markdown"
    }

    async with httpx.AsyncClient() as client:
        try:
            resp = await client.post(url, json=payload)
            if resp.status_code == 200:
                print("✅ [SUCCESS] The message has left the chassis and should be on your phone.")
            else:
                print(f"❌ [FAIL] Telegram API error: {resp.status_code} - {resp.text}")
        except Exception as e:
            print(f"💥 [CRITICAL] Short circuit in the HTTP client: {e}")

if __name__ == "__main__":
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("❌ [ERROR] Missing environment variables (TOKEN/CHAT_ID). Check your .env")
    else:
        asyncio.run(test_relay())
