"""
orchestrator/nodes/meta_judge.py - Flight Control Agent (Meta-LLM).
Version v1.9.4: Ultra-Robust Parser & Raw Signal Logging.

Hardware analogy: We have improved the 'Protocol Analyzer'. If the data
frame fails to sync, the system now dumps the raw buffer to the log
to diagnose language interference (Thinking noise).
"""

import json
import logging
import re
from datetime import UTC, datetime
from typing import Any

from langchain_core.messages import HumanMessage
from langchain_ollama import ChatOllama

from orchestrator.core.config import get_orchestrator_settings
from orchestrator.core.observability import lqa
from orchestrator.state import TradingState

logger = logging.getLogger(__name__)
settings = get_orchestrator_settings()

judge_llm = ChatOllama(
    base_url=settings.ollama_base_url,
    model=settings.reasoning_model,
    temperature=0.0,
    format="json",  # Forzamos modo JSON a nivel de driver
    timeout=120,
)

JUDGE_PROMPT = """[SYSTEM: CRITICAL_VALIDATOR]
Compare NEWS vs THESIS. Output ONLY valid JSON.
NEWS: {news}
THESIS: {thesis}

SCHEMA:
{{
  "verdict": "APPROVED" | "REJECTED",
  "reasoning": "Clinical explanation"
}}"""


async def node_meta_judge(state: TradingState) -> dict[str, Any]:
    ts = datetime.now(UTC).isoformat()
    signal = state.get("ingress_signal", {})
    risk = state.get("risk_quant", {})
    thread_id = state.get("metadata", {}).get("thread_id", "???")
    ticker = signal.get("ticker", "UNK")

    # 1. 🛡️ HARDWARE BYPASS (Pre-Inference)
    rr = float(risk.get("reward_risk_ratio", 0.0))
    if rr >= 2.5:
        return {
            "risk_quant": {**risk, "routing_flag": "APPROVED"},
            "logs": [f"[{ts}][MetaJudge] High-RR Bypass (RR={rr})"],
        }

    # 2. 🧠 SEMANTIC AUDIT WITH DPI FILTER
    try:
        prompt = JUDGE_PROMPT.format(
            news=signal.get("news_text", "")[:500], thesis=signal.get("reasoning", "")[:500]
        )
        response = await judge_llm.ainvoke([HumanMessage(content=prompt)])

        # 🧼 CARRIER CLEANUP (Squelch)
        raw_content = response.content

        # Strip 'thinking' noise from DeepSeek/Phi-4-style models
        clean_content = re.sub(r"<think>.*?</think>", "", raw_content, flags=re.DOTALL)
        clean_content = clean_content.replace("```json", "").replace("```", "").strip()

        # Surgical search for the data block { ... }
        match = re.search(r"(\{.*\})", clean_content, re.DOTALL)

        if match:
            try:
                data = json.loads(match.group(1))
                verdict = data.get("verdict", "REJECTED")
                reason = data.get("reasoning", "No specific reason in JSON.")
            except json.JSONDecodeError:
                verdict, reason = "REJECTED", "PARITY_ERROR: Invalid JSON structure."
                logger.error(f"💥 [DPI_FAULT] Corrupted frame for {ticker}: {raw_content[:100]}...")
        else:
            verdict, reason = "REJECTED", "SYNC_ERROR: No JSON found in carrier."
            logger.error(
                f"💥 [DPI_MISS] No signal detected for {ticker}. Raw: {raw_content[:50]}..."
            )

        # 3. OUTPUT BUS UPDATE
        if verdict == "REJECTED":
            lqa.trace(thread_id, "JUDGE", f"🛑 Veto: {reason}")
            return {
                "risk_quant": {
                    **risk,
                    "routing_flag": "REJECTED",
                    "discard_reason": f"Semantic Veto: {reason}",
                },
                "logs": [f"[{ts}][MetaJudge] Vetoed: {reason}"],
            }

        return {
            "risk_quant": {**risk, "routing_flag": "APPROVED"},
            "logs": [f"[{ts}][MetaJudge] Signal Validated for {ticker}."],
        }

    except Exception as e:
        logger.error(f"🚨 [JUDGE_PANIC] Thermal failure in judge node: {e}")
        return {
            "risk_quant": {
                **risk,
                "routing_flag": "REJECTED",
                "discard_reason": f"System Fault: {str(e)}",
            },
            "logs": [f"[{ts}][MetaJudge] Execution Fault."],
        }
