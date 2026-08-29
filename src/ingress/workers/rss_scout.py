"""
src/ingress/workers/rss_scout.py - RSS Signal Sensor (Field Antenna).
DEFCON 1 Version: Direct Injection, State Persistence, and Squelch Filter.

Hardware analogy: This is an RF transducer that scans the news spectrum.
It uses a persistence capacitor (SQLite) to avoid re-triggering on
old signals and a band filter (Squelch) to clean out non-financial noise.
"""

import asyncio
import hashlib
import logging
import os
import sys
from datetime import UTC, datetime
from typing import NamedTuple

import aiosqlite
import feedparser
import httpx

# 📡 SRE FIX: Unified telemetry log bus
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger("scout.rss")

# --- PIN CONFIGURATION (Registers) ---
INGRESS_URL = os.getenv("INGRESS_URL", "http://orchestrator:8001/trigger")
DB_PATH = os.getenv("SCOUT_DB_PATH", "/data/scout_seen.db")
POLL_INTERVAL = int(os.getenv("POLL_INTERVAL_SECONDS", "300"))

# Frequency array (Feeds) for maximum coverage
_RSS_FEEDS = [
    ("Yahoo Finance", os.getenv("RSS_FEED_URL", "https://finance.yahoo.com/news/rssindex")),
    ("Reuters Biz", "https://feeds.reuters.com/reuters/businessNews"),
    ("MarketWatch", "https://feeds.content.dowjones.io/public/rss/mw_realtimeheadlines"),
]

# Squelch Filter: Keywords that open the gate
_FINANCIAL_KEYWORDS = frozenset(
    {
        "nasdaq",
        "s&p",
        "fed",
        "fomc",
        "interest rate",
        "inflation",
        "cpi",
        "gdp",
        "earnings",
        "stock",
        "shares",
        "nvidia",
        "apple",
        "tesla",
        "ai",
    }
)


class NewsArticle(NamedTuple):
    title: str
    summary: str
    link: str
    published: str
    content_hash: str


# ─── COMPONENT: PERSISTENT DEDUPLICATOR (SRAM) ─────────────────────────────


class Deduplicator:
    """
    Data Capacitor: Prevents re-processing signals already digitized.
    Stores the hashes in SQLite to survive Pod restarts.
    """

    def __init__(self, db_path: str):
        self.db_path = db_path
        self.db = None

    async def initialize(self):
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self.db = await aiosqlite.connect(self.db_path)
        await self.db.execute(
            """
            CREATE TABLE IF NOT EXISTS processed_articles (
                content_hash TEXT PRIMARY KEY,
                processed_at TEXT NOT NULL,
                title        TEXT
            )
        """
        )
        # 🔧 SRE: Idempotent migration for the 'title' column if the table already existed
        try:
            await self.db.execute("ALTER TABLE processed_articles ADD COLUMN title TEXT")
        except Exception:  # nosec B110
            pass  # The column already exists

        await self.db.commit()

    async def is_seen(self, content_hash: str) -> bool:
        async with self.db.execute(
            "SELECT 1 FROM processed_articles WHERE content_hash = ?", (content_hash,)
        ) as cursor:
            return await cursor.fetchone() is not None

    async def mark_seen(self, content_hash: str, title: str = ""):
        # 🛡️ SRE FIX: Ensure 3-column parity (hash, date, title)
        await self.db.execute(
            "INSERT OR IGNORE INTO processed_articles (content_hash, processed_at, title) VALUES (?, ?, ?)",
            (content_hash, datetime.now(UTC).isoformat(), str(title)),
        )
        await self.db.commit()

    async def close(self):
        if self.db:
            await self.db.close()


# ─── SIGNAL PROCESSING (Logic Gates) ───────────────────────────────────────


def passes_squelch(title: str, summary: str) -> bool:
    """Band Filter: Does the signal contain financial components?"""
    text = (title + " " + summary).lower()
    return any(kw in text for kw in _FINANCIAL_KEYWORDS)


async def process_feed(
    feed_name: str, feed_url: str, dedup: Deduplicator, client: httpx.AsyncClient
):
    """Sampling of a specific frequency."""
    try:
        # 📥 Analog signal capture (I/O-intensive work moved to thread pool)
        loop = asyncio.get_event_loop()
        feed = await loop.run_in_executor(None, feedparser.parse, feed_url)

        for entry in feed.entries:
            title = entry.get("title", "").strip()
            summary = entry.get("summary", entry.get("description", "")).strip()
            link = entry.get("link", "")

            if not title or not link:
                continue

            # Generate the signal's digital fingerprint
            content_hash = hashlib.md5(
                f"{title}|{link}".encode(), usedforsecurity=False
            ).hexdigest()

            # 🛡️ Control Logic: Squelch + Dedup
            if not passes_squelch(title, summary):
                continue
            if await dedup.is_seen(content_hash):
                continue

            # 🔌 Packet Digitization
            payload = {
                "text": f"{title}. {summary}",
                "source": f"RSS_{feed_name}",
                "thread_id": f"RSS-{datetime.now().strftime('%H%M%S')}",
            }

            # 🚀 Injection into the Main Bus (Orchestrator)
            resp = await client.post(INGRESS_URL, json=payload, timeout=10.0)

            if resp.status_code in (200, 202):
                logger.info(f"✅ [{feed_name}] Signal injected: {title[:60]}...")
                await dedup.mark_seen(content_hash, title)
            else:
                logger.error(f"🚨 [Bus_Fault] Rejected with {resp.status_code} for {title[:30]}")

    except Exception as e:
        logger.error(f"💥 [Sensor_Fault] Failure in {feed_name}: {e}")


# ─── EXECUTION CYCLE (Main Loop) ───────────────────────────────────────────


async def run_scout():
    """Startup of the monitoring system."""
    logger.info(f"🛰️ [RSS_Scout] Starting monitoring on {len(_RSS_FEEDS)} frequencies.")

    dedup = Deduplicator(DB_PATH)
    await dedup.initialize()

    try:
        while True:
            # 🔌 SRE FIX: HTTP client with a managed connection pool
            async with httpx.AsyncClient(timeout=30.0) as client:
                tasks = [process_feed(name, url, dedup, client) for name, url in _RSS_FEEDS]
                await asyncio.gather(*tasks)

            logger.info(f"💤 Cycle completed. Standing by for {POLL_INTERVAL}s...")
            await asyncio.sleep(POLL_INTERVAL)
    finally:
        await dedup.close()


if __name__ == "__main__":
    try:
        asyncio.run(run_scout())
    except KeyboardInterrupt:
        logger.info("🛑 Emergency shutdown requested by operator.")
