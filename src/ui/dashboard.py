"""
src/ui/dashboard.py - SIGINT Control Panel v7.1.
Supreme Supervision Interface for the Site Reliability Engineer (SRE).

Hardware analogy: This is the rack's 'Front Panel'. It includes the
real-time oscilloscope (Control Plane) and the power counters (Data Plane).
"""

import os
import time
from datetime import UTC, datetime

import httpx
import streamlit as st

# --- PAGE CONFIGURATION (Registers) ---
st.set_page_config(
    page_title="SIGINT Dashboard v7.1",
    page_icon="🛰️",
    layout="wide",
    initial_sidebar_state="expanded",
)

# --- INDUSTRIAL-GRADE STYLES (CSS) ---
st.markdown(
    """
<style>
    .stMetric {
        background: #1e2130;
        padding: 15px;
        border-radius: 10px;
        border-left: 5px solid #4CAF50;
    }
    .stProgress .st-bo {
        background-color: #4CAF50;
    }
    /* Style to highlight high load */
    .css-1wivap2 {
        background-color: #262730;
    }
</style>
""",
    unsafe_allow_html=True,
)

# --- ADDRESS REGISTERS ---
ORCHESTRATOR_URL = os.getenv("ORCHESTRATOR_URL", "http://orchestrator:8001")


def render_system_telemetry():
    """Renders the supreme telemetry section."""
    st.title("🛰️ SIGINT Dashboard | Supreme Supervision")
    st.markdown(
        "**Node Status:** `Cloud-Native Cluster` | **Uptime:** `Operational` | **C2 Link:** `Telegram Online`"
    )
    st.markdown("---")

    try:
        # 🔌 Diagnostic Probe: Query the APIC (Orchestrator)
        with httpx.Client(timeout=2.0) as client:
            resp = client.get(f"{ORCHESTRATOR_URL}/inspect")
            resp.raise_for_status()
            data = resp.json()

        # --- CHANNEL 1: PROCESSOR STATE (Control Plane) ---
        st.subheader("🛠️ Control Plane (System State)")
        col1, col2, col3, col4 = st.columns(4)

        status_val = data.get("engine_status", "UNKNOWN")
        status_color = "🟢" if status_val == "CRUISING" else "🔴"
        col1.metric("Engine Status", f"{status_color} {status_val}")

        # Read the real queue depth (Backpressure)
        # waiting_in_queue now only shows those NOT on the CPU
        metrics = data.get("realtime_metrics", {})
        q_depth = metrics.get("waiting_in_queue", 0)

        q_label = "Nominal" if q_depth == 0 else "Backpressure" if q_depth < 5 else "Congestioned"
        col2.metric(
            "Queue (Waiting)",
            f"{q_depth} signals",
            q_label,
            delta_color="inverse" if q_depth > 0 else "normal",
        )

        # SRAM memory state (Deduplication)
        cache_count = data.get("cache_entries_count", 0)
        col3.metric("Squelch Cache", f"{cache_count} items", "Active (5m TTL)")

        # System Clock Synchronization (Drift)
        server_time_str = data.get("server_time")
        if server_time_str:
            # Sanitize the timestamp for FROMISOFORMAT (Z/UTC compatibility)
            server_ts = datetime.fromisoformat(server_time_str.replace("Z", "+00:00"))
            drift = (datetime.now(UTC) - server_ts).total_seconds()
            col4.metric(
                "Clock Drift", f"{abs(drift):.2f}s", "In-Sync" if abs(drift) < 2 else "Lagging"
            )

        # --- CHANNEL 2: HISTORICAL THROUGHPUT (Data Plane) ---
        st.markdown("---")
        st.subheader("📊 Data Plane (Throughput last 4 hours)")

        stats = data.get("historical_throughput_4h", {"total": 0, "executed": 0, "discarded": 0})
        total = stats.get("total", 0)
        executed = stats.get("executed", 0)
        discarded = stats.get("discarded", 0)

        t1, t2, t3 = st.columns(3)
        t1.metric("Total Pulses Ingested", f"{total}")
        t2.metric("Orders Executed / Approved", f"{executed} ✅")
        t3.metric("Noise Filtered / Discarded", f"{discarded} 🗑️")

        # Signal Efficiency Calculation (SNR - Signal to Noise Ratio)
        st.markdown("### 🧬 Signal-to-Trade Efficiency (SNR)")
        if total > 0:
            efficiency = (executed / total) * 100
            # Dynamic progress bar (SRE-Spec: width="stretch" via CSS)
            st.progress(min(1.0, efficiency / 100))
            st.write(
                f"Inference Bus Efficiency: **{efficiency:.1f}%** (Conversion of noise into actionable signals)"
            )
        else:
            st.info("Waiting for the first data pulse to compute the bus SNR...")

    except Exception as e:
        st.error(f"🚨 Error connecting to the Telemetry Bus: {e}")
        if st.button("🔄 Force Probe Reconnection"):
            st.rerun()


# --- ENTRY POINT ---
if __name__ == "__main__":
    render_system_telemetry()

    # Automatic refresh injection every 30 seconds (Polling Loop)
    time.sleep(30)
    st.rerun()
