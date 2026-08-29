"""
orchestrator/core/observability.py - Advanced Telemetry System.
Implements Prometheus instrumentation and Structured Logging.
"""

import logging
import sys

from prometheus_client import Counter, Gauge, Histogram, start_http_server

# --- LOGICAL HARDWARE METRICS (Prometheus) ---
# Counter of injected pulses
FSM_SIGNALS_TOTAL = Counter(
    "sigint_fsm_signals_total", "Total signals injected into the bus", ["source", "status"]
)
# Inference latency histogram (the processor's "heat")
INFERENCE_LATENCY = Histogram(
    "sigint_inference_latency_seconds", "Response time of the AI Agents", ["node"]
)
# Market voltage gauge (VIX)
MARKET_VIX_GAUGE = Gauge("sigint_market_vix_level", "Current level of the VIX sensor")
# Active Capital Allocation
CAPITAL_ALLOCATED = Gauge(
    "sigint_capital_allocated_usd", "Dollars allocated by the Kelly Engine", ["ticker"]
)


class SIGINTLogger:
    """
    Super Logger with thread-tracing capability.
    Designed for forensic analysis by the AI Partner.
    """

    def __init__(self, name="SIGINT-LQA"):
        self.logger = logging.getLogger(name)
        self.logger.setLevel(logging.INFO)

        # Industrial-Grade Format: [Timestamp] [ThreadID] [Node] [Level] Message
        # Note: thread_id and component are passed via 'extra'
        formatter = logging.Formatter(
            "[%(asctime)s] [%(thread_id)s] [%(component)s] [%(levelname)s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )

        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(formatter)
        if not self.logger.handlers:
            self.logger.addHandler(handler)

    def trace(self, thread_id: str, component: str, message: str, level=logging.INFO):
        """Emits a log with full context for Gemini analysis."""
        extra = {"thread_id": thread_id, "component": component}
        self.logger.log(level, message, extra=extra)


# Global analyzer instance
lqa = SIGINTLogger()


def start_telemetry_server(port=9090):
    """Starts the metrics exporter for Prometheus."""
    try:
        start_http_server(port)
        lqa.trace("SYSTEM", "METRICS", f"Metrics server opened on port {port}")
    except Exception as e:
        lqa.trace("SYSTEM", "METRICS", f"Error opening metrics port: {e}", level=logging.ERROR)
