"""
workers/short_interest_scout.py - Short Interest Sensor and Short Squeeze Potential.

Hardware analogy: this module is an 'accumulated tension detector'.
When short interest (SI) is very high, the market is loaded like a spring.
A positive catalyst can trigger a 'short squeeze': short sellers close
positions by buying -> positive feedback loop -> the price spikes exponentially.

Historical examples:
  - GME (GameStop, 2021): SI of 140% -> 1,500% squeeze in days
  - AMC, BBBY, RIVN, etc.

Sensor strategy:
  1. Query the Short Interest ratio via yfinance (info["shortRatio"] / "shortPercentOfFloat")
  2. Filter by symptoms of an imminent squeeze:
     - shortPercentOfFloat > 15% (at least 15% of float sold short)
     - shortRatio > 5 (more than 5 days to cover the position at current volume)
     - Price in an UPTREND (price > SMA20) — the catalyst already exists
  3. Generate a "high short pressure" signal and dispatch it to Ingress

Data source: yfinance (free) — no API key required.
For high-precision data: FINRA short reports (free, weekly CSV).
"""

import asyncio
import hashlib
import logging
import os
from datetime import datetime, timezone

import aiosqlite
import httpx
import yfinance as yf

logger = logging.getLogger(__name__)

# =============================================================================
# CONFIGURATION
# =============================================================================

# 🔧 SRE FIX: Point directly at the Asynchronous FSM Bus
ORCHESTRATOR_URL: str = os.getenv("ORCHESTRATOR_URL", "http://orchestrator:8001/trigger")
DB_PATH: str          = os.getenv("SCOUT_DB_PATH", "/data/scout_seen.db")

# Weekly polling: FINRA publishes SI twice a month (1st and 3rd week)
POLL_INTERVAL_SECONDS: int = int(os.getenv("SHORT_POLL_INTERVAL_SECONDS", "86400"))  # 24h

# Squeeze thresholds
MIN_SHORT_FLOAT_PCT: float = float(os.getenv("SHORT_MIN_FLOAT_PCT", "15.0"))   # 15% of float
MIN_SHORT_RATIO:     float = float(os.getenv("SHORT_MIN_RATIO", "5.0"))        # 5 days to cover

# Broad watchlist: includes both mega-caps and small/mid-caps with historically high SI
WATCHLIST: list[str] = [
    ticker.strip().upper()
    for ticker in os.getenv(
        "SHORT_WATCHLIST",
        # Mega caps (by market volume)
        "NVDA,AAPL,MSFT,TSLA,AMZN,META,GOOGL,AMD,INTC,PLTR,"
        # Biotech and small caps (historically high SI)
        "MRNA,BNTX,RIVN,LCID,SOFI,UPST,AFRM,HOOD,WISH,"
        # Retail/meme sector
        "GME,AMC,BBBY,SPCE"
    ).split(",")
    if ticker.strip()
]

# =============================================================================
# DEDUPLICATOR
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
        (h, src, datetime.now(timezone.utc).isoformat()),
    )
    await conn.commit()

# =============================================================================
# ACQUISITION: Short Interest via yfinance
# =============================================================================

def _get_short_data(ticker: str) -> dict | None:
    """
    Gets short interest metrics from yfinance.
    Returns a dict with the relevant data or None on error.
    """
    try:
        info = yf.Ticker(ticker).info
        current_price   = float(info.get("currentPrice") or info.get("regularMarketPrice") or 0)
        short_pct_float = float(info.get("shortPercentOfFloat") or 0) * 100  # Convert to %
        short_ratio     = float(info.get("shortRatio") or 0)
        shares_short    = int(info.get("sharesShort") or 0)
        sma20           = float(info.get("fiftyDayAverage") or 0)   # Approximation using SMA50
        market_cap      = float(info.get("marketCap") or 0)
        company_name    = str(info.get("longName") or ticker)

        if current_price <= 0:
            return None

        return {
            "ticker":           ticker,
            "name":             company_name,
            "price":            current_price,
            "short_pct_float":  short_pct_float,
            "short_ratio":      short_ratio,
            "shares_short":     shares_short,
            "sma50":            sma20,
            "market_cap":       market_cap,
            "above_sma":        current_price > sma20 if sma20 > 0 else False,
        }
    except Exception as e:
        logger.debug("[ShortScout][%s] Error getting data: %s", ticker, e)
        return None


def _is_squeeze_candidate(data: dict) -> bool:
    """
    Evaluates whether a ticker has short squeeze candidate characteristics.

    Conditions (conjunction):
    1. Short % of Float > minimum threshold (a lot of money betting short)
    2. Short Ratio > minimum threshold (takes days to cover -> the squeeze is violent)

    Bonus condition (accelerator):
    3. Price above SMA50 (the market is already pressuring the shorts)
    """
    return (
        data["short_pct_float"] >= MIN_SHORT_FLOAT_PCT and
        data["short_ratio"]     >= MIN_SHORT_RATIO
    )


def _format_signal(data: dict) -> tuple[str, str]:
    """Formats the potential squeeze signal for the Ingress ADC."""
    ticker = data["ticker"]
    squeeze_score = (data["short_pct_float"] / 100) * (data["short_ratio"] / 10)
    catalyst_note = (
        "The stock is trading ABOVE its 50-day moving average, suggesting "
        "upward momentum that could accelerate the squeeze."
        if data["above_sma"]
        else "The stock is currently below its 50-day moving average. "
             "A bullish catalyst is needed to trigger the squeeze."
    )

    text = (
        f"SHORT SQUEEZE ALERT: {ticker} ({data['name']}) shows classic squeeze setup. "
        f"Short Interest: {data['short_pct_float']:.1f}% of float "
        f"({data['shares_short']:,} shares short). "
        f"Short Ratio: {data['short_ratio']:.1f} days to cover "
        f"(short sellers need {data['short_ratio']:.0f} days to unwind at current volume). "
        f"Current price: ${data['price']:.2f}. "
        f"{catalyst_note} "
        f"Squeeze score: {squeeze_score:.2f}. "
        f"If a positive catalyst emerges (earnings beat, buyout rumors, product launch), "
        f"short sellers will be forced to buy simultaneously, creating a violent upward spiral. "
        f"Analyze the setup and define entry/exit levels for {ticker}."
    )
    # Weekly hash: the same ticker isn't alerted twice in the same week
    week = datetime.now(timezone.utc).strftime("%Y-W%W")
    h = hashlib.sha256(f"short:{ticker}:{week}".encode()).hexdigest()
    return text, h

# =============================================================================
# MAIN CYCLE
# =============================================================================

async def _scan_once(client: httpx.AsyncClient, conn: aiosqlite.Connection) -> None:
    """Scans the watchlist looking for short squeeze candidates."""
    dispatched = 0
    squeeze_candidates: list[dict] = []
    loop = asyncio.get_running_loop()  # 🔧 SRE FIX: Modern clock

    for ticker in WATCHLIST:
        # 🔧 SRE FIX: Use running_loop to avoid DeprecationWarnings
        data = await loop.run_in_executor(None, _get_short_data, ticker)
        if data and _is_squeeze_candidate(data):
            squeeze_candidates.append(data)
            logger.info("[ShortScout] 🎯 SQUEEZE CANDIDATE: %s", ticker)
        await asyncio.sleep(0.5)

    # Sort by squeeze score descending (most dangerous first)
    squeeze_candidates.sort(
        key=lambda d: (d["short_pct_float"] / 100) * (d["short_ratio"] / 10),
        reverse=True,
    )

    for data in squeeze_candidates[:5]:
        text, h = _format_signal(data)
        if await _is_seen(conn, h):
            continue

        try:
            # 🔧 SRE FIX: Direct wiring to the FSM Gateway (v4.0)
            payload = {
                "ticker": data['ticker'],
                "text": text,
                "source": f"Short Interest Alert — {data['ticker']}"
            }
            resp = await client.post(ORCHESTRATOR_URL, json=payload, timeout=10.0)
            resp.raise_for_status()

            ack = resp.json()
            logger.info(f"[ShortScout][{data['ticker']}] ✅ FSM Cycle Queued | thread={ack.get('thread_id')}")
            await _mark_seen(conn, h, f"short:{data['ticker']}")
            dispatched += 1
        except Exception as e:
            logger.error(f"[ShortScout][{data['ticker']}] 🔥 BUS ERROR: {e}")

    logger.info(
        "[ShortScout] Cycle completed | %d candidates detected | %d dispatched",
        len(squeeze_candidates), dispatched,
    )


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    )
    logger.info(
        "=== SIGINT Short Interest Scout v1.0 ===\n"
        "Watchlist: %d tickers | Min SI: %.0f%% float | Min ratio: %.0f days",
        len(WATCHLIST), MIN_SHORT_FLOAT_PCT, MIN_SHORT_RATIO,
    )

    async with aiosqlite.connect(DB_PATH) as conn:
        await _ensure_db(conn)
        async with httpx.AsyncClient(headers={"User-Agent": "SIGINT-ShortScout/1.0"}) as client:
            cycle = 0
            while True:
                cycle += 1
                logger.info("[ShortScout] === Cycle #%d ===", cycle)
                try:
                    await _scan_once(client, conn)
                except Exception as e:
                    logger.error("[ShortScout] Unexpected error: %s", e, exc_info=True)
                logger.info("[ShortScout] 💤 Next cycle in %ds (%dh)", POLL_INTERVAL_SECONDS, POLL_INTERVAL_SECONDS // 3600)
                await asyncio.sleep(POLL_INTERVAL_SECONDS)


if __name__ == "__main__":
    asyncio.run(main())
