"""
orchestrator/workers/notifier.py — Front Panel & Alarm Control Unit.
DEFCON 1 Version: EEPROM Integration and SRE Telemetry.

Hardware analogy: This is the rack's LED panel and buzzer. Its
mission is to translate digital bus events into visual signals
(Telegram) for the human operator.
"""

import logging
import os
import asyncio
import httpx
from datetime import datetime, timezone

from orchestrator.core.config import get_orchestrator_settings

# 📡 SRE PATCH: Alignment with the Global Telemetry Bus
logger = logging.getLogger("agentops.telemetry")

# --- REGISTROS DE DIRECCIONES (API Endpoints) ---
_SEND_URL = "https://api.telegram.org/bot{token}/sendMessage"
_EDIT_URL = "https://api.telegram.org/bot{token}/editMessageText"
_ANSWER_URL = "https://api.telegram.org/bot{token}/answerCallbackQuery"

async def send_trade_alert(
    message: str, 
    parse_mode: str = "Markdown", 
    disable_notification: bool = False
) -> bool:
    """
    Alert Transmitter: Sends an information pulse to the user's terminal.
    """
    settings = get_orchestrator_settings()
    token = settings.telegram_bot_token.get_secret_value() if hasattr(settings.telegram_bot_token, "get_secret_value") else settings.telegram_bot_token
    chat_id = settings.telegram_chat_id

    if not token or not chat_id:
        logger.warning("⚠️ [Notifier] Radio disabled: Missing Telegram registers in EEPROM.")
        return False

    url = _SEND_URL.format(token=token)
    payload = {
        "chat_id": chat_id,
        "text": message,
        "parse_mode": parse_mode,
        "disable_notification": disable_notification,
    }

    try:
        # 🔧 SRE FIX: Timeout ajustado para absorber jitter de red en K8s
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(url, json=payload)
            if resp.status_code == 200:
                logger.debug(f"✅ [Radio] Signal emitted successfully.")
                return True
            else:
                logger.error(f"💥 [Radio_Fault] Telegram responded with code {resp.status_code}.")
                return False
    except Exception as e:
        logger.error(f"🚨 [Radio_Critical] Physical failure in the transmitter: {e}")
        return False

async def send_system_alert(message: str) -> bool:
    """
    Survival Alert: Notifies critical failures (Kill-Switch, Drawdown).
    """
    urgent_msg = f"🚨 *SIGINT SYSTEM FAULT* 🚨\n\n{message}"
    return await send_trade_alert(urgent_msg, disable_notification=False)

def send_trade_alert_sync(message: str) -> None:
    """
    Synchronous Bypass: For contexts where the asyncio loop is not available.
    """
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            loop.create_task(send_trade_alert(message))
        else:
            loop.run_until_complete(send_trade_alert(message))
    except Exception as e:
        logger.warning(f"⚠️ [Notifier] Failure in synchronous bypass: {e}")

# 🔧 HITL REDESIGN: Control Bus Decoupling
async def notify_hitl_request(message: str, thread_id: str) -> bool:
    """
    IRQ Emitter: Sends a human interrupt request with action buttons.

    Note: In v4.0, this function does NOT block. It sends the signal and finishes.
    Resumption is managed via the FSM Gateway (/approve).
    """
    settings = get_orchestrator_settings()
    token = settings.telegram_bot_token.get_secret_value() if hasattr(settings.telegram_bot_token, "get_secret_value") else settings.telegram_bot_token
    chat_id = settings.telegram_chat_id
    
    if not token or not chat_id: return False

    url = _SEND_URL.format(token=token)

    # 🕹️ INTERACTIVE UI: Buttons pointing to our FSM Gateway
    # In production, these buttons can call a FastAPI webhook
    payload = {
        "chat_id": chat_id,
        "text": f"⚠️ HITL INTERRUPT DETECTED ⚠️\nID: {thread_id}\n\n{message}",
        "reply_markup": {
            "inline_keyboard": [
                [
                    {"text": "✅ APPROVE", "callback_data": f"approve_{thread_id}"},
                    {"text": "❌ REJECT", "callback_data": f"reject_{thread_id}"},
                ]
            ]
        }
    }

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(url, json=payload)
            return resp.status_code == 200
    except Exception as e:
        logger.error(f"🚨 [HITL_Notify_Fault] Error emitting IRQ: {e}")
        return False
