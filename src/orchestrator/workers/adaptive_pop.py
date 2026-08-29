"""
orchestrator/workers/adaptive_pop.py — Automatic Gain Control (AGC).
DEFCON 1 Version: Closed-Loop Feedback and SRE Telemetry.

Hardware analogy: This module is the receiver's AGC. It measures the output
power (portfolio WinRate) and adjusts input sensitivity (PoP Threshold) to
keep the system at the optimal operating point, avoiding capital "clipping".
"""

import logging
import os
from datetime import UTC, datetime

import aiosqlite

# Instrumentation alignment with the Main Gateway
logger = logging.getLogger("agentops.telemetry")

# --- CONFIGURATION REGISTERS (Bias Settings) ---
# These values act as the calibration potentiometers on the physical board.
BASE_POP = float(os.getenv("MIN_POP_THRESHOLD", "0.75"))
TARGET_WIN_RATE = float(os.getenv("ADAPTIVE_TARGET_WIN_RATE", "0.40"))
KP = float(os.getenv("ADAPTIVE_POP_KP", "0.10"))  # Controller's Proportional Gain
POP_MIN = float(os.getenv("ADAPTIVE_POP_MIN", "0.60"))
POP_MAX = float(os.getenv("ADAPTIVE_POP_MAX", "0.92"))
MIN_SAMPLE_SIZE = int(
    os.getenv("ADAPTIVE_MIN_SAMPLE", "20")
)  # Minimum window for statistical stability

TELEMETRY_DB_PATH = os.getenv("TELEMETRY_DB_PATH", "/app/data/telemetry.db")


async def get_historical_win_rate(lookback_trades: int = 50) -> tuple[float, int]:
    """
    Output Probe: Measures real performance by querying the 'Black Box'.

    Returns:
        (win_rate, n_trades): Sample of the system's current state.
    """
    try:
        if not os.path.exists(TELEMETRY_DB_PATH):
            logger.warning(
                f"⚠️ [AGC] Telemetry bus not detected at {TELEMETRY_DB_PATH}. Using defaults."
            )
            return 0.0, 0

        async with aiosqlite.connect(TELEMETRY_DB_PATH) as db:
            # Query the last N signals that reached real execution
            cursor = await db.execute(
                """
                SELECT pnl_usd
                FROM execution_logs
                WHERE action IN ('LONG', 'SHORT')
                  AND pnl_usd IS NOT NULL
                ORDER BY id DESC LIMIT ?
                """,
                (lookback_trades,),
            )
            rows = await cursor.fetchall()

            if not rows:
                return 0.0, 0

            n_total = len(rows)
            n_wins = sum(1 for (pnl,) in rows if pnl > 0)
            win_rate = n_wins / n_total

            return win_rate, n_total

    except Exception as e:
        logger.error(f"💥 [AGC_SENSE_FAULT] Critical failure reading history: {e}")
        return 0.0, 0


async def get_recommended_pop() -> tuple[float, str]:
    """
    P (Proportional) Controller Algorithm:
    Computes the new PoP threshold to close the feedback loop.

    Analogy: If the WinRate drops (voltage loss), the controller raises the
    impedance (PoP) so that only the strongest signals reach the amplifier.
    """
    ts = datetime.now(UTC).isoformat()
    win_rate, n_trades = await get_historical_win_rate()

    # 🛡️ STARTUP PROTECTION (Soft Start)
    if n_trades < MIN_SAMPLE_SIZE:
        audit_msg = (
            f"Mode: WARMUP | Sample: {n_trades}/{MIN_SAMPLE_SIZE} | Using Base PoP: {BASE_POP}"
        )
        return BASE_POP, audit_msg

    # --- TRACKING ERROR CALCULATION ---
    # error > 0: The system is over-performing successfully (Slack allowed)
    # error < 0: The system is losing (Restriction required)
    error = win_rate - TARGET_WIN_RATE

    # Apply the inversely-proportional control law
    # Positive delta (raise PoP) if the error is negative (we're losing)
    delta_pop = -KP * error

    # Clamping: Ensure the voltage doesn't burn out the components
    new_pop = max(POP_MIN, min(POP_MAX, BASE_POP + delta_pop))

    # Structured Telemetry Formatting
    status = "STABLE" if abs(error) < 0.05 else "ADJUSTING"
    audit_msg = (
        f"AGC_{status} | WinRate:{win_rate*100:.1f}% | Target:{TARGET_WIN_RATE*100:.0f}% | "
        f"Error:{error:+.2f} | Recommended_PoP:{new_pop:.4f}"
    )

    logger.info(f"[{ts}] [AGC_LOG] {audit_msg}")
    return new_pop, audit_msg
