import logging
import os
from datetime import UTC, datetime

import aiosqlite

from orchestrator.core.config import get_orchestrator_settings

logger = logging.getLogger("agentops.telemetry")

_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS execution_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    ticker TEXT NOT NULL,
    sentiment TEXT NOT NULL,
    impact_score REAL NOT NULL DEFAULT 0.0,
    stoch_confidence REAL NOT NULL DEFAULT 0.0,
    kelly_fraction REAL NOT NULL DEFAULT 0.0,
    allocation_usd REAL NOT NULL DEFAULT 0.0,
    action TEXT NOT NULL,
    reason TEXT,
    broker_order_id TEXT,
    latency_ms REAL,
    current_price REAL,
    vix_level REAL,
    market_regime TEXT,
    support REAL,
    resistance REAL,
    reward_risk_ratio REAL,
    meta_judge_verdict TEXT,
    meta_judge_confidence REAL,
    pnl_usd REAL DEFAULT NULL,
    exit_price REAL DEFAULT NULL,
    closed_at TEXT DEFAULT NULL,
    source TEXT DEFAULT 'UNKNOWN'
);
"""


async def init_telemetry_bus():
    """Low-level data bus formatting (BIOS)."""
    settings = get_orchestrator_settings()
    db_path = settings.telemetry_db_path
    try:
        os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
        async with aiosqlite.connect(db_path) as db:
            await db.execute(_CREATE_TABLE_SQL)

            # 🔧 SRE: Idempotent migration for new columns if the table already existed
            new_cols = [
                ("pnl_usd", "REAL DEFAULT NULL"),
                ("exit_price", "REAL DEFAULT NULL"),
                ("closed_at", "TEXT DEFAULT NULL"),
                ("source", "TEXT DEFAULT 'UNKNOWN'"),
                ("vix_level", "REAL"),
                ("market_regime", "TEXT"),
                ("meta_judge_verdict", "TEXT"),
                ("meta_judge_confidence", "REAL"),
            ]
            for name, dtype in new_cols:
                try:
                    await db.execute(f"ALTER TABLE execution_logs ADD COLUMN {name} {dtype}")
                except Exception:  # nosec B110
                    pass  # Column already exists

            await db.commit()
            logger.info(f"✅ [telemetry] Data bus synchronized at {db_path}")
    except Exception as e:
        logger.error(f"❌ [telemetry_fault] Hardware failure in DB: {e}")


async def log_execution(**kwargs):
    """Data injector into the Black Box."""
    settings = get_orchestrator_settings()
    db_path = settings.telemetry_db_path

    keys = kwargs.keys()
    columns = ", ".join(keys)
    placeholders = ", ".join([f":{k}" for k in keys])
    # Column names come from this module's own call sites (kwargs keys), never from
    # external/user input; values are bound via named placeholders.
    sql = f"INSERT INTO execution_logs ({columns}, timestamp) VALUES ({placeholders}, :ts)"  # nosec B608

    try:
        async with aiosqlite.connect(db_path) as db:
            data = {**kwargs, "ts": datetime.now(UTC).isoformat()}
            await db.execute(sql, data)
            await db.commit()
            logger.info(
                f"[telemetry] Decision Logged: {kwargs.get('ticker')} -> {kwargs.get('action')}"
            )
    except Exception as e:
        logger.error(f"💥 [telemetry_write_fault] Write error: {e}")


async def get_throughput_stats(hours: int = 4) -> dict:
    """
    Historical Read Probe: Calculates the volume of processed signals.
    Analogy: Reading the CPU cycle counter over the last time window.
    """
    from orchestrator.core.config import get_orchestrator_settings

    settings = get_orchestrator_settings()
    db_path = settings.telemetry_db_path

    try:
        if not os.path.exists(db_path):
            return {"total": 0, "executed": 0, "discarded": 0}

        async with aiosqlite.connect(db_path) as db:
            # Compute the cutoff timestamp (T - N hours)
            from datetime import timedelta

            cutoff = (datetime.now(UTC) - timedelta(hours=hours)).isoformat()

            # Query total volume and segmentation by action
            async with db.execute(
                "SELECT action, COUNT(*) FROM execution_logs WHERE timestamp > ? GROUP BY action",
                (cutoff,),
            ) as cursor:
                rows = await cursor.fetchall()

                stats = {"total": 0, "executed": 0, "discarded": 0}
                for action, count in rows:
                    stats["total"] += count
                    if action in ("LONG", "SHORT", "EXECUTED", "APPROVED"):
                        stats["executed"] += count
                    else:
                        stats["discarded"] += count

                return stats
    except Exception as e:
        logger.error(f"💥 [TELEMETRY_READ_FAULT] Failed to extract throughput: {e}")
        return {"total": 0, "error": str(e)}
