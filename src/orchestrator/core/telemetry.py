"""
core/telemetry.py - Instrumentation and Tracing Probe (The Digital Oscilloscope)

Provides higher-order decorators to inject visibility (latency,
token consumption, and USD cost) directly into the LangGraph Runtime, without
cluttering the business logic.

Output exposed via Sidecar (Prometheus/Grafana).
"""

import logging
import time
from functools import wraps
from typing import Any

from prometheus_client import Counter, Histogram

logger = logging.getLogger("agentops.telemetry")

# LLM Costs (Local Mistral Execution = $0.00, but we instrument for Cloud models)
COST_PER_1K_IN_USD = 0.0001
COST_PER_1K_OUT_USD = 0.0002

# Cloud-Native Prometheus Metrics
NODE_LATENCY = Histogram(
    "sigint_node_latency_seconds",
    "Individual latency of the Orchestrator Nodes",
    ["node_name", "status"],
)
LLM_TOKENS_IN = Counter(
    "sigint_llm_tokens_in_total", "Input tokens (Prompt) ingested by the LLM", ["node_name"]
)
LLM_TOKENS_OUT = Counter(
    "sigint_llm_tokens_out_total", "Output tokens (Completion) emitted by the LLM", ["node_name"]
)
LLM_COST_USD = Counter(
    "sigint_llm_cost_usd_total", "Estimated economic thermal dissipation (USD)", ["node_name"]
)


def node_telemetry(node_name: str) -> Any:
    """
    Active telemetry probe coupled to LangGraph Nodes.
    Measures latency and injects it into the Prometheus metrics registry.
    """

    def decorator(node_func: Any) -> Any:
        @wraps(node_func)
        async def wrapper(state: dict, *args: Any, **kwargs: Any) -> Any:
            t0 = time.perf_counter()

            # 1. Payload Execution
            try:
                result = await node_func(state, *args, **kwargs)
                status = "SUCCESS"
            except Exception as e:
                status = f"ERROR:{type(e).__name__}"
                raise e
            finally:
                # 2. Latency Capture (Clock time in milliseconds)
                elapsed_seconds = time.perf_counter() - t0
                elapsed_ms = elapsed_seconds * 1000.0

                # Update Prometheus Oscilloscope
                NODE_LATENCY.labels(node_name=node_name, status=status).observe(elapsed_seconds)

                # 3. Consumption Metadata Extraction (Advanced Noise Filter)
                tokens_in = 0
                tokens_out = 0

                # We look into the node's result (usually a state-update dict)
                if isinstance(result, dict):
                    # Case A: The node returned explicit usage_metadata
                    usage = result.get("usage_metadata", {})
                    # Case B: The node returned messages, we look in the last message of the list
                    messages = result.get("messages", [])
                    if not usage and messages and hasattr(messages[-1], "usage_metadata"):
                        usage = messages[-1].usage_metadata if messages[-1].usage_metadata else {}

                    tokens_in = usage.get("prompt_tokens", 0)
                    tokens_out = usage.get("completion_tokens", 0)
                # Case C: Backward compatibility/Fallback if usage_metadata is in the input state
                elif isinstance(state, dict) and "usage_metadata" in state:
                    usage = state["usage_metadata"]
                    tokens_in = usage.get("prompt_tokens", 0)
                    tokens_out = usage.get("completion_tokens", 0)

                # Financial dissipation calculation (Operating cost)
                cost_usd = 0.0
                if tokens_in > 0 or tokens_out > 0:
                    cost_usd = (tokens_in * COST_PER_1K_IN_USD / 1000) + (
                        tokens_out * COST_PER_1K_OUT_USD / 1000
                    )
                    LLM_TOKENS_IN.labels(node_name=node_name).inc(tokens_in)
                    LLM_TOKENS_OUT.labels(node_name=node_name).inc(tokens_out)
                    LLM_COST_USD.labels(node_name=node_name).inc(cost_usd)

                # 4. Structured Logs for AgentOps (Ready for Scraping)
                logger.info(
                    "[AgentOps] component=FSM_Node node=%s status=%s latency_ms=%.1f "
                    "tokens_in=%d tokens_out=%d cost_usd=%.6f",
                    node_name,
                    status,
                    elapsed_ms,
                    tokens_in,
                    tokens_out,
                    cost_usd,
                )

            return result

        return wrapper

    return decorator
