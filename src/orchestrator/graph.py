"""
orchestrator/graph.py - The Logical 'Backplane' of the FSM (LangGraph).
Version v8.0: Critical Re-sequencing (Risk engine before Meta Judge).
"""

import logging
import time
from datetime import UTC, datetime
from typing import Any, Literal

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph

from orchestrator.nodes.execution import node_execution
from orchestrator.nodes.fsm_adjuster import node_position_adjuster
from orchestrator.nodes.fsm_execution import node_discard, node_telemetry
from orchestrator.nodes.fsm_hitl import node_hitl_escalation
from orchestrator.nodes.fsm_ingress import node_ingress_receiver
from orchestrator.nodes.fsm_risk_quant import node_risk_quant_agent
from orchestrator.nodes.meta_judge import node_meta_judge
from orchestrator.nodes.multi_agent import node_analyzer, node_router, node_tool_extractor
from orchestrator.nodes.portfolio_risk import node_portfolio_risk_check
from orchestrator.nodes.technical import node_technical_analysis as node_market_monitor
from orchestrator.state import TradingState

logger = logging.getLogger(__name__)


async def node_initialize(state: TradingState) -> dict[str, Any]:
    """Capacitor charge-up and register reset."""
    return {
        "metadata": {"start_time_ms": time.perf_counter() * 1000},
        "risk_quant": {"routing_flag": "REJECTED", "manual_override": False, "hitl_start_ts": None},
        "retry_count": 0,
        "logs": ["Init: System Bus Powered Up. Decision Flags Reset."],
    }


def multi_agent_router(state: TradingState) -> Literal["tool_extractor", "discard"]:
    decision = state.get("router_decision", "NOISE")
    if decision in ["EXTRACT", "DIRECT"]:
        return "tool_extractor"
    return "discard"


def risk_quant_router(
    state: TradingState,
) -> Literal["portfolio_risk", "adjuster", "hitl", "discard"]:
    from orchestrator.core.config import get_orchestrator_settings

    settings = get_orchestrator_settings()

    routing = state.get("risk_quant", {}).get("routing_flag", "REJECTED")
    retry = state.get("retry_count", 0)

    if retry > 3:
        logger.warning("[FSM] Retry limit reached. Forcing discard.")
        return "discard"

    if routing == "APPROVED":
        return "portfolio_risk"
    elif routing == "BORDERLINE":
        return "adjuster"
    elif routing == "AMBIGUOUS":
        if not settings.hitl_enabled:
            logger.info("[FSM] ⚡ HITL_ENABLED=False: Autopass AMBIGUOUS to Adjuster (-50% Power).")
            return "adjuster"
        return "hitl"
    return "discard"


def post_hitl_router(state: TradingState) -> Literal["portfolio_risk", "discard"]:
    routing = state.get("risk_quant", {}).get("routing_flag", "REJECTED")
    if routing == "APPROVED":
        return "portfolio_risk"
    return "discard"


def portfolio_router(
    state: TradingState,
) -> Literal["execution_agent", "hitl_escalation", "discard"]:
    """Power Control Router: Distributes based on risk state."""
    risk = state.get("risk_quant", {})
    routing = risk.get("routing_flag", "REJECTED")

    if routing == "APPROVED":
        return "execution_agent"
    if routing == "AMBIGUOUS":
        return "hitl_escalation"
    return "discard"


def build_fsm_graph() -> StateGraph:
    builder = StateGraph(TradingState)

    builder.add_node("initialize", node_initialize)
    builder.add_node("ingress_receiver", node_ingress_receiver)
    builder.add_node("router", node_router)
    builder.add_node("tool_extractor", node_tool_extractor)
    builder.add_node("market_monitor", node_market_monitor)
    builder.add_node("analyzer", node_analyzer)
    builder.add_node("meta_judge", node_meta_judge)
    builder.add_node("risk_quant_agent", node_risk_quant_agent)
    builder.add_node("position_adjuster", node_position_adjuster)
    builder.add_node("hitl_escalation", node_hitl_escalation)
    builder.add_node("portfolio_risk_check", node_portfolio_risk_check)
    builder.add_node("execution_agent", node_execution)
    builder.add_node("discard", node_discard)
    builder.add_node("telemetry", node_telemetry)

    builder.add_edge(START, "initialize")
    builder.add_edge("initialize", "ingress_receiver")
    builder.add_edge("ingress_receiver", "router")

    builder.add_conditional_edges(
        "router", multi_agent_router, {"tool_extractor": "tool_extractor", "discard": "discard"}
    )

    # 🧬 SEQUENCE v8.0: The Judge acts post-DSP (Risk Engine)
    builder.add_edge("tool_extractor", "market_monitor")
    builder.add_edge("market_monitor", "analyzer")
    builder.add_edge("analyzer", "risk_quant_agent")  # risk_engine
    builder.add_edge("risk_quant_agent", "meta_judge")

    # 🔧 Routing from the Judge (CRO sign-off)
    builder.add_conditional_edges(
        "meta_judge",
        risk_quant_router,
        {
            "portfolio_risk": "portfolio_risk_check",
            "adjuster": "position_adjuster",
            "hitl": "hitl_escalation",
            "discard": "discard",
        },
    )

    builder.add_edge("position_adjuster", "risk_quant_agent")
    builder.add_conditional_edges(
        "hitl_escalation",
        post_hitl_router,
        {"portfolio_risk": "portfolio_risk_check", "discard": "discard"},
    )

    # 🔧 Post-Portfolio Risk Routing (HITL Bypass Diode)
    builder.add_conditional_edges(
        "portfolio_risk_check",
        portfolio_router,
        {
            "execution_agent": "execution_agent",
            "hitl_escalation": "hitl_escalation",
            "discard": "discard",
        },
    )

    builder.add_edge("execution_agent", "telemetry")
    builder.add_edge("discard", "telemetry")
    builder.add_edge("telemetry", END)

    checkpointer = MemorySaver()
    return builder.compile(checkpointer=checkpointer, interrupt_after=["hitl_escalation"])


fsm_pipeline = build_fsm_graph()


async def run_trading_cycle(
    news_text: str, initial_signal: dict | None = None, thread_id: str = "default_bus"
) -> TradingState:
    signal = initial_signal.copy() if initial_signal else {}
    if news_text:
        signal["news_text"] = news_text

    initial_state = {"ingress_signal": signal, "messages": [], "retry_count": 0, "logs": []}
    config = {"configurable": {"thread_id": thread_id}}
    return await fsm_pipeline.ainvoke(initial_state, config=config)


async def resume_trading_cycle(thread_id: str, human_decision: str = "APPROVED") -> TradingState:
    config = {"configurable": {"thread_id": thread_id}}
    ts = datetime.now(UTC).isoformat()
    decision_flag = (
        "APPROVED" if human_decision.upper() in ["BUY", "APPROVED", "YES"] else "REJECTED"
    )

    await fsm_pipeline.aupdate_state(
        config,
        {
            "risk_quant": {
                "routing_flag": decision_flag,
                "manual_override": True,  # 🔧 PATCH: Human authority latch
                "discard_reason": f"[Manual_Override] Action: {decision_flag}",
            },
            "logs": [f"[{ts}][FSM_RESUME] 🕹️ Manual Override Received: {decision_flag}"],
        },
        as_node="hitl_escalation",
    )
    return await fsm_pipeline.ainvoke(None, config=config)
