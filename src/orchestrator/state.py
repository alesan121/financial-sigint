"""
orchestrator/state.py - FSM Multiplexed Data Bus (LangGraph).

Hardware analogy: This is the System Bus (Memory Bus) of our
Finite State Machine (FSM). It is divided into three main "channels"
to avoid data collisions and ensure clean routing.

1. ingress_signal: The Raw Data coming in from the antenna (ADC).
2. risk_quant: The math coprocessor that evaluates thermal limits.
3. execution_payload: The final package ready to inject into the broker.
"""

import operator
from typing import Annotated, Literal, Optional
from typing_extensions import TypedDict


from langchain_core.messages import BaseMessage

# --- CHANNEL 1: Ingress ADC (Primary Signal) ---
class IngressSignal(TypedDict, total=False):
    news_text: str          # Raw text received
    ticker: str             # Ticker returned by the LLM
    impact_score: float     # [-1.0, 1.0]
    stoch_confidence: float # [0.0, 1.0] SNR (Signal-To-Noise)
    sentiment: Literal["bullish", "bearish", "neutral"]
    reasoning: str


# --- CHANNEL 2: Risk Quantification & Meta-Labeling ---
class RiskQuant(TypedDict, total=False):
    # Environment variables (Sensors)
    current_price: float
    sma_20: float
    kalman_price: float
    support: float
    resistance: float
    atr_14: float
    vix_level: float
    market_regime: str

    # Derived heuristics (DSP)
    pop_score: float             # Probability of Profit after Sensor Fusion
    kelly_fraction: float        # Raw Kelly (f*)
    allocation_usd: float        # Allocated Capital
    reward_risk_ratio: float     # b (legacy/computed in engine)

    # --- NEW SAFETY PINS (FUSES v8.0) ---
    stop_loss: float             # Cut-off point (Zener Diode)
    take_profit: float           # Profit ceiling
    risk_reward_ratio: float     # Ratio computed for the Judge
    atr_14: float                # Thermal noise meter (ATR)

    routing_flag: Literal["APPROVED", "BORDERLINE", "AMBIGUOUS", "REJECTED"]
    discard_reason: str


# --- CHANNEL 3: Execution Payload (Broker) ---
class ExecutionPayload(TypedDict, total=False):
    action: Literal["LONG", "SHORT", "HOLD", "DISCARD"]
    qty: int
    entry_price: float
    take_profit: float
    stop_loss: float
    trail_price: float
    broker_order_id: str
    status: Literal["PENDING", "EXECUTED", "FAILED", "ROLLBACK"]


# --- FINITE STATE MACHINE: General State Bus ---
class TradingState(TypedDict, total=False):
    """
    Central Memory (Shared State) of the Cyclic Graph.
    Analogy: This is the System Bus with collision protection (ECC).
    """
    # Message Bus: Accumulates the dialogue between agents without overwriting.
    messages: Annotated[list[BaseMessage], operator.add]

    ingress_signal: Annotated[IngressSignal, operator.ior]
    risk_quant: Annotated[RiskQuant, operator.ior]
    execution_payload: Annotated[ExecutionPayload, operator.ior]

    # Global Telemetry and Traceability (Append-Only Lists)
    logs: Annotated[list[str], operator.add]
    portfolio_risk_status: str
    retry_count: int      # Cycle counter (Feedback Loops)
    router_decision: str  # Decision made by the Router node
    metadata: dict        # For start_time_ms and other K8s parameters

    # 🔧 SRE FIX: Parametric Bus v6.2 (Overwriting default behavior)
    current_price: float
    vix_level: float
    market_regime: str
    sma_20: float
    support: float
    resistance: float
    atr_14: float
    dynamic_pop_threshold: float
    meta_judge_verdict: str
    meta_judge_confidence: float
