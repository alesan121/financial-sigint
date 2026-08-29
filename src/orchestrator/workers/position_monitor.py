"""
orchestrator/workers/position_monitor.py — Feedback Loop Sensor.
DEFCON 1 Version: EEPROM Integration and SRE Telemetry.

Hardware analogy: This is the system's 'Encoder'. It monitors the real
state of the load (positions at the broker) and closes the control
loop by computing the maneuver's efficiency (PnL) once it's finished.
"""

import asyncio
import logging
import os
from datetime import UTC, datetime

from orchestrator.core.config import get_orchestrator_settings
from orchestrator.workers.notifier import send_trade_alert

# 📡 SRE PATCH: Alignment with the Global Telemetry Bus
logger = logging.getLogger("agentops.telemetry")


async def init_monitor_bus():
    """
    Chassis Preparation: Ensures the telemetry table has
    the terminals needed for PnL.
    """
    import aiosqlite

    settings = get_orchestrator_settings()
    db_path = settings.telemetry_db_path

    try:
        async with aiosqlite.connect(db_path) as db:
            # 🔧 PATCH: Idempotent migration of closing columns
            cols = [
                ("pnl_usd", "REAL DEFAULT NULL"),
                ("exit_price", "REAL DEFAULT NULL"),
                ("closed_at", "TEXT DEFAULT NULL"),
            ]
            for name, dtype in cols:
                try:
                    await db.execute(f"ALTER TABLE execution_logs ADD COLUMN {name} {dtype}")
                except Exception:  # nosec B110
                    pass  # Column already exists on the bus
            await db.commit()
            logger.info("✅ [Monitor_Bus] Telemetry structure synchronized.")
    except Exception as e:
        logger.error(f"🚨 [Monitor_Bus_Fault] Error initializing registers: {e}")


async def _register_closed_trade(symbol: str, pnl_usd: float, exit_price: float) -> None:
    """Injects the trade result into the Black Box (SQLite)."""
    import aiosqlite

    settings = get_orchestrator_settings()
    db_path = settings.telemetry_db_path
    closed_at = datetime.now(UTC).isoformat()

    try:
        async with aiosqlite.connect(db_path) as db:
            # UPDATE: Look for the last open entry for this ticker
            await db.execute(
                """
                UPDATE execution_logs
                SET pnl_usd = ?, exit_price = ?, closed_at = ?
                WHERE ticker = ? AND closed_at IS NULL
                AND id = (SELECT MAX(id) FROM execution_logs WHERE ticker = ? AND closed_at IS NULL)
                """,
                (round(pnl_usd, 2), round(exit_price, 2), closed_at, symbol, symbol),
            )
            await db.commit()
    except Exception as e:
        logger.error(f"💥 [Telemetry_Write_Error] Failed to register close for {symbol}: {e}")


async def run_position_monitor():
    """
    Watch Loop: High-fidelity polling to detect
    automatic TP/SL triggers at the broker.
    """
    from alpaca.trading.client import TradingClient
    from alpaca.trading.enums import QueryOrderStatus
    from alpaca.trading.requests import GetOrdersRequest

    settings = get_orchestrator_settings()
    client = TradingClient(
        api_key=settings.alpaca_api_key.get_secret_value(),
        secret_key=settings.alpaca_secret_key.get_secret_value(),
        paper=settings.alpaca_paper,
    )

    await init_monitor_bus()

    # Local SRAM for edge detection (Open -> Closed)
    loop = asyncio.get_running_loop()
    prev_positions = {}

    # Initial state capture to avoid false positives on boot
    initial = await loop.run_in_executor(None, client.get_all_positions)
    prev_positions = {p.symbol: p for p in initial}

    logger.info(f"🛰️ [Monitor] Scanning {len(prev_positions)} active terminals.")

    while True:
        try:
            await asyncio.sleep(int(os.getenv("MONITOR_POLL_INTERVAL_SECONDS", "60")))

            # 📥 Current measurement
            current_raw = await loop.run_in_executor(None, client.get_all_positions)
            current_positions = {p.symbol: p for p in current_raw}

            # 🕵️ Signal Drop Detection (Closed Position)
            closed_symbols = set(prev_positions.keys()) - set(current_positions.keys())

            for symbol in closed_symbols:
                old_p = prev_positions[symbol]

                # 🔧 SRE FIX: Protection against empty signals (Alpaca API Latency)
                req = GetOrdersRequest(status=QueryOrderStatus.CLOSED, symbols=[symbol], limit=1)
                orders = await loop.run_in_executor(None, client.get_orders, req)

                if orders and float(orders[0].filled_avg_price) > 0:
                    exit_price = float(orders[0].filled_avg_price)
                else:
                    exit_price = float(
                        old_p.current_price
                    )  # Fallback if no closed order is available yet
                entry_price = float(old_p.avg_entry_price)
                qty = float(old_p.qty)

                # Power calculation (PnL)
                side_mult = 1 if old_p.side.value == "long" else -1
                pnl = (exit_price - entry_price) * qty * side_mult
                pnl_pct = (pnl / (entry_price * qty)) * 100

                icon = "✅" if pnl >= 0 else "❌"
                logger.info(f"🏁 [Closed] {symbol} | PnL: ${pnl:.2f} ({pnl_pct:.2f}%)")

                # Alert and Log
                await _register_closed_trade(symbol, pnl, exit_price)
                alert = (
                    f"{icon} *POSITION CLOSED* — {symbol}\n"
                    f"Realized PnL: `${pnl:+.2f}` ({pnl_pct:+.2f}%)\n"
                    f"Exit: `${exit_price:.2f}`"
                )
                await send_trade_alert(alert)

            # 🕵️ New Signal Detection (Position Opened outside FSM)
            new_symbols = set(current_positions.keys()) - set(prev_positions.keys())
            for symbol in new_symbols:
                p = current_positions[symbol]
                logger.info(f"🆕 [Opened] New load detected: {symbol}")
                await send_trade_alert(
                    f"📈 *NEW POSITION* — {symbol}\nQty: `{p.qty}` @ `${p.avg_entry_price}`"
                )

            prev_positions = current_positions

        except Exception as e:
            logger.error(f"🚨 [Monitor_Runtime_Fault] Error in sensor cycle: {e}")
            await asyncio.sleep(10)


if __name__ == "__main__":
    try:
        asyncio.run(run_position_monitor())
    except KeyboardInterrupt:
        logger.info("🛑 [Monitor] Shutdown requested by operator.")
