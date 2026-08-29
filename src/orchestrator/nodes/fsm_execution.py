"""
orchestrator/nodes/fsm_execution.py - FSM Node: Final Broker Actuator.

Analogy: This is the definitive 'Power Contactor'.
It only receives signals from channel Q (RiskQuant) that have passed
the `routing_flag == "APPROVED"` flag. It transforms the data into a Payload
executable by Alpaca and then logs it to Telemetry.
"""

import logging
import time
import os
import asyncio
import httpx
from datetime import datetime, timezone

from alpaca.trading.client import TradingClient
from alpaca.trading.enums import OrderClass, OrderSide, TimeInForce
from alpaca.trading.requests import MarketOrderRequest, TakeProfitRequest, StopLossRequest, TrailingStopOrderRequest, LimitOrderRequest

from orchestrator.state import TradingState, ExecutionPayload, RiskQuant
from orchestrator.core.config import get_orchestrator_settings

logger = logging.getLogger(__name__)

async def send_telegram_alert(text: str):
    """Radio Transmitter: Uses the reference voltages from the EEPROM."""
    cfg = get_orchestrator_settings()
    token = cfg.telegram_bot_token.get_secret_value() if hasattr(cfg.telegram_bot_token, "get_secret_value") else cfg.telegram_bot_token
    chat_id = cfg.telegram_chat_id

    if not token or not chat_id:
        return # Silence if no peripheral is connected

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {"chat_id": chat_id, "text": text}

    async with httpx.AsyncClient() as client:
        try:
            await client.post(url, json=payload, timeout=5.0)
        except Exception as e:
            logger.error(f"📡 [Radio_Error] Propagation failure: {e}")

async def node_execution_agent(state: TradingState) -> dict:
    ts = datetime.now(timezone.utc).isoformat()
    signal = state.get("ingress_signal", {})
    risk_q: RiskQuant = state.get("risk_quant", {})
    ticker = signal.get("ticker", "ERROR")
    alloc = risk_q.get("allocation_usd", 0.0)

    # 📡 [SRE] IMMEDIATE TELEMETRY INJECTION
    # We send the Telegram notice BEFORE attempting the trade to ensure visibility
    routing_flag = risk_q.get("routing_flag", "APPROVED")
    header = "🚀 AUTO-TRADE EXECUTED" if routing_flag == "APPROVED" else "✅ MANUAL-TRADE EXECUTED (Authorized)"
    
    alert_msg = (
        f"{header}\n"
        f"Ticker: {ticker}\n"
        f"Sentiment: {signal.get('sentiment')}\n"
        f"Alloc: ${alloc:.2f}\n"
        f"Reason: {signal.get('reasoning')[:200]}..."
    )
    await send_telegram_alert(alert_msg)

    # 🔌 Alpaca Execution Logic
    if routing_flag != "APPROVED":
        # Note: If it comes from HITL, the flag was already set to APPROVED in fsm_hitl.py,
        # but if we want to differentiate the initial message, we do it above.
        pass

    if ticker == "ERROR" or alloc <= 0:
        msg = f"[{ts}][FSM_Squelch] Noise signal detected ({ticker}). Silencing output."
        logger.info(msg)
        return {
            "execution_payload": {"status": "FAILED", "action": "DISCARD"},
            "logs": [msg]
        }

    logger.info(f"[{ts}][FSM_Execution] ⚡ Nominal Execution for {ticker}...")
    
    cfg = get_orchestrator_settings()
    client = TradingClient(
        api_key=cfg.alpaca_api_key.get_secret_value(),
        secret_key=cfg.alpaca_secret_key.get_secret_value(),
        paper=cfg.alpaca_paper,
    )
    
    if risk_q.get("routing_flag") != "APPROVED":
        return {"logs": [f"[{ts}][FSM_Execution] Bypass (Flag: {risk_q.get('routing_flag')})"]}
        
    ticker = signal.get("ticker", "?")
    action_str = "LONG" if signal.get("sentiment") == "bullish" else "SHORT"
    
    price = float(risk_q.get("current_price") or 0.0)
    alloc = float(risk_q.get("allocation_usd") or 0.0)
    res = float(risk_q.get("resistance") or 0.0)
    sup = float(risk_q.get("support") or 0.0)
    
    if price <= 0:
         return {"execution_payload": {"status": "FAILED"}, "logs": ["Invalid Price"]}
         
    # Redondeo estricto de Cantidad (Entero) y Precios (2 decimales)
    qty = int(alloc / price)
    if qty <= 0 and alloc > 0:
        qty = 1 # Force minimum unit if there is at least some capital
        
    if qty <= 0:
        return {"execution_payload": {"status": "FAILED"}, "logs": ["Qty=0"]}

    if action_str == "LONG":
        side = OrderSide.BUY
        tp = res
        sl = sup
    else:
        side = OrderSide.SELL
        tp = sup
        sl = res

    # Price normalization to avoid rejections from the Power Bus (Alpaca)
    tp = round(tp, 2)
    sl = round(sl, 2)
    price = round(price, 2)

    # --- Simple Bracket Execution ---
    main_order_id = "MOCK_OR_FAILED"
    dry_run = os.getenv("DRY_RUN_MODE", "False").lower() == "true"
    
    try:
        if dry_run:
            status_val = "EXECUTED_DRY_RUN"
            main_order_id = f"DRY-RUN-{int(time.time())}"
            logger_msg = f"[{ts}][FSM_Execution] ⚠️ [DRY-RUN EXECUTION SIMULATED] Bypass Alpaca API (Qty={qty} @ ${price:.2f})"
        else:
            req = MarketOrderRequest(
                symbol=ticker,
                qty=qty,
                side=side,
                time_in_force=TimeInForce.DAY,
                order_class=OrderClass.BRACKET,
                take_profit=TakeProfitRequest(limit_price=tp),
                stop_loss=StopLossRequest(stop_price=sl),
            )
            # Impedance decoupling: Alpaca SDK is synchronous.
            loop = asyncio.get_event_loop()
            order = await asyncio.wait_for(
                loop.run_in_executor(None, client.submit_order, req),
                timeout=10.0
            )

            main_order_id = str(order.id)
            status_val = "EXECUTED"
            logger_msg = f"[{ts}][FSM_Execution] ✅ ORDER {main_order_id} sent (Qty={qty} @ ${price:.2f})"
    except Exception as e:
        logger.error(f"[{ts}][FSM_Execution] ❌ Alpaca Failed: {e}")
        status_val = "FAILED"
        logger_msg = f"[{ts}][FSM_Execution] ERROR in Bracket: {e}"
        
    payload: ExecutionPayload = {
        "action": action_str,
        "qty": qty,
        "entry_price": price,
        "take_profit": tp,
        "stop_loss": sl,
        "trail_price": 0.0,
        "broker_order_id": main_order_id,
        "status": status_val
    }
    
    return {
        "execution_payload": payload,
        "logs": [logger_msg]
    }
    
async def node_discard(state: TradingState) -> dict:
    ts = datetime.now(timezone.utc).isoformat()
    risk_q: RiskQuant = state.get("risk_quant", {})
    reason = risk_q.get("discard_reason", "No Reason")
    ticker = state.get("ingress_signal", {}).get("ticker", "UNKNOWN")

    msg = f"🗑️ SIGNAL DISCARDED\nTicker: {ticker}\nReason: {reason}"
    await send_telegram_alert(msg) # <--- SRE: We want to know why signals get discarded!

    logger.info(f"[{ts}][FSM_Discard] Signal Discarded: {reason}")
    return {"logs": [f"[{ts}][FSM_Discard] Operation cancelled: {reason}"]}

async def node_telemetry(state: TradingState) -> dict:
    """Closing Node: Logs telemetry to the Black Box."""
    from orchestrator.memory.telemetry import log_execution
    
    signal = state.get("ingress_signal", {})
    risk = state.get("risk_quant", {})
    pay = state.get("execution_payload", {})
    
    # 🔧 SRE FIX: Retrieve bus start time or use fallback
    start_time = state.get("metadata", {}).get("start_time_ms") or state.get("start_time_ms") or (time.perf_counter() * 1000)
    lat_ms = (time.perf_counter() * 1000) - start_time

    is_manual = risk.get("manual_override", False)
    routing_flag = risk.get("routing_flag", "REJECTED")

    # 🔧 PATCH: If it's manual and APPROVED, the action is APPROVED (or EXECUTED if it went through the broker)
    action = pay.get("action")
    if not action:
        if routing_flag == "APPROVED":
            action = "APPROVED"
        else:
            action = "DISCARD"

    # 🔧 SRE DEBUG: Bus Audit v6.2.3
    logger.info(f"📊 [TELEMETRY_DEBUG] Verdict: {state.get('meta_judge_verdict')} | Conf: {state.get('meta_judge_confidence')}")
    
    try:
        await log_execution(
            ticker=signal.get("ticker", "UNKNOWN"),
            sentiment=signal.get("sentiment", "neutral"),
            impact_score=signal.get("impact_score", 0.0),
            stoch_confidence=signal.get("stoch_confidence", 0.0),
            kelly_fraction=risk.get("kelly_fraction", 0.0),
            allocation_usd=risk.get("allocation_usd", 0.0),
            action=action,
            reason=risk.get("discard_reason", "N/A"),
            broker_order_id=pay.get("broker_order_id", ""),
            latency_ms=lat_ms,
            current_price=state.get("current_price", 0.0),
            vix_level=state.get("vix_level", 0.0),
            market_regime=state.get("market_regime", "UNKNOWN"),
            support=state.get("support", 0.0),
            resistance=state.get("resistance", 0.0),
            reward_risk_ratio=risk.get("reward_risk_ratio", 0.0),
            meta_judge_verdict=state.get("meta_judge_verdict", "N/A"),
            meta_judge_confidence=state.get("meta_judge_confidence", 0.0),
            source=signal.get("source", "UNKNOWN")
        )
    except Exception as e:
        # 🔧 SRE FIX: Black box failures must be reported to the main log
        logger.error(f"🚨 [BlackBox_Fault] Failed to log telemetry: {e}")

    return {"logs": [f"🏁 [FSM_Cycle_End] Latency: {lat_ms:.0f}ms | Action: {action}"]}
