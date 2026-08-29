"""
workers/fred_scout.py - Macroeconomic Sensor (Federal Reserve FRED API).

Hardware analogy: this module is the SIGINT system's 'low-frequency spectrum
analyzer'. Macro data (CPI, NFP, GDP) are low-frequency signals
(monthly/quarterly) but with the greatest systemic impact of all the sensors.

A CPI reading that beats consensus expectations can move the ENTIRE market:
  - Stocks: fall (rates will rise -> multiples compress)
  - Bonds: fall (yields rise)
  - Gold: rises (inflation hedge)
  - Dollar: rises (higher rates attract capital)

The FRED API is FREE (Federal Reserve Bank of St. Louis). Free plan: 120 calls/min.
Sign up: https://fred.stlouisfed.org/docs/api/api_key.html

Monitored macro series:
  - CPIAUCSL: CPI (headline inflation)
  - CPILFESL: Core CPI (ex energy/food)
  - PAYEMS:   Nonfarm Payrolls (NFP)
  - FEDFUNDS: Fed Funds Rate
  - M2SL:     M2 money supply
  - UNRATE:   Unemployment rate
  - GDP:      Quarterly GDP
  - T10Y2Y:   10Y-2Y yield curve spread (recession indicator)
  - BAMLH0A0HYM2: High Yield OAS Spread (credit risk)
"""

import asyncio
import hashlib
import logging
import os
from datetime import datetime, timezone

import aiosqlite
import httpx

logger = logging.getLogger(__name__)

# =============================================================================
# CONFIGURATION
# =============================================================================

INGRESS_URL: str = os.getenv("INGRESS_URL", "http://ingress:8000/analyze")
DB_PATH: str     = os.getenv("SCOUT_DB_PATH", "/data/scout_seen.db")
FRED_API_KEY: str = os.getenv("FRED_API_KEY", "")
FRED_BASE_URL: str = "https://api.stlouisfed.org/fred/series/observations"
ORCHESTRATOR_URL: str = os.getenv("ORCHESTRATOR_URL", "http://orchestrator:8001/trigger")

# Polling: every 4 hours (FRED data updates when published, not every minute)
POLL_INTERVAL_SECONDS: int = int(os.getenv("FRED_POLL_INTERVAL_SECONDS", "14400"))

HTTP_TIMEOUT: float = 30.0

# =============================================================================
# MACRO SERIES TO MONITOR
# Format: (series_id, readable_name, unit, etf_impact, description)
# =============================================================================
_MACRO_SERIES: list[tuple[str, str, str, str, str]] = [
    (
        "CPIAUCSL",
        "Consumer Price Index (CPI — Inflation)",
        "% YoY",
        "TIP,TLT,GLD",
        "Primary inflation indicator. High CPI → Fed hikes → stocks/bonds fall, USD/gold rise.",
    ),
    (
        "CPILFESL",
        "Core CPI (ex Food & Energy)",
        "% YoY",
        "TLT,QQQ",
        "Fed's preferred inflation gauge. Core CPI drives rate decisions more than headline CPI.",
    ),
    (
        "PAYEMS",
        "Nonfarm Payrolls (NFP)",
        "K jobs",
        "SPY,TLT",
        "Monthly job creation. Strong NFP → economy healthy → Fed may hike. Biggest monthly macro event.",
    ),
    (
        "FEDFUNDS",
        "Federal Funds Rate",
        "%",
        "TLT,QQQ,GLD",
        "The most important rate in the world. Fed Funds Rate changes propagate through ALL asset classes.",
    ),
    (
        "M2SL",
        "M2 Money Supply",
        "$ Billions",
        "GLD,IBIT",
        "Broad money supply. M2 contraction = deflation risk. M2 expansion = inflation/asset bubble risk.",
    ),
    (
        "UNRATE",
        "Unemployment Rate",
        "%",
        "SPY,XLY",
        "Labor market health indicator. Low unemployment → wage inflation → Fed hawkish.",
    ),
    (
        "GDP",
        "Gross Domestic Product",
        "$ Billions (annualized)",
        "SPY,EEM",
        "Quarterly GDP. Two consecutive negative quarters = technical recession. Massive market impact.",
    ),
    (
        "T10Y2Y",
        "10-Year minus 2-Year Treasury Yield Spread",
        "% spread",
        "SHY,TLT,XLF",
        "Yield curve inversion indicator. Negative spread historically predicts recession in 6-18 months.",
    ),
    (
        "BAMLH0A0HYM2",
        "High Yield OAS Spread",
        "basis points",
        "HYG,LQD",
        "Credit risk gauge. Widening spreads signal stress in corporate debt markets.",
    ),
    (
        "DCOILWTICO",
        "WTI Crude Oil Price",
        "$ per barrel",
        "USO,XLE",
        "Oil price from FRED. Correlated with inflation, geopolitics, and global growth expectations.",
    ),
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
        row = await c.fetchone()
        return row is not None


async def _mark_seen(conn: aiosqlite.Connection, h: str, src: str) -> None:
    await conn.execute(
        "INSERT OR IGNORE INTO seen_signals (hash, source, seen_at) VALUES (?,?,?)",
        (h, src, datetime.now(timezone.utc).isoformat()),
    )
    await conn.commit()

# =============================================================================
# ACQUISITION: FRED API
# =============================================================================

async def _fetch_latest_fred_value(
    client: httpx.AsyncClient, series_id: str
) -> tuple[str, float] | None:
    """
    Gets the latest published value of a FRED series.
    Returns (publication_date, value) or None on error.

    The FRED API requires an API key or allows limited anonymous access.
    """
    params: dict = {
        "series_id":       series_id,
        "sort_order":      "desc",
        "limit":           "2",          # Last 2 values to calculate the delta
        "file_type":       "json",
        "observation_start": "2020-01-01",
    }
    if FRED_API_KEY:
        params["api_key"] = FRED_API_KEY

    try:
        resp = await client.get(FRED_BASE_URL, params=params, timeout=HTTP_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        observations = data.get("observations", [])
        if len(observations) < 1:
            return None
        # The most recent is the first one (desc order)
        latest = observations[0]
        val = float(latest["value"]) if latest["value"] not in (".", "") else None
        if val is None:
            return None
        return (latest["date"], val)
    except Exception as e:
        logger.debug("[FREDScout][%s] Error fetching: %s", series_id, e)
        return None


def _format_macro_signal(
    series_id: str,
    name: str,
    unit: str,
    etfs: str,
    description: str,
    date: str,
    value: float,
) -> tuple[str, str]:
    """Formats the macro data as analog text for the ADC."""
    text = (
        f"MACRO DATA RELEASE — FEDERAL RESERVE (FRED): {name}\n"
        f"Latest published value: {value:.3f} {unit} (released {date})\n"
        f"Context: {description}\n"
        f"Most affected assets: {etfs}\n"
        f"Analyze the investment implications of this macroeconomic data point. "
        f"Consider the direction of the trend compared to previous readings, "
        f"whether this data is likely to influence Federal Reserve policy decisions, "
        f"and which specific ETFs or sectors will be most directly impacted. "
        f"Be precise about whether this is bullish or bearish for the listed assets."
    )
    h = hashlib.sha256(f"fred:{series_id}:{date}:{value}".encode()).hexdigest()
    return text, h

# =============================================================================
# MAIN CYCLE
# =============================================================================

async def _scan_once(client: httpx.AsyncClient, conn: aiosqlite.Connection) -> None:
    """Queries all FRED series and dispatches new readings."""
    dispatched = 0

    for series_id, name, unit, etfs, description in _MACRO_SERIES:
        result = await _fetch_latest_fred_value(client, series_id)
        if result is None:
            logger.debug("[FREDScout][%s] No data available", series_id)
            continue

        date, value = result
        text, h = _format_macro_signal(series_id, name, unit, etfs, description, date, value)

        if await _is_seen(conn, h):
            logger.debug("[FREDScout][%s] Already processed (%s)", series_id, date)
            continue

        logger.info(
            "[FREDScout] 📊 NEW DATA: %s | %s: %.3f %s → Dispatching...",
            series_id, date, value, unit,
        )

        try:
            resp = await client.post(
                INGRESS_URL,
                json={
                    "ticker": f"MACRO:{series_id}",
                    "text": text, 
                    "source": f"FRED / Federal Reserve — {series_id}"
                },
                timeout=1200.0,
            )
            resp.raise_for_status()
            sig = resp.json()
            await _mark_seen(conn, h, f"fred:{series_id}")
            dispatched += 1
            
            # ─── END-TO-END LOOP: Invoke DSP Orchestrator ────────────────────
            # Structured Bus injection (avoids re-inference)
            try:
                logger.info("[FREDScout] ⚡ Invoking DSP Orchestrator for %s...", name)
                trigger_payload = {
                     "ingress_signal": sig,
                     "news_text": text
                }
                trigger_res = await client.post(ORCHESTRATOR_URL, json=trigger_payload, timeout=15.0)
                trigger_res.raise_for_status()
                logger.info("[FREDScout] ✅ FSM Cycle Queued.")
            except Exception as e:
                logger.error("[FREDScout] 🔥 DSP ERROR: Failed to communicate with Orchestrator: %s", e)

        except Exception as e:
            logger.error("[FREDScout][%s] ❌ Dispatch error: %s", series_id, e)

        # FRED rate limit: 120 calls/min → wait at least 0.5s between calls
        await asyncio.sleep(0.6)

    logger.info("[FREDScout] Cycle completed | %d new data points dispatched", dispatched)


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    )
    logger.info(
        "=== SIGINT FRED Scout v1.0 ===\n"
        "Series: %d | API Key: %s | Interval: %ds",
        len(_MACRO_SERIES),
        "✅ Configured" if FRED_API_KEY else "⚠️ Anonymous (limited)",
        POLL_INTERVAL_SECONDS,
    )
    if not FRED_API_KEY:
        logger.warning(
            "FRED_API_KEY not configured. The API works without a key but with lower limits.\n"
            "Free sign-up: https://fred.stlouisfed.org/docs/api/api_key.html"
        )

    async with aiosqlite.connect(DB_PATH) as conn:
        await _ensure_db(conn)
        cycle = 0
        while True:
            cycle += 1
            logger.info("[FREDScout] === Cycle #%d ===", cycle)

            # 🔌 SRE FIX: Fresh HTTP client on each cycle (avoids TCP RST)
            async with httpx.AsyncClient(headers={"User-Agent": "SIGINT-FREDScout/1.0"}, timeout=300.0) as client:
                try:
                    await _scan_once(client, conn)
                except Exception as e:
                    logger.error("[FREDScout] Unexpected error: %s", e, exc_info=True)

            logger.info("[FREDScout] 💤 Next cycle in %ds (%dh)", POLL_INTERVAL_SECONDS, POLL_INTERVAL_SECONDS // 3600)
            await asyncio.sleep(POLL_INTERVAL_SECONDS)


if __name__ == "__main__":
    asyncio.run(main())
