"""
workers/insider_scout.py - Smart Money Sensor: Insider Trading Tracker (SEC Form 4).

Hardware analogy: this module is a highly selective 'narrowband signal receiver'.
The RSS Scout captures a broad news spectrum (wideband signal).
This worker is a directional Yagi antenna aimed straight at the SEC/EDGAR channel:
it captures only one very specific signal type: open-market purchases by C-Level executives.

When a CEO/CFO/Director buys millions of dollars of their own stock
on the open market, they are putting real money where their conviction is.
That is the highest-SNR (Signal-to-Noise Ratio) signal available in the markets.

Architecture:
    - Data source: Finnhub API (GET /stock/insider-transactions) — free with an API key.
    - High-conviction filter: transactionCode == 'P' + value > $250,000.
    - Deduplicator: SQLite shared with rss-scout (same /data volume).
    - Dispatcher: POST http://ingress:8000/analyze (same ADC as rss-scout).

REQUIRED VARIABLE: FINNHUB_API_KEY (add to .env and .env.example)
"""

import asyncio
import hashlib
import logging
import os
from datetime import UTC, datetime

import aiosqlite
import httpx

logger = logging.getLogger(__name__)

# =============================================================================
# CONFIGURATION
# =============================================================================

# Finnhub API Key (https://finnhub.io — free plan allows 60 calls/min)
FINNHUB_API_KEY: str = os.getenv("FINNHUB_API_KEY", "")

# Finnhub's Form 4 endpoint
FINNHUB_INSIDER_URL: str = "https://finnhub.io/api/v1/stock/insider-transactions"

# List of tickers to watch. If empty, uses the global endpoint with an empty symbol.
# You can expand this list with the companies you're most interested in monitoring.
WATCHLIST: list[str] = [
    ticker.strip().upper()
    for ticker in os.getenv("INSIDER_WATCHLIST", "NVDA,AAPL,MSFT,AMZN,TSLA,AMD,META").split(",")
    if ticker.strip()
]

# Polling interval in seconds (15 minutes: insiders don't move the market every second)
POLL_INTERVAL_SECONDS: int = int(os.getenv("INSIDER_POLL_INTERVAL_SECONDS", "900"))

# SQLite file path for deduplication (shared with rss-scout at /data)
DB_PATH: str = os.getenv("SCOUT_DB_PATH", "/data/scout_seen.db")

# Minimum purchase threshold to filter out stock option/compensation noise (USD)
MIN_TRANSACTION_VALUE_USD: float = float(os.getenv("INSIDER_MIN_VALUE_USD", "250000"))

# Timeout per external API call (in seconds)
HTTP_TIMEOUT: float = 30.0

# =============================================================================
# DEDUPLICATOR (SQLite shared with rss-scout)
# =============================================================================


async def _ensure_db(conn: aiosqlite.Connection) -> None:
    """
    Creates the seen-signals table if it doesn't exist.
    Reuses the same table as rss-scout for a single global deduplicator.
    """
    await conn.execute(
        """
        CREATE TABLE IF NOT EXISTS seen_signals (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            hash      TEXT NOT NULL UNIQUE,
            source    TEXT,
            seen_at   TEXT NOT NULL
        )
        """
    )
    await conn.commit()


async def _is_seen(conn: aiosqlite.Connection, signal_hash: str) -> bool:
    """Returns True if the signal was already processed previously."""
    async with conn.execute("SELECT 1 FROM seen_signals WHERE hash = ?", (signal_hash,)) as cur:
        return await cur.fetchone() is not None


async def _mark_seen(conn: aiosqlite.Connection, signal_hash: str, source: str) -> None:
    """Records the signal as processed."""
    await conn.execute(
        "INSERT OR IGNORE INTO seen_signals (hash, source, seen_at) VALUES (?, ?, ?)",
        (signal_hash, source, datetime.now(UTC).isoformat()),
    )
    await conn.commit()


# =============================================================================
# ACQUISITION LAYER (Finnhub API)
# =============================================================================


async def _fetch_insider_transactions(client: httpx.AsyncClient, symbol: str) -> list[dict]:
    """
    Queries Finnhub's Insider Trading endpoint for a ticker.

    Documentation: https://finnhub.io/docs/api/insider-transactions
    Response: {"data": [{...}, ...], "symbol": "NVDA"}

    Args:
        client: Reusable HTTP client (persistent connection).
        symbol: NASDAQ ticker (e.g.: "NVDA").

    Returns:
        List of raw transactions from the API.
    """
    if not FINNHUB_API_KEY:
        logger.warning("[InsiderScout] FINNHUB_API_KEY not configured. Using MOCK data.")
        return _mock_insider_transactions(symbol)

    try:
        resp = await client.get(
            FINNHUB_INSIDER_URL,
            params={"symbol": symbol, "token": FINNHUB_API_KEY},
            timeout=HTTP_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("data", []) or []
    except httpx.HTTPStatusError as e:
        logger.error(
            "[InsiderScout][%s] Finnhub HTTP error: %s %s", symbol, e.response.status_code, e
        )
        return []
    except Exception as e:
        logger.error("[InsiderScout][%s] Error querying Finnhub: %s", symbol, e)
        return []


def _mock_insider_transactions(symbol: str) -> list[dict]:
    """
    MOCK data for development and testing without an API key.
    Simulates a massive high-conviction CEO purchase.

    Analogy: a 'test signal generator' used to calibrate
    the pipeline without needing a real source.
    """
    return [
        {
            "name": "John Smith",
            "change": 50000,
            "share": 250_000,
            "transactionCode": "P",  # 'P' = open-market Purchase
            "transactionPrice": 450.0,
            "value": 22_500_000.0,  # $22.5M massive purchase
            "filingDate": datetime.now(UTC).strftime("%Y-%m-%d"),
            "symbol": symbol,
        }
    ]


# =============================================================================
# HIGH-CONVICTION FILTER
# =============================================================================


def _is_high_conviction_purchase(tx: dict) -> bool:
    """
    High-conviction signal filter for insider trading.

    ALL conditions must be met:
    1. transactionCode == 'P': open-market purchase (not options, not a 10b5-1 plan)
    2. value > MIN_TRANSACTION_VALUE_USD: enough magnitude to be a real signal

    We exclude:
    - 'A': Award / grant of free shares (costs the insider nothing)
    - 'F': Tax withholding (forced sale to pay taxes)
    - 'S': Open sale (bearish on the company itself)
    - 'G', 'I', 'J'...: Internal transfers, estate planning

    Analogy: this is the receiver's 'signal discriminator'. It only lets through
    the exact modulation code we're looking for.
    """
    is_purchase = tx.get("transactionCode") == "P"
    value = float(tx.get("value") or 0.0)
    is_large = value >= MIN_TRANSACTION_VALUE_USD
    return is_purchase and is_large


# =============================================================================
# SIGNAL FORMATTER (Analog text for the ADC/Ingress)
# =============================================================================


def _format_signal(tx: dict) -> tuple[str, str]:
    """
    Converts an insider transaction into analog text for Ingress.

    The text must be informative enough for the LLM (ADC)
    to extract the context without additional data.

    Returns:
        Tuple (text: str, signal_hash: str) — the news text and a unique hash.
    """
    name = tx.get("name", "Unknown Insider")
    symbol = tx.get("symbol", "?")
    value = float(tx.get("value") or 0.0)
    price = float(tx.get("transactionPrice") or 0.0)
    shares = int(tx.get("change") or tx.get("share") or 0)
    date = tx.get("filingDate", "unknown date")

    value_m = value / 1_000_000  # Convert to millions for readability

    text = (
        f"URGENT SEC FORM 4: {name}, a C-Level executive at {symbol}, "
        f"just purchased {shares:,} shares of {symbol} stock in the open market "
        f"at ${price:.2f} per share, totaling ${value_m:.1f}M. "
        f"Filing date: {date}. "
        f"This is a large open-market purchase, considered a strong bullish conviction signal. "
        f"Insiders rarely buy large amounts of their own stock unless they believe the price "
        f"will increase significantly. Analyze the investment implications for {symbol}."
    )

    # Unique hash: symbol + date + value → avoids resending if the same Form 4 shows up across multiple polls
    raw_id = f"{symbol}:{date}:{value:.0f}"
    signal_hash = hashlib.sha256(raw_id.encode()).hexdigest()

    return text, signal_hash


# =============================================================================
# DISPATCHER (POST → Ingress ADC)
# =============================================================================

# =============================================================================
# DISPATCHER (POST → FSM Gateway)
# =============================================================================


async def _dispatch_to_ingress(
    client: httpx.AsyncClient,
    text: str,
    symbol: str,
) -> bool:
    """
    Sends the insider trading signal directly to the Asynchronous FSM Gateway (v4.0).
    We cut the wire to the legacy Ingress to avoid double thermal inference.
    """
    # 🔧 SRE FIX: Direct routing to the v4.0 bus
    ingress_url = os.getenv("INGRESS_URL", "http://orchestrator:8001/trigger")

    payload = {
        "ticker": symbol,
        "text": text,
        "source": f"SEC Form 4 / Insider Trading — {symbol}",
    }

    try:
        logger.info(
            "[InsiderScout][%s] ⚡ Injecting pulse into FSM Gateway: %s", symbol, ingress_url
        )
        # Short timeout (10s) because the v4.0 Gateway only enqueues in memory and responds fast
        response = await client.post(
            ingress_url,
            json=payload,
            timeout=10.0,
        )
        response.raise_for_status()

        # Read the ack from the APIC
        ack = response.json()
        logger.info(
            "[InsiderScout][%s] ✅ FSM Cycle Queued | thread_id=%s",
            symbol,
            ack.get("thread_id", "unknown"),
        )
        return True

    except httpx.TimeoutException:
        logger.error("[InsiderScout][%s] ⏱️ Timeout connecting to the FSM Gateway.", symbol)
    except httpx.HTTPStatusError as e:
        logger.error(
            "[InsiderScout][%s] HTTP %s from the FSM Gateway: %s", symbol, e.response.status_code, e
        )
    except Exception as e:
        logger.error("[InsiderScout][%s] Critical bus error towards the FSM Gateway: %s", symbol, e)

    return False


# =============================================================================
# MAIN POLLING CYCLE
# =============================================================================


async def _scan_once(client: httpx.AsyncClient, conn: aiosqlite.Connection) -> dict:
    """
    A complete monitoring cycle: queries every ticker in the watchlist,
    filters and dispatches the high-conviction signals.

    Returns:
        Dictionary of cycle metrics for logging.
    """
    total_transactions = 0
    high_conviction = 0
    already_seen = 0
    dispatched = 0
    errors = 0

    for symbol in WATCHLIST:
        logger.debug("[InsiderScout] Querying Form 4: %s", symbol)
        transactions = await _fetch_insider_transactions(client, symbol)
        total_transactions += len(transactions)

        for tx in transactions:
            # Ensure the symbol is available in the transaction dict
            tx.setdefault("symbol", symbol)

            if not _is_high_conviction_purchase(tx):
                continue

            high_conviction += 1
            text, signal_hash = _format_signal(tx)

            # Deduplication
            if await _is_seen(conn, signal_hash):
                already_seen += 1
                logger.debug("[InsiderScout][%s] Signal already processed (dup). Skipping.", symbol)
                continue

            # Dispatch to the ADC (Ingress)
            value_m = float(tx.get("value") or 0.0) / 1_000_000
            logger.info(
                "[InsiderScout][%s] 🎯 INSIDER PURCHASE DETECTED | %s | $%.1fM | Dispatching to Ingress...",
                symbol,
                tx.get("name", "Unknown"),
                value_m,
            )
            success = await _dispatch_to_ingress(client, text, symbol)

            if success:
                await _mark_seen(conn, signal_hash, f"insider:{symbol}")
                dispatched += 1
            else:
                errors += 1

        # Small pause between tickers to avoid saturating the API (rate limit: 60 calls/min)
        await asyncio.sleep(1.0)

    return {
        "total_transactions": total_transactions,
        "high_conviction": high_conviction,
        "already_seen": already_seen,
        "dispatched": dispatched,
        "errors": errors,
    }


# =============================================================================
# MAIN ENTRYPOINT
# =============================================================================


async def main() -> None:
    """
    Infinite loop of the Insider Trading sensor.

    Cycle:
    1. Connect to the deduplication SQLite database.
    2. Poll Finnhub for each ticker in the watchlist.
    3. Filter: purchase + value > MIN.
    4. Deduplicate: ignore signals already sent.
    5. Dispatch: POST to Ingress.
    6. Sleep POLL_INTERVAL_SECONDS and go back to 2.
    """
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )

    logger.info(
        "=== SIGINT Insider Scout v1.0 starting ===\n"
        "Watchlist: %s | MinValue: $%s | Interval: %ss | DB: %s",
        ", ".join(WATCHLIST),
        f"{MIN_TRANSACTION_VALUE_USD:,.0f}",
        POLL_INTERVAL_SECONDS,
        DB_PATH,
    )

    if not FINNHUB_API_KEY:
        logger.warning(
            "⚠️  FINNHUB_API_KEY not configured. Using MOCK data for testing.\n"
            "Add FINNHUB_API_KEY=<your_key> to .env (https://finnhub.io — free plan available)."
        )

    async with aiosqlite.connect(DB_PATH) as conn:
        await _ensure_db(conn)

        async with httpx.AsyncClient(
            headers={"User-Agent": "SIGINT-InsiderScout/1.0"},
            timeout=HTTP_TIMEOUT,
        ) as http_client:

            cycle = 0
            while True:
                cycle += 1
                ts = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S")
                logger.info("[InsiderScout] === Cycle #%d started (%s) ===", cycle, ts)

                try:
                    metrics = await _scan_once(http_client, conn)
                    logger.info(
                        "[InsiderScout] Cycle #%d completed | "
                        "total_tx=%d | conviction=%d | seen=%d | dispatched=%d | errors=%d",
                        cycle,
                        metrics["total_transactions"],
                        metrics["high_conviction"],
                        metrics["already_seen"],
                        metrics["dispatched"],
                        metrics["errors"],
                    )
                except Exception as e:
                    logger.error(
                        "[InsiderScout] Unexpected error in cycle #%d: %s", cycle, e, exc_info=True
                    )

                logger.info(
                    "[InsiderScout] 💤 Next poll in %d seconds (%d min). Waiting...",
                    POLL_INTERVAL_SECONDS,
                    POLL_INTERVAL_SECONDS // 60,
                )
                await asyncio.sleep(POLL_INTERVAL_SECONDS)


if __name__ == "__main__":
    asyncio.run(main())
