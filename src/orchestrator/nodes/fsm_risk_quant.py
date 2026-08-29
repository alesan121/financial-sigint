"""
orchestrator/nodes/fsm_risk_quant.py - Quantitative Risk Agent (DSP).
Version v2.4: Ultra-Low Latency DSP (Pure Python Math).

Hardware analogy: We have removed the redundant inference coprocessor.
This node now works as a pure high-speed DSP, computing the Kelly
and RR-Ratio in microseconds before passing the signal on to the Judge.
"""

import logging
from datetime import UTC, datetime

from orchestrator.core.config import get_orchestrator_settings
from orchestrator.core.observability import lqa
from orchestrator.nodes.risk_engine import (
    _calculate_pop,
    _compute_real_kelly,
    calculate_dynamic_safety,
)
from orchestrator.state import RiskQuant, TradingState

logger = logging.getLogger(__name__)
settings = get_orchestrator_settings()


async def node_risk_quant_agent(state: TradingState) -> dict:
    ts = datetime.now(UTC).isoformat()
    signal = state.get("ingress_signal", {})
    ticker = signal.get("ticker", "ERROR")
    sentiment = signal.get("sentiment", "neutral")
    thread_id = state.get("metadata", {}).get("thread_id", "default_bus")

    current_risk = state.get("risk_quant", {})
    if "[Manual_Override]" in (current_risk.get("discard_reason") or ""):
        lqa.trace(thread_id, "QUANT", "Manual Passthrough: Bypassing DSP Logic.")
        return {}

    # ── PROTECTION AGAINST ARITHMETIC SHORT-CIRCUIT (Sensor Check) ──
    safe_price = float(state.get("current_price") or 0.0)
    vix = float(state.get("vix_level") or 20.0)
    regime = state.get("market_regime", "RISK_OFF_VOLATILE")

    if safe_price <= 0:
        lqa.trace(
            thread_id, "QUANT", "🚨 Zero Price detected in Bus. Shutdown.", level=logging.ERROR
        )
        pop_score, k_frac, b = 0.0, 0.0, 0.0
    else:
        # ── DSP MATH (No LLM latency) ──
        pop_score, _ = _calculate_pop(
            p=signal.get("stoch_confidence", 0.0), impact_score=signal.get("impact_score", 0.0)
        )
        k_frac, b, _ = _compute_real_kelly(
            p=pop_score,
            current_price=safe_price,
            support=state.get("support", 0.0),
            resistance=state.get("resistance", 0.0),
            sentiment=sentiment,
            market_regime=regime,
        )

    allocation = k_frac * settings.virtual_balance_usd

    # ── FUSE CALCULATION (SL/TP/RR) ──
    atr = float(state.get("atr_14", 0.0))
    sl, tp, rr = calculate_dynamic_safety(safe_price, atr, sentiment)

    # ── INITIAL ROUTING LOGIC (Threshold-Based) ──
    # The Meta-Judge will do the final 'Hard-Wired' validation.
    routing_flag = "REJECTED"
    reason = ""
    stoch_conf = float(signal.get("stoch_confidence", 0.0))

    if ticker == "ERROR" or safe_price <= 0:
        reason = "Sensor Fault / Empty Ticker"
    elif allocation < settings.min_allocation_usd:
        reason = f"Insufficient Allocation: ${allocation:.2f} < Threshold"
    elif 0.5 <= stoch_conf <= 0.80:
        routing_flag = "AMBIGUOUS"
        reason = f"Ambiguous Confidence: {stoch_conf:.2f}"
    elif stoch_conf > 0.80:
        routing_flag = "APPROVED"  # The Judge can still veto later if RR < 1.5
        reason = "High SNR Signal - Candidate for Execution"
    else:
        reason = "Low SNR Signal - Discarded"

    # ── STATE BUS UPDATE (v8.2 Final Sync) ──
    updated_risk: RiskQuant = {
        "current_price": safe_price,
        "sma_20": float(state.get("sma_20", 0.0)),
        "support": float(state.get("support", 0.0)),
        "resistance": float(state.get("resistance", 0.0)),
        "atr_14": float(state.get("atr_14", 0.0)),
        "vix_level": vix,
        "market_regime": regime,
        "pop_score": pop_score,
        "kelly_fraction": k_frac,
        "allocation_usd": allocation if routing_flag != "REJECTED" else 0.0,
        "stop_loss": sl,
        "take_profit": tp,
        "reward_risk_ratio": rr,  # 🔌 PIN SYNCED FOR DB AND JUDGE
        "routing_flag": routing_flag,
        "discard_reason": reason,
    }

    return {
        "risk_quant": updated_risk,
        "logs": [f"[{ts}][FSM_Quant] DSP Logic Pass. RR={rr:.2f} | Kelly={k_frac*100:.1f}%"],
    }
