"""
orchestrator/workers/telegram_bot.py — Command Receiver (Polling Worker).
Version v1.0: HITL return connector.

Hardware analogy: This is the 'Remote Control Signal Receiver'. It listens
for Telegram button pulses and translates them into HTTP commands for the
orchestrator bus.
"""

import asyncio
import logging

import httpx

from orchestrator.core.config import get_orchestrator_settings

logger = logging.getLogger("telegram_bot")
logging.basicConfig(level=logging.INFO)


async def run_telegram_bot():
    """
    Listen Loop: Continuously polls getUpdates.
    """
    settings = get_orchestrator_settings()
    token = (
        settings.telegram_bot_token.get_secret_value()
        if hasattr(settings.telegram_bot_token, "get_secret_value")
        else settings.telegram_bot_token
    )

    if not token:
        logger.error("❌ [Bot_Fault] Missing TOKEN in EEPROM. Aborting.")
        return

    # Actuator base endpoint (Orchestrator on Docker's internal network)
    ORCHESTRATOR_URL = "http://orchestrator:8001/approve"

    offset = 0
    poll_url = f"https://api.telegram.org/bot{token}/getUpdates"
    answer_url = f"https://api.telegram.org/bot{token}/answerCallbackQuery"
    edit_url = f"https://api.telegram.org/bot{token}/editMessageText"

    logger.info("📡 Bot Listener Activated. Listening for partner signals...")

    async with httpx.AsyncClient(timeout=30.0) as client:
        while True:
            try:
                # 📡 Long Polling
                resp = await client.get(poll_url, params={"offset": offset, "timeout": 20})
                if resp.status_code != 200:
                    await asyncio.sleep(5)
                    continue

                updates = resp.json().get("result", [])
                if updates:
                    logger.info(f"📥 Received {len(updates)} Telegram updates.")

                for update in updates:
                    offset = update["update_id"] + 1
                    logger.debug(f"🔍 Processing Update ID: {update['update_id']}")

                    # 🕹️ Process button clicks (Callback Queries)
                    if "callback_query" in update:
                        cb = update["callback_query"]
                        data = cb.get("data", "NO_DATA")
                        logger.info(f"🕹️ [HITL_EVENT] Callback detected: {data}")

                        chat_id = cb["message"]["chat"]["id"]
                        msg_id = cb["message"]["message_id"]

                        logger.info(f"🕹️ Command signal detected: {data}")

                        # 1. Order parsing
                        try:
                            action, thread_id = data.split("_", 1)
                            decision = "BUY" if action == "approve" else "REJECT"

                            # 2. Injection into the Orchestrator (HTTP POST)
                            target = f"{ORCHESTRATOR_URL}/{thread_id}?decision={decision}"
                            logger.info(f"🚀 [COMMAND] Injecting Override: {target}")

                            r_inj = await client.post(target)
                            logger.info(
                                f"📡 [COMMAND_ACK] Orchestrator responded: {r_inj.status_code}"
                            )
                            if r_inj.status_code != 200:
                                logger.error(
                                    f"❌ [COMMAND_FAULT] Orchestrator rejected the order: {r_inj.text}"
                                )

                            # 3. ACK to Telegram (Removes the button's wait spinner)
                            await client.post(
                                answer_url,
                                json={
                                    "callback_query_id": cb["id"],
                                    "text": f"✅ Order: {decision} sent to bus.",
                                },
                            )

                            # 4. Visual Feedback (Plain text to avoid Markdown escaping failures)
                            new_text = f"⚙️ COMMAND RECEIVED\nID: {thread_id}\nAction: {decision}\n\nThe orchestrator is resuming the sequence..."
                            await client.post(
                                edit_url,
                                json={"chat_id": chat_id, "message_id": msg_id, "text": new_text},
                            )

                        except Exception as e:
                            logger.error(f"💥 [Cmd_Parsing_Error] Could not process {data}: {e}")

            except Exception as e:
                logger.error(f"🚨 [Polling_Fault] Error in listening bus: {e}")
                await asyncio.sleep(5)


if __name__ == "__main__":
    asyncio.run(run_telegram_bot())
