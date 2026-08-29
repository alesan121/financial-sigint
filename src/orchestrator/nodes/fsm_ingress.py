"""
orchestrator/nodes/fsm_ingress.py - FSM Node: Ingress Receiver.

Analogy: This is the system's 'Antenna and Demodulator'. It picks up the
raw signal from the environment, normalizes it, and loads it onto channel I
of the Main Bus.
"""

import logging
from datetime import datetime, timezone
from orchestrator.state import TradingState

logger = logging.getLogger(__name__)

async def node_ingress_receiver(state: TradingState) -> dict:
    """
    [SRE] Zero-Latency Buffer Node.

    Normalizes the impedance of signals coming from different scouts
    (RSS, FRED, Insider) so the Router works with a standard voltage.
    """
    ts = datetime.now(timezone.utc).isoformat()
    signal_in = state.get("ingress_signal", {})

    # 🔌 SRE PATCH: Channel normalization (Backward Compatibility)
    raw_text = signal_in.get("news_text") or signal_in.get("text")

    # 🛡️ INPUT FUSE: Automatic Squelch
    # If the antenna picks up nothing, we short-circuit to ERROR to save GPU
    if not raw_text or len(str(raw_text).strip()) < 5:
        logger.warning(f"[{ts}][FSM_Ingress] Null or noisy signal detected. Activating Squelch.")
        return {
            "ingress_signal": {
                "ticker": "ERROR",
                "news_text": "EMPTY_SIGNAL",
                "reasoning": "Input signal dropped: No usable text payload."
            },
            "logs": [f"[{ts}][FSM_Ingress] Squelch: Empty signal grounded out."]
        }

    # Bus normalization: Ensure news_text is the primary record
    signal = signal_in.copy()
    signal["news_text"] = str(raw_text).strip()

    if signal.get("ticker") and signal.get("sentiment"):
        logger.info(f"[{ts}][FSM_Ingress] ⚡ Pre-structured signal (partial bypass) detected: {signal['ticker']}")
    else:
        logger.info(f"[{ts}][FSM_Ingress] 📥 RAW signal captured. Injecting into Multiplexer.")

    return {
        "ingress_signal": signal,
        "logs": [f"[{ts}][FSM_Ingress] Handshake successful. Signal synchronized on local bus."]
    }
