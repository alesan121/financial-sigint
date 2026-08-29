"""
workers/options_scout.py - Unusual Options Flow Sensor.

Hardware analogy: this is the system's 'ultra-high-frequency oscillator'.
The options market is the territory of institutional smart money.
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
# SRE CONFIGURATION (V4.0 Routing)
# =============================================================================

# 🔧 SRE FIX: Point directly at the FSM Bus
ORCHESTRATOR_URL: str = os.getenv("ORCHESTRATOR_URL", "http://orchestrator:8001/trigger")
DB_PATH: str = os.getenv("SCOUT_DB_PATH", "/data/scout_seen.db")

POLL_INTERVAL_SECONDS: int = int(os.getenv("OPTIONS_POLL_INTERVAL_SECONDS", "7200"))
MIN_VOLUME_OI_RATIO: float = float(os.getenv("OPTIONS_MIN_VOL_OI", "2.0"))
MIN_ABSOLUTE_VOLUME: int = int(os.getenv("OPTIONS_MIN_VOLUME", "500"))
MIN_PREMIUM_USD: float = float(os.getenv("OPTIONS_MIN_PREMIUM_USD", "50000.0"))

WATCHLIST: list[str] = [
    ticker.strip().upper()
    for ticker in os.getenv(
        "OPTIONS_WATCHLIST",
        "SPY,QQQ,AAPL,MSFT,NVDA,TSLA,AMZN,META,GOOGL,AMD,"
        "JPM,GS,XOM,GLD,USO,TLT,IWM,VIX,PLTR,NFLX",
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
        (h, src, datetime.now(UTC).isoformat()),
    )
    await conn.commit()


# =============================================================================
# ACQUISITION: Options Chain via yfinance
# =============================================================================


def _get_unusual_options(ticker: str) -> list[dict]:
    unusual: list[dict] = []
    try:
        t = yf.Ticker(ticker)
        expirations = t.options
        if not expirations:
            return unusual

        exp_date = expirations[0]
        chain = t.option_chain(exp_date)

        for option_type, df in [("CALL", chain.calls), ("PUT", chain.puts)]:
            if df is None or df.empty:
                continue

            mask = (
                (df["volume"] >= MIN_ABSOLUTE_VOLUME)
                & (df["openInterest"] > 0)
                & (df["volume"] / df["openInterest"] >= MIN_VOLUME_OI_RATIO)
            )
            active = df[mask].copy()

            if active.empty:
                continue

            for _, row in active.iterrows():
                volume = int(row.get("volume", 0) or 0)
                oi = int(row.get("openInterest", 0) or 0)
                ask = float(row.get("ask", 0) or 0)
                strike = float(row.get("strike", 0) or 0)
                impl_vol = float(row.get("impliedVolatility", 0) or 0)
                premium_usd = volume * ask * 100

                if premium_usd < MIN_PREMIUM_USD:
                    continue

                unusual.append(
                    {
                        "ticker": ticker,
                        "option_type": option_type,
                        "strike": strike,
                        "expiration": exp_date,
                        "volume": volume,
                        "open_interest": oi,
                        "vol_oi_ratio": volume / oi if oi > 0 else 0.0,
                        "ask": ask,
                        "premium_usd": premium_usd,
                        "impl_vol": impl_vol * 100,
                    }
                )

    except Exception as e:
        logger.debug("[OptionsScout][%s] Error getting options: %s", ticker, e)

    return unusual


def _get_current_price(ticker: str) -> float:
    """Extracts the current price in isolation."""
    try:
        return float(yf.Ticker(ticker).info.get("currentPrice", 0) or 0)
    except Exception:
        return 0.0


def _format_signal(activity: dict, current_price: float) -> tuple[str, str]:
    ticker = activity["ticker"]
    opt_type = activity["option_type"]
    strike = activity["strike"]
    exp = activity["expiration"]
    volume = activity["volume"]
    premium_k = activity["premium_usd"] / 1_000
    impl_vol = activity["impl_vol"]
    vol_oi = activity["vol_oi_ratio"]

    direction = "BULLISH" if opt_type == "CALL" else "BEARISH"
    interpretation = (
        f"The buyer is betting {ticker} will trade ABOVE ${strike:.0f} by {exp}."
        if opt_type == "CALL"
        else f"The buyer is betting {ticker} will trade BELOW ${strike:.0f} by {exp}."
    )

    moneyness = ""
    if current_price > 0:
        pct_away = ((strike - current_price) / current_price) * 100
        if opt_type == "CALL" and pct_away > 0:
            moneyness = f"(OTM by {pct_away:.1f}% — aggressive upside bet)"
        elif opt_type == "PUT" and pct_away < 0:
            moneyness = f"(OTM by {abs(pct_away):.1f}% — aggressive downside bet)"
        else:
            moneyness = f"({'ITM' if pct_away < 0 else 'ATM'} — near current price)"

    text = (
        f"UNUSUAL OPTIONS ACTIVITY [{direction}]: {ticker} options market shows abnormal flow. "
        f"Type: {opt_type} | Strike: ${strike:.0f} {moneyness} | Expiration: {exp} | "
        f"Volume: {volume:,} contracts | Volume/OI ratio: {vol_oi:.1f}x (normal = <1.0x). "
        f"Total premium deployed: ${premium_k:.0f}K | Implied Volatility: {impl_vol:.0f}%. "
        f"{interpretation} "
        f"This level of unusual options activity typically indicates institutional positioning "
        f"or informed money anticipating a significant price move in {ticker}. "
        f"Analyze whether this is a directional bet, a hedge, or a volatility play. "
        f"Consider if there are upcoming catalysts (earnings, FDA approval, product launch, "
        f"merger rumors) that could justify this position size."
    )

    today = datetime.now(UTC).strftime("%Y-%m-%d")
    h = hashlib.sha256(
        f"options:{ticker}:{opt_type}:{strike}:{exp}:{volume}:{today}".encode()
    ).hexdigest()
    return text, h


# =============================================================================
# DISPATCHER
# =============================================================================


async def _dispatch_to_fsm(
    client: httpx.AsyncClient, text: str, symbol: str, opt_type: str
) -> bool:
    payload = {
        "ticker": symbol,
        "text": text,
        "source": f"Unusual Options Flow — {symbol} {opt_type}",
    }
    for attempt in range(3):
        try:
            logger.info(
                "[OptionsScout][%s] ⚡ Injecting pulse into FSM Gateway (Attempt %d): %s",
                symbol,
                attempt + 1,
                ORCHESTRATOR_URL,
            )
            response = await client.post(
                ORCHESTRATOR_URL, json=payload, timeout=60.0
            )  # 🔌 SRE: Timeout extended to 60s
            response.raise_for_status()
            ack = response.json()
            logger.info(
                "[OptionsScout][%s] ✅ FSM Cycle Queued | thread_id=%s",
                symbol,
                ack.get("thread_id", "unknown"),
            )
            return True
        except Exception as e:
            logger.warning("[OptionsScout][%s] Retry %d failed: %s", symbol, attempt + 1, e)
            await asyncio.sleep(2**attempt)  # Exponential backoff

    logger.error(
        "[OptionsScout][%s] Critical bus error after 3 attempts. Signal discarded.", symbol
    )
    return False


# =============================================================================
# MAIN CYCLE
# =============================================================================


async def _scan_once(client: httpx.AsyncClient, conn: aiosqlite.Connection) -> None:
    all_unusual: list[dict] = []
    loop = asyncio.get_running_loop()  # 🔧 SRE FIX: Correct asynchronous clock

    for ticker in WATCHLIST:
        unusual_list = await loop.run_in_executor(None, _get_unusual_options, ticker)

        if unusual_list:
            logger.info(
                "[OptionsScout][%s] 🎯 %d unusual contracts detected", ticker, len(unusual_list)
            )
            # 🔧 SRE FIX: Safe execution of blocking IO
            current_price = await loop.run_in_executor(None, _get_current_price, ticker)

            for item in unusual_list:
                item["_ticker_price"] = current_price

            all_unusual.extend(unusual_list)
        await asyncio.sleep(0.5)

    all_unusual.sort(key=lambda x: x["premium_usd"], reverse=True)

    dispatched = 0
    for activity in all_unusual[:5]:
        current_price = activity.pop("_ticker_price", 0.0)
        text, h = _format_signal(activity, current_price)

        if await _is_seen(conn, h):
            continue

        success = await _dispatch_to_fsm(client, text, activity["ticker"], activity["option_type"])
        if success:
            await _mark_seen(conn, h, f"options:{activity['ticker']}")
            dispatched += 1

    logger.info(
        "[OptionsScout] Cycle completed | %d unusual positions | %d dispatched",
        len(all_unusual),
        dispatched,
    )


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
    )
    logger.info("=== SIGINT Options Scout v1.0 ===\nWatchlist: %d tickers", len(WATCHLIST))

    async with aiosqlite.connect(DB_PATH) as conn:
        await _ensure_db(conn)
        cycle = 0
        while True:
            cycle += 1
            logger.info("[OptionsScout] === Cycle #%d ===", cycle)
            async with httpx.AsyncClient(
                headers={"User-Agent": "SIGINT-OptionsScout/1.0"}, timeout=300.0
            ) as client:
                try:
                    await _scan_once(client, conn)
                except Exception as e:
                    logger.error("[OptionsScout] Unexpected error: %s", e, exc_info=True)

            logger.info("[OptionsScout] 💤 Next cycle in %ds", POLL_INTERVAL_SECONDS)
            await asyncio.sleep(POLL_INTERVAL_SECONDS)


if __name__ == "__main__":
    asyncio.run(main())
