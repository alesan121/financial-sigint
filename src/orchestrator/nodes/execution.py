"""
orchestrator/nodes/execution.py - Actuator Node: Alpaca Broker Relay v2.8.
Version v2.8: SRE Shield - Fix Telegram 400 & Inventory Collision Check.

Hardware analogy: This actuator is a 'Solid-State Relay with Protection Circuit'.
It implements 'Inventory Guard' and 'Radio Squelch' logic to avoid short-circuits.
"""

import asyncio
import logging
from datetime import UTC, datetime

from alpaca.trading.client import TradingClient
from alpaca.trading.enums import OrderClass, OrderSide, TimeInForce
from alpaca.trading.requests import (
    GetOrdersRequest,
    MarketOrderRequest,
    QueryOrderStatus,
    StopLossRequest,
    TakeProfitRequest,
)

from orchestrator.core.config import get_orchestrator_settings
from orchestrator.state import TradingState

logger = logging.getLogger(__name__)


def _get_alpaca_client() -> TradingClient:
    """Instantiates the Alpaca TradingClient using environment keys."""
    cfg = get_orchestrator_settings()
    return TradingClient(
        api_key=cfg.alpaca_api_key.get_secret_value(),
        secret_key=cfg.alpaca_secret_key.get_secret_value(),
        paper=cfg.alpaca_paper,
    )


def _clean_for_markdown(text: str) -> str:
    """Escapes special characters to avoid Telegram's Error 400."""
    if not text:
        return ""
    # SRE escaping for MarkdownV2
    return text.replace("_", "\\_").replace("*", "\\*").replace("`", "\\`").replace("-", "\\-")


async def node_execution(state: TradingState) -> dict:
    """
    Actuator Node v2.8: SRE Shield - Fix Telegram 400, Inventory Guard, and TP Precision.
    """
    ts = datetime.now(UTC).isoformat()

    # 🔧 SRE FIX: Signal and Risk Resolution
    signal = state.get("ingress_signal", {})
    risk = state.get("risk_quant", {})

    ticker = signal.get("ticker", "UNKNOWN")
    action = risk.get("routing_flag", "REJECTED")
    current_price = risk.get("current_price", 0.0)
    allocation_usd = risk.get("allocation_usd", 0.0)
    sentiment = signal.get("sentiment", "neutral")

    # Determine the real action (LONG/SHORT)
    trade_side = (
        OrderSide.BUY
        if sentiment == "bullish"
        else OrderSide.SELL if sentiment == "bearish" else None
    )

    # 1. 🛡️ SHIELDED NOTIFICATION PROTOCOL (Fix Error 400)
    from orchestrator.workers.notifier import send_trade_alert

    if action != "APPROVED" or trade_side is None:
        discard_reason = risk.get("discard_reason", "Reason not specified")
        safe_ticker = _clean_for_markdown(ticker)
        safe_reason = _clean_for_markdown(discard_reason)

        log_entry = f"[{ts}][node_execution] SKIP | ticker={ticker} | reason={discard_reason}"
        logger.info(log_entry)

        try:
            alert = (
                f"🗑️ *SIGNAL DISCARDED*\n" f"Ticker: `{safe_ticker}`\n" f"Reason: _{safe_reason}_"
            )
            await send_trade_alert(alert, parse_mode="MarkdownV2", disable_notification=True)
        except Exception as notify_err:
            logger.error(f"Radio failure (Telegram): {notify_err}")

        return {"logs": [log_entry]}

    # 2. 🛡️ INVENTORY COLLISION GUARD (Fix Error 403)
    client = _get_alpaca_client()
    loop = asyncio.get_running_loop()

    try:
        # Check open positions for this asset
        positions = await loop.run_in_executor(None, client.get_all_positions)
        has_position = any(p.symbol == ticker for p in positions)

        # Check pending orders for this asset
        orders_req = GetOrdersRequest(status=QueryOrderStatus.OPEN, symbols=[ticker])
        open_orders = await loop.run_in_executor(None, client.get_orders, orders_req)
        has_pending = len(open_orders) > 0

        if has_position or has_pending:
            safe_ticker = _clean_for_markdown(ticker)
            log_entry = f"[{ts}][node_execution] ABORT | Inventory Conflict detected for {ticker}."
            logger.warning(log_entry)

            alert = (
                f"⚠️ *INVENTORY CONFLICT*\n"
                f"Attention partner, attempted `{trade_side.name}` on `{safe_ticker}` aborted due to an existing position/order\\.\n"
                f"Close manually to free up the bus\\."
            )
            await send_trade_alert(alert, parse_mode="MarkdownV2")
            return {"broker_order_id": "ABORTED:INVENTORY_CONFLICT", "logs": [log_entry]}

    except Exception as inv_err:
        logger.error(f"Error checking inventory: {inv_err}")
        # When in doubt, we proceed, but under a warning log.

    # 3. 🛡️ PRECISION AND VOLTAGE ADJUSTMENT (Fix Error 422 - Take Profit)
    sl_bus = risk.get("stop_loss", 0.0)
    tp_bus = risk.get("take_profit", 0.0)

    # Ensure TP is at least 10 cents from the price to comply with Alpaca regulations
    if trade_side == OrderSide.BUY:
        safe_tp = max(tp_bus, current_price + 0.10)
        safe_sl = min(sl_bus, current_price - 0.10)
    else:  # SELL (Short)
        safe_tp = min(tp_bus, current_price - 0.10)
        safe_sl = max(sl_bus, current_price + 0.10)

    tp_price = round(safe_tp, 2)
    sl_price = round(safe_sl, 2)

    qty = int(allocation_usd / current_price)
    if qty == 0:
        return {"broker_order_id": "ABORTED:QTY_ZERO", "logs": [f"[{ts}] Qty zero for {ticker}"]}

    # 4. 🚀 RELAY LAUNCH (FOC v2.8)
    try:
        req = MarketOrderRequest(
            symbol=ticker,
            qty=qty,
            side=trade_side,
            time_in_force=TimeInForce.DAY,
            order_class=OrderClass.BRACKET,
            take_profit=TakeProfitRequest(limit_price=tp_price),
            stop_loss=StopLossRequest(stop_price=sl_price),
        )

        order = await loop.run_in_executor(None, client.submit_order, req)
        main_order_id = str(order.id)

        safe_ticker = _clean_for_markdown(ticker)
        alert = (
            f"🚀 *ORDER SENT TO MARKET*\n"
            f"Asset: `{safe_ticker}`\n"
            f"Operation: `{trade_side.name}`\n"
            f"TP: `${tp_price:.2f}` \\| SL: `${sl_price:.2f}`"
        )
        await send_trade_alert(alert, parse_mode="MarkdownV2")

        log_entry = f"[{ts}][node_execution] ✅ {trade_side.name} {ticker} OK | id={main_order_id}"
        logger.info(log_entry)

        return {"broker_order_id": main_order_id, "action": trade_side.name, "logs": [log_entry]}

    except Exception as e:
        log_err = f"[{ts}][node_execution] ❌ ERROR ALPACA: {str(e)[:100]}"
        logger.error(log_err)
        return {"broker_order_id": f"ERROR:{type(e).__name__}", "logs": [log_err]}
