"""
SIGINT Factory Reset Tool v2.0.
Mission: Purge the telemetry logs and the persistence bus.
Analogy: Low-level format of the SRAM and the event logger.
"""
import sqlite3
import os
from pathlib import Path

# We locate the project root to make sure the memory sensor is detected
ROOT_DIR = Path(__file__).parent.parent
DB_PATH = ROOT_DIR / "data" / "telemetry" / "telemetry.db"
SCOUT_DB_PATH = ROOT_DIR / "data" / "scout" / "scout_seen.db"

def clear_telemetry():
    print(f"🔍 [SCAN] Looking for data hardware at: {DB_PATH}")

    # 🧹 Telemetry Purge
    if DB_PATH.exists():
        try:
            conn = sqlite3.connect(str(DB_PATH))
            cursor = conn.cursor()
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
            tables = cursor.fetchall()
            for table in tables:
                if not table[0].startswith("sqlite_"):
                    conn.execute(f"DELETE FROM {table[0]}")
            conn.commit()
            conn.close()
            print("✨ [SUCCESS] Telemetry Bus purged.")
        except Exception as e:
            print(f"💥 [ERROR] Failed purging Telemetry: {e}")

    # 🧹 Scouts Purge
    if SCOUT_DB_PATH.exists():
        try:
            conn = sqlite3.connect(str(SCOUT_DB_PATH))
            conn.execute("DELETE FROM seen_items")
            conn.commit()
            conn.close()
            print("✨ [SUCCESS] Scouts Bus (Seen Items) purged.")
        except Exception as e:
            print(f"💥 [ERROR] Failed purging Scouts: {e}")

if __name__ == "__main__":
    clear_telemetry()
