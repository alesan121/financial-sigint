import sqlite3
import os

db_path = r"c:\Users\alesa\source\repos\sigint-financiero\data\telemetry\telemetry.db"
print(f"🔍 Opening EEPROM at: {db_path}")

if not os.path.exists(db_path):
    print("❌ ERROR: The database file does not exist.")
    exit(1)

try:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM execution_logs ORDER BY id DESC LIMIT 5")
    rows = cursor.fetchall()

    if not rows:
        print("📭 The execution_logs table is empty.")
    else:
        print("📜 Last records detected:")
        for row in rows:
            print(f"ID: {row['id']} | Ticker: {row['ticker']} | Action: {row['action']} | Reason: {row['reason']}")

    conn.close()
except Exception as e:
    print(f"💥 Error reading the DB: {e}")
