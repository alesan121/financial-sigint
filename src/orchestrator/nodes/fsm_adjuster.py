"""
orchestrator/nodes/fsm_adjuster.py - FSM Node: Position Adjuster.

Analogy: This is a 'Closed-Loop Attenuator' (Feedback Loop).
When Risk_Quant evaluates volatility as BORDERLINE or AMBIGUOUS,
the FSM routes the signal to this node to "trim" the signal power
(Quarter-Kelly) before sending it to the Actuator (Execution).
This is where the sequential paradigm gets broken!
"""

import logging
from datetime import datetime, timezone

from orchestrator.state import TradingState, RiskQuant

logger = logging.getLogger(__name__)

async def node_position_adjuster(state: TradingState) -> dict:
    ts = datetime.now(timezone.utc).isoformat()
    # 🔧 SRE FIX: Extraction from the risk bus
    risk_q = state.get("risk_quant", {})

    # 🔧 SRE FIX: We don't mutate, we create a new data frame (Immutability)
    # If RiskQuant is Pydantic, we use model_copy. If it's a Dict, we use unpacking.
    is_pydantic = hasattr(risk_q, "model_copy")
    current_alloc = risk_q.allocation_usd if is_pydantic else risk_q.get("allocation_usd", 0.0)
    routing = risk_q.routing_flag if is_pydantic else risk_q.get("routing_flag", "REJECTED")

    if current_alloc > 0 and routing in ("BORDERLINE", "AMBIGUOUS"):
        new_alloc = current_alloc * 0.5 # -6dB Attenuation (50% Power Cut)
        reason_patch = f" | [FSM] Bias Adjusted: Gain Damped to ${new_alloc:,.2f} due to Volatility."
        
        if is_pydantic:
            updated_risk = risk_q.model_copy(update={
                "allocation_usd": new_alloc,
                "routing_flag": "APPROVED",
                "discard_reason": (risk_q.discard_reason or "") + reason_patch
            })
        else:
            updated_risk = {
                **risk_q, 
                "allocation_usd": new_alloc, 
                "routing_flag": "APPROVED",
                "discard_reason": risk_q.get("discard_reason", "") + reason_patch
            }
            
        msg = f"✅ [FSM_Adjuster] Feedback Loop: Signal Damped (-50%) -> ${new_alloc:.2f}"
    else:
        updated_risk = risk_q
        msg = f"⚠️ [FSM_Adjuster] No adjustment needed for routing={routing}"

    logger.info(f"[{ts}]{msg}")

    return {
        "risk_quant": updated_risk,
        "retry_count": state.get("retry_count", 0) + 1,
        "logs": [msg]
    }
