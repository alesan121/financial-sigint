"""
scripts/synthetic_injector.py - Synthetic Function Generator (POST - Power-On Self-Test).
Analogy: Injects a square wave (financial news) into the input bus
to verify continuity of the Multi-Agent cluster (Router -> Extractor -> Analyzer).
"""

import asyncio
import os
import sys
import time

# Path bus adjustment: make sure Python finds the 'src' package
# We add the absolute path to the 'src' subdirectory so relative imports work.
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

try:
    from orchestrator.graph import run_trading_cycle
except ImportError as e:
    print("❌ [SRE] Routing failure: Could not import 'run_trading_cycle'.")
    print("   Make sure you run this from the project root or within the poetry environment.")
    print(f"   Error: {e}")
    sys.exit(1)

# =============================================================================
# TEST WAVE (Synthetic Payload) - Maximum Entropy
# =============================================================================
TEST_NEWS = """
NVIDIA (NVDA) announces a breakthrough in its new Quantum-AI processing units,
expecting a 40% revenue increase for Q3. However, the SEC has just launched
an unexpected antitrust probe into their data center monopolistic practices,
causing institutional investors to panic sell. Supply chain issues in Taiwan
might also delay production by 3 months.
"""


async def run_diagnostics():
    print("\n" + "=" * 70)
    print("📡 [SRE] SIGINT - STARTING POST SEQUENCE (Power-On Self-Test)")
    print("=" * 70)
    print(f"🔌 [SRE] Injecting carrier wave (Length: {len(TEST_NEWS)} chars)")
    print("-" * 70)

    start_time = time.perf_counter()

    try:
        # --- INJECTION INTO THE LANGGRAPH BUS ---
        # This method injects the initial state and starts the state machine
        final_state = await run_trading_cycle(news_text=TEST_NEWS)

        elapsed = time.perf_counter() - start_time

        # --- OSCILLOSCOPE READOUT (Final State Extraction) ---
        print("\n✅ [SRE] Multi-Agent Sweep Completed Successfully.")
        print(f"⏱️  [SRE] Total Cluster Latency: {elapsed:.2f} seconds")
        print("-" * 70)

        # Read the accumulation log (with operator.ior protection)
        ingress_data = final_state.get("ingress_signal", {})
        router_decision = final_state.get("router_decision", "UNKNOWN")

        print("\n📊 --- MEMORY BUS DIAGNOSTIC (State Bus) ---")
        print(f"  [MUX] Router Decision    : {router_decision} (Expected: EXTRACT)")
        print(f"  [CPU] Extracted Ticker   : {ingress_data.get('ticker', 'N/A')}")
        print(f"  [ADC] Logical Sentiment  : {ingress_data.get('sentiment', 'N/A')}")
        print(f"  [SNR] Impact Score       : {ingress_data.get('impact_score', 'N/A')}")

        print("\n🧠 --- ANALYZER SYNTHESIS (Phi-4 Reasoning Core) ---")
        reasoning = ingress_data.get("reasoning", "No reasoning on the bus")
        # Print a meaningful chunk of the synthesis
        cutoff = 600
        print(
            reasoning[:cutoff]
            + ("\n[... SIGNAL TRUNCATED BY THE POST BUFFER ...]" if len(reasoning) > cutoff else "")
        )

        print("\n📜 --- HARDWARE LOG AUDIT (Full Trace) ---")
        logs = final_state.get("logs", [])
        if not logs:
            print("   [!] No traces found on the log bus.")
        for log in logs:
            print(f"   > {log}")

        print("\n" + "=" * 70)
        print("✅ [SRE] NOMINAL DIAGNOSTIC - SYSTEM READY FOR GO-LIVE")
        print("=" * 70)

    except Exception as e:
        print(f"\n💥 [SRE] CRITICAL SHORT CIRCUIT DETECTED: {str(e)}")
        import traceback

        traceback.print_exc()


if __name__ == "__main__":
    # Ignite the async loop in the runtime environment
    try:
        asyncio.run(run_diagnostics())
    except KeyboardInterrupt:
        print("\n🛑 [SRE] Sequence aborted by manual interrupt (IRQ).")
