"""
workers/earnings_scout.py - Earnings Catalyst Sensor.

Hardware analogy: this module is a 'predictable energy burst detector'.
Earnings are pre-scheduled high-volatility events — like a capacitor
charging up to release an energy pulse on a known date.
"""

import asyncio
import hashlib
import logging
import os
from datetime import UTC, datetime

import aiosqlite
import httpx
import yfinance as yf

logger = logging.getLogger(__name__)

# =============================================================================
# SRE CONFIGURATION (Aligned with FSM Gateway v4.0)
# =============================================================================

# 🔧 SRE FIX: Point to the Orchestrator's new asynchronous bus
INGRESS_URL: str = os.getenv("INGRESS_URL", "http://orchestrator:8001/trigger")
DB_PATH: str = os.getenv("SCOUT_DB_PATH", "/data/scout_seen.db")

POLL_INTERVAL_SECONDS: int = int(os.getenv("EARNINGS_POLL_INTERVAL_SECONDS", "21600"))
PRE_EARNINGS_DAYS: int = int(os.getenv("EARNINGS_PRE_DAYS", "1"))

WATCHLIST: list[str] = [
    ticker.strip().upper()
    for ticker in os.getenv(
        "EARNINGS_WATCHLIST",
        "AAPL,MSFT,NVDA,GOOGL,AMZN,META,TSLA,AMD,INTC,JPM,GS,BAC,"
        "JNJ,PFE,LLY,XOM,CVX,WMT,HD,NKE,NFLX,CRM,PLTR",
    ).split(",")
    if ticker.strip()
]

HTTP_TIMEOUT: float = 60.0

# =============================================================================
# DEDUPLICATOR (Historical SRAM)
# =============================================================================


async def _ensure_db(conn: aiosqlite.Connection) -> None:
    await conn.execute(
        "CREATE TABLE IF NOT EXISTS seen_signals "
        "(id INTEGER PRIMARY KEY, hash TEXT UNIQUE, source TEXT, seen_at TEXT)"
    )
    await conn.commit()


async def _is_seen(conn: aiosqlite.Connection, h: str) -> bool:
    async with conn.execute("SELECT 1 FROM seen_signals WHERE hash=?", (h,)) as c:
        return await c.fetchone() is not None


async def _mark_seen(conn: aiosqlite.Connection, h: str, src: str) -> None:
    await conn.execute(
        "INSERT OR IGNORE INTO seen_signals (hash, source, seen_at) VALUES (?,?,?)",
        (h, src, datetime.now(UTC).isoformat()),
    )
    await conn.commit()


# =============================================================================
# ACQUISITION: Earnings Calendar (yfinance)
# =============================================================================


def _get_earnings_date(ticker: str) -> datetime | None:
    try:
        info = yf.Ticker(ticker).info
        ed = info.get("earningsDate") or info.get("earningsTimestamp")
        if ed is None:
            return None
        if isinstance(ed, list | tuple) and len(ed) > 0:
            ed = ed[0]
        if isinstance(ed, int | float):
            return datetime.fromtimestamp(ed, tz=UTC)
        return None
    except Exception:
        return None


def _get_last_earnings_surprise(ticker: str) -> dict:
    try:
        t = yf.Ticker(ticker)
        hist = t.earnings_history
        if hist is None or hist.empty:
            return {}
        latest = hist.iloc[-1]
        return {
            "eps_actual": float(latest.get("epsActual", 0) or 0),
            "eps_estimate": float(latest.get("epsEstimate", 0) or 0),
            "surprise_pct": float(latest.get("epsDifference", 0) or 0),
            "period": str(latest.name) if latest.name else "N/A",
        }
    except Exception:
        return {}


# =============================================================================
# SIGNAL GENERATOR
# =============================================================================


def _format_pre_earnings_signal(ticker: str, earnings_date: datetime) -> tuple[str, str]:
    days_left = (earnings_date.date() - datetime.now(UTC).date()).days
    date_str = earnings_date.strftime("%Y-%m-%d")

    text = (
        f"EARNINGS ALERT: {ticker} is scheduled to report quarterly earnings in "
        f"{days_left} day(s), on {date_str}. "
        f"This is a high-volatility catalyst event. Implied volatility (IV) typically "
        f"expands significantly before earnings and collapses after (IV crush). "
        f"Institutional traders are positioning now. "
        f"Analyze the risk/reward for {ticker} ahead of this binary event: "
        f"if the company beats estimates, the stock could rally 5-15%. "
        f"If it misses, it could drop 10-20%. "
        f"Consider the current market sentiment, sector momentum, and recent guidance."
    )
    h = hashlib.sha256(f"pre_earnings:{ticker}:{date_str}".encode()).hexdigest()
    return text, h


def _format_post_earnings_signal(ticker: str, surprise: dict) -> tuple[str, str]:
    eps_actual = surprise.get("eps_actual", 0)
    eps_estimate = surprise.get("eps_estimate", 0)
    surprise_pct = surprise.get("surprise_pct", 0)
    period = surprise.get("period", "Q")

    beat_miss = "BEAT" if eps_actual >= eps_estimate else "MISSED"
    direction = "surpassed" if beat_miss == "BEAT" else "fell short of"

    text = (
        f"EARNINGS RESULT: {ticker} just reported {period} earnings. "
        f"EPS actual: ${eps_actual:.2f} vs estimate: ${eps_estimate:.2f}. "
        f"The company {direction} analyst expectations by {abs(surprise_pct):.1f}% ({beat_miss}). "
        f"This is a post-earnings momentum signal. After a significant beat or miss, "
        f"institutional algorithms rebalance positions in the first 30-60 minutes. "
        f"Analyze if this earnings {beat_miss} represents a sustainable trend change "
        f"or a short-term overreaction to be faded."
    )
    date_key = datetime.now(UTC).strftime("%Y-%m-%d")
    h = hashlib.sha256(f"post_earnings:{ticker}:{date_key}:{eps_actual}".encode()).hexdigest()
    return text, h


# =============================================================================
# DISPATCHER
# =============================================================================


async def _dispatch(client: httpx.AsyncClient, text: str, source: str, ticker: str) -> bool:
    try:
        r = await client.post(
            INGRESS_URL, json={"ticker": ticker, "text": text, "source": source}, timeout=30.0
        )
        r.raise_for_status()
        sig = r.json()

        # 🔧 SRE FIX: Adapted to the FSM Gateway's asynchronous response
        thread_id = sig.get("thread_id", "unknown")
        logger.info(f"[EarningsScout] ✅ Dispatched | Bus={INGRESS_URL} | Thread={thread_id}")
        return True
    except Exception as e:
        logger.error(f"[EarningsScout] ❌ Dispatch error: {e}")
        return False


# =============================================================================
# MAIN CYCLE
# =============================================================================


async def _scan_once(client: httpx.AsyncClient, conn: aiosqlite.Connection) -> None:
    now = datetime.now(UTC)
    dispatched = 0
    loop = asyncio.get_running_loop()  # 🔧 SRE FIX: Avoids deprecation warning

    for ticker in WATCHLIST:
        try:
            # ── Pre-Earnings ──────────────────────
            earnings_dt = await loop.run_in_executor(None, _get_earnings_date, ticker)
            if earnings_dt:
                days_away = (earnings_dt.date() - now.date()).days
                if 0 <= days_away <= PRE_EARNINGS_DAYS:
                    text, h = _format_pre_earnings_signal(ticker, earnings_dt)
                    if not await _is_seen(conn, h):
                        logger.info(
                            "[EarningsScout] 📅 Pre-earnings: %s in %d days", ticker, days_away
                        )
                    if await _dispatch(client, text, f"Earnings Calendar — {ticker}", ticker):
                        await _mark_seen(conn, h, f"earnings_pre:{ticker}")
                        dispatched += 1

            # ── Post-Earnings ────────────────────────
            surprise = await loop.run_in_executor(None, _get_last_earnings_surprise, ticker)
            if surprise and abs(surprise.get("surprise_pct", 0)) >= 3.0:
                text, h = _format_post_earnings_signal(ticker, surprise)
                if not await _is_seen(conn, h):
                    logger.info(
                        "[EarningsScout] 📊 Post-earnings surprise: %s | %.1f%% %s",
                        ticker,
                        surprise["surprise_pct"],
                        "BEAT" if surprise["eps_actual"] >= surprise["eps_estimate"] else "MISS",
                    )
                    if await _dispatch(client, text, f"Earnings Surprise — {ticker}", ticker):
                        await _mark_seen(conn, h, f"earnings_post:{ticker}")
                        dispatched += 1

        except Exception as e:
            logger.warning("[EarningsScout][%s] Error in cycle: %s", ticker, e)

        await asyncio.sleep(0.5)

    logger.info("[EarningsScout] Cycle completed | %d signals dispatched", dispatched)


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    )
    logger.info(
        "=== SIGINT Earnings Scout v1.0 ===\n"
        "Watchlist: %d tickers | Pre-alert: %d day(s) | Interval: %ds",
        len(WATCHLIST),
        PRE_EARNINGS_DAYS,
        POLL_INTERVAL_SECONDS,
    )

    async with aiosqlite.connect(DB_PATH) as conn:
        await _ensure_db(conn)
        async with httpx.AsyncClient(headers={"User-Agent": "SIGINT-EarningsScout/1.0"}) as client:
            cycle = 0
            while True:
                cycle += 1
                logger.info("[EarningsScout] === Cycle #%d ===", cycle)
                try:
                    await _scan_once(client, conn)
                except Exception as e:
                    logger.error("[EarningsScout] Unexpected error: %s", e, exc_info=True)
                logger.info("[EarningsScout] 💤 Next cycle in %ds", POLL_INTERVAL_SECONDS)
                await asyncio.sleep(POLL_INTERVAL_SECONDS)


if __name__ == "__main__":
    asyncio.run(main())
