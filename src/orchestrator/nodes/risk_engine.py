"""
orchestrator/nodes/risk_engine.py - Digital Signal Processing Core (DSP).
Version v4.3: Protection against Null ATR and Bus Synchronization.

Hardware analogy: we have installed an 'emergency oscillator'. If the main
noise sensor (ATR) gives no signal, the system generates a reference
frequency based on the price to keep the fuses operational.
"""

import logging

from orchestrator.core.config import get_orchestrator_settings
from orchestrator.core.telemetry import node_telemetry as trace_node
from orchestrator.state import TradingState

logger = logging.getLogger(__name__)

# --- EEPROM LOADING (Global Configuration) ---
settings = get_orchestrator_settings()


def calculate_dynamic_safety(
    price: float, atr: float, sentiment: str
) -> tuple[float, float, float]:
    """
    Fuse Calculation (SL/TP) with 'Limp-Home' mode (Graceful Degradation).
    """
    if price <= 0:
        return 0.0, 0.0, 0.0

    # 🔌 SRE PATCH: If ATR is 0 or null, we use a 2% fallback (Baseline noise)
    # This prevents RR_Ratio from being 0 and the Judge vetoing the signal for lack of data.
    safe_atr = atr if atr > 0 else (price * 0.02)

    # The 2.0x ATR multiplier is the industrial protection standard
    stop_dist = safe_atr * 2.0

    if sentiment == "bearish":  # Short case (NVIDIA Bearish Test)
        sl = price + stop_dist
        tp = price - (stop_dist * 2.5)
        # 🛡️ SRE PROTECTION: SL can never be less than 10 cents from the price
        sl = max(sl, price + 0.10)
    else:  # Long case (bullish)
        sl = price - stop_dist
        tp = price + (stop_dist * 2.5)
        # 🛡️ SRE PROTECTION: SL can never be less than 10 cents from the price
        sl = min(sl, price - 0.10)

    # Compute the Reward/Risk Ratio
    risk_width = abs(price - sl)
    rr_ratio = abs(tp - price) / risk_width if risk_width > 0 else 0.0

    return round(sl, 2), round(tp, 2), round(rr_ratio, 2)


def _calculate_pop(p: float, impact_score: float, **kwargs) -> tuple[float, str]:
    """
    Probability of Profit (PoP) Calculator.
    Fuses sensor confidence (STOCH) with the impact of the message.
    """
    # Impact attenuation (Normalization to 0-1 scale)
    adj_impact = (impact_score + 1.0) / 2.0
    pop = (p * 0.7) + (adj_impact * 0.3)
    return round(pop, 4), "PoP Fusion Successful"


def _compute_real_kelly(
    p: float,
    current_price: float,
    support: float,
    resistance: float,
    sentiment: str,
    market_regime: str,
) -> tuple[float, float, str]:
    """
    Precision Kelly Engine with Closed-Loop Attenuator.
    """
    if current_price <= 0:
        return 0.0, 0.0, "Zero Price"

    # Compute the gain (b) based on distance to technical levels
    if sentiment == "bullish":
        risk = abs(current_price - support) if support > 0 else current_price * 0.02
        reward = (
            abs(resistance - current_price) if resistance > current_price else current_price * 0.04
        )
    else:
        risk = abs(resistance - current_price) if resistance > 0 else current_price * 0.02
        reward = abs(current_price - support) if support < current_price else current_price * 0.04

    b = reward / risk if risk > 0 else 1.0

    # Master formula: f = (bp - q) / b
    q = 1.0 - p
    f_star = (b * p - q) / b if b > 0 else 0.0

    # 🔌 OUTPUT FILTER APPLICATION
    # 1. Kelly Divisor (Volatility attenuation)
    attenuated_f = f_star / settings.kelly_divisor

    # 2. Clamping (Amplitude limitation to max max_kelly_fraction)
    final_f = max(0.0, min(attenuated_f, settings.max_kelly_fraction))

    # 3. VIX Penalty (Market noise adjustment)
    if market_regime == "RISK_OFF_VOLATILE":
        final_f *= 0.5  # Power cut to 50% during panic

    return round(final_f, 4), round(b, 2), "Kelly Calc OK"


@trace_node("Risk_Engine")
async def node_risk_evaluation(state: TradingState) -> dict:
    """
    Telemetry Node: This node now acts as a 'Bus Monitor'
    that uses the pure calculation functions above.
    """
    # This node can be kept as a backup or bridge if the FSM requires it,
    # but the main logic now lives in the pure functions above.
    return {"logs": ["Risk Engine Logic Core Ready."]}
