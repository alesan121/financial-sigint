"""
orchestrator/nodes/fsm_hitl.py - Nodo FSM: Human-In-The-Loop Escalation.
"""

import logging
from datetime import UTC, datetime

from langchain_core.prompts import PromptTemplate
from langchain_core.runnables import RunnableConfig
from langchain_ollama import ChatOllama

from orchestrator.core.config import get_orchestrator_settings
from orchestrator.state import TradingState

logger = logging.getLogger(__name__)

# ─── LOAD CONFIGURATION REGISTERS (EEPROM) ───
settings = get_orchestrator_settings()

# Initialize the Fast ALU (Phi-4 / Llama 3.2)
llm_alu = ChatOllama(
    base_url=settings.ollama_base_url,
    model=settings.reasoning_model,  # 🔧 SRE FIX: Use the official config bus
    temperature=0.1,  # Lower jitter for technical briefings
)

PROMPT_HITL_BRIEF = PromptTemplate.from_template(
    """You are a Strategic Control Unit (ALU).
Signal detected for {ticker} ({action}) is AMBIGUOUS.
Risk Parameters: Alloc=${alloc} | VIX: {vix}.
DSP Warning: {reason}
Raw Intelligence: {analyzer_reason}

Task: Generate a 3-bullet briefing for the Human Supervisor.
Be clinical, highlight the specific conflict between Technicals and Sentiment.
"""
)


async def node_hitl_escalation(state: TradingState, config: RunnableConfig) -> dict:
    """
    Interrupt Node (IRQ): Generates briefing and pauses the execution bus.
    """
    ts = datetime.now(UTC).isoformat()
    thread_id = config.get("configurable", {}).get("thread_id", "NOT_SET")

    # 🔧 SRE FIX: Robust extraction from the state bus
    risk_q = state.get("risk_quant", {})
    signal = state.get("ingress_signal", {})

    ticker = signal.get("ticker", "UNKNOWN")
    action = "LONG" if signal.get("sentiment") == "bullish" else "SHORT"
    alloc = risk_q.get("allocation_usd", 0.0)

    # 1. Briefing Execution (ALU Fast-Inference)
    try:
        prompt_str = PROMPT_HITL_BRIEF.format(
            ticker=ticker,
            action=action,
            alloc=f"{alloc:.2f}",
            vix=risk_q.get("vix_level", 15.0),
            reason=risk_q.get("discard_reason", "No specific warning"),
            analyzer_reason=signal.get("reasoning", "N/A")[:400],
        )

        response = await llm_alu.ainvoke([("human", prompt_str)])
        alu_brief = response.content.strip()

        # 🔧 SRE FIX: Standardized token telemetry (Shielded against None)
        usage = getattr(response, "usage_metadata", {}) or {}
        tokens_in = usage.get("input_tokens") or usage.get("prompt_eval_count") or 0
        tokens_out = usage.get("output_tokens") or usage.get("eval_count") or 0

    except Exception as e:
        logger.error(f"❌ [ALU_FAULT] Briefing error: {e}")
        alu_brief = "⚠️ CRITICAL: Decision ambiguity detected but briefing failed."
        tokens_in = tokens_out = 0

    # 2. IRQ Alert Emission (Tactile Interface)
    from orchestrator.workers.notifier import notify_hitl_request

    await notify_hitl_request(alu_brief, thread_id)

    # 3. Latch Flags Update
    # 🔧 SRE FIX: Explicitly mark the state as PENDING for the orchestrator
    updated_risk = {
        **risk_q,
        "routing_flag": "PENDING_APPROVAL",
        "hitl_start_ts": ts,  # ⏱️ CLOCK REFERENCE: Start of the interrupt
        "discard_reason": (risk_q.get("discard_reason") or "")
        + " | [IRQ] Awaiting Human Response.",
    }

    logger.warning(f"📡 [FSM_IRQ] Node Paused. Thread {thread_id} locked awaiting ACK.")

    return {
        "risk_quant": updated_risk,
        "usage_metadata": {"prompt_tokens": tokens_in, "completion_tokens": tokens_out},
        "logs": [f"[{ts}][FSM_HITL] IRQ Raised. Briefing: {tokens_in+tokens_out} tokens."],
    }
