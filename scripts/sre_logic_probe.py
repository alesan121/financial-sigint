"""
scripts/sre_logic_probe.py - Logic Analyzer Probe v7.8.
Mission: Deep Forensic, Differential and Phase Latency Analysis.
Version v7.8: "Quantum Telemetry" - Adds Clock Drift and Command Report.

SRE Analogy: We've added a 'Time-Domain Reflectometer' (TDR) to measure
exactly how long the signal takes to travel from the antenna to the relay.
"""
import sqlite3
import os
import json
import subprocess
from datetime import datetime, timezone

# --- PIN CONFIGURATION (MEMORY REGISTERS) ---
DB_PATH = "data/telemetry/telemetry.db"

def get_bus_impedance():
    """
    Network telemetry probe: measures buffer pressure over the T-5min window.
    """
    try:
        cmd = 'docker compose logs orchestrator --since 5m'
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            shell=True,
            encoding='utf-8',
            errors='replace'
        )

        if result.returncode != 0:
            return {"error": "Bus unreachable"}

        logs = result.stdout if result.stdout else ""
        in_pulses = logs.count("Signal detected")
        out_cycles = logs.count("FSM Cycle closed")
        backlog = in_pulses - out_cycles

        return {
            "Ingress_5m": in_pulses,
            "Egress_5m": out_cycles,
            "Backlog": backlog,
            "Status": "NOMINAL" if backlog <= 0 else "CONGESTIONED"
        }
    except Exception as e:
        return {"error": f"Sensing Fault: {str(e)}"}

def format_latency(ms: float) -> str:
    """Auto-scales the unit of measurement for response time."""
    if ms >= 1000:
        return f"{ms/1000:.2f} s (Deep Inference)"
    return f"{ms:.1f} ms (Bypass Mode)"

def calculate_drift(ts_str: str) -> str:
    """Measures the difference between the record's timestamp and real time."""
    try:
        dt_event = datetime.fromisoformat(ts_str.replace('Z', '+00:00'))
        now = datetime.now(timezone.utc)
        drift = (now - dt_event).total_seconds()
        return f"{drift:.1f} s (Real-time lag)"
    except:
        return "N/A"

def get_latest_flight_recorder():
    print(f"🔍 [SCAN] Starting Sweep v7.8 (Quantum Telemetry Active)...")

    if not os.path.exists(DB_PATH):
        print(f"❌ [ERROR] Data bus not detected at {DB_PATH}")
        return

    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        # 🧪 DUAL-CHANNEL READ: Extract the last 2 records
        cursor.execute("SELECT * FROM execution_logs ORDER BY id DESC LIMIT 2")
        rows = cursor.fetchall()

        if not rows:
            print("📭 [INFO] Flight recorder is empty.")
            conn.close()
            return

        curr = dict(rows[0])
        prev = dict(rows[1]) if len(rows) > 1 else None
        conn.close()

        # --- DELTA CALCULATION (Voltage Differential) ---
        vix_delta = 0.0
        if prev:
            vix_delta = curr.get('vix_level', 0) - prev.get('vix_level', 0)

        # Network metrics acquisition
        bus_stats = get_bus_impedance()

        # --- FORENSIC REPORT ASSEMBLY v7.8 ---
        print("\n" + "═"*75)
        print(f"🚀 BLACK BOX DUMP - ID: {curr.get('id')} | {curr.get('timestamp')}")
        print("═"*75)

        report = {
            "SIGNAL_IDENTITY": {
                "Ticker": curr.get('ticker', 'N/A'),
                "Sentiment": curr.get('sentiment', 'N/A').upper(),
                "Source": curr.get('source', 'N/A')
            },
            "DIFFERENTIAL_ANALYSIS": {
                "VIX_Current": curr.get('vix_level', 0.0),
                "VIX_Trend": f"{vix_delta:+.2f} (CoolingDown)" if vix_delta < 0 else f"{vix_delta:+.2f} (HeatingUp)" if vix_delta > 0 else "STABLE",
                "Price_Current": f"${curr.get('current_price', 0.0):.2f}",
            },
            "QUANTUM_DATA (Risk DSP)": {
                "Confidence": f"{curr.get('stoch_confidence', 0.0)*100:.1f}%",
                "Impact_Score": curr.get('impact_score', 0.0),
                "RR_Ratio": curr.get('reward_risk_ratio', 0.0),
                "Kelly_Fraction": f"{curr.get('kelly_fraction', 0.0)*100:.2f}%"
            },
            "COMMAND_REPORT": {
                "Action": curr.get('action', 'N/A'),
                "Allocation": f"${curr.get('allocation_usd', 0.0):.2f}",
                "Veto_Active": "YES" if "Vetoed" in str(curr.get('reason', '')) else "NO",
                "Final_Reason": curr.get('reason', 'N/A')
            },
            "BUS_INFRASTRUCTURE": bus_stats,
            "TEMPORAL_METRICS": {
                "Propagation_Latency": format_latency(curr.get('latency_ms', 0.0)),
                "Clock_Drift": calculate_drift(curr.get('timestamp')),
                "Bus_Pressure": "Nominal" if bus_stats.get("Backlog", 0) <= 0 else "High"
            }
        }

        print(json.dumps(report, indent=2, ensure_ascii=False))
        print("═"*75)

        # SRE Alerts
        if bus_stats.get("Backlog", 0) > 3:
            print(f"⚠️ [WARNING] Backpressure detected.")

        print("\n👉 COPY THIS BLOCK AND SEND IT TO YOUR GEMINI PARTNER FOR CERTIFICATION.")

    except Exception as e:
        print(f"💥 [PROBE_FAULT] Short circuit in the probe: {e}")

if __name__ == "__main__":
    get_latest_flight_recorder()
