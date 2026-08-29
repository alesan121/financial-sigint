"""
orchestrator/nodes/technical.py - Technical Impedance Analysis Node.
DEFCON 1 Version: v4.2 - Static Bus Isolation and Retry Protection.

Hardware analogy: we have replaced the temporary capacitor with a
fixed galvanic isolator. If the sensor detects 'noise' (DB lock),
the circuit automatically retries the read before aborting.
"""

import logging
import time
import asyncio
import os
from datetime import datetime, timezone

import pandas as pd
import yfinance as yf

# 🔧 SRE FIX: Static but container-isolated cache location
# We avoid creating/deleting folders on every cycle to prevent Race Conditions.
CACHE_PATH = "/tmp/yf_sigint_cache"
os.makedirs(CACHE_PATH, exist_ok=True)
yf.set_tz_cache_location(CACHE_PATH)

from orchestrator.state import TradingState
from orchestrator.core.telemetry import node_telemetry as trace_node
from orchestrator.workers.adaptive_pop import get_recommended_pop
from orchestrator.core.normalizer import normalize_ticker

logger = logging.getLogger(__name__)

# --- ROUTING ROM CONFIGURATION (MACRO PROXIES) ---
_MACRO_PROXY = {
    "MACRO:FED": "QQQ", "MACRO:GDP": "SPY", "MACRO:ECONOMY": "SPY",
    "MACRO:OIL": "USO", "MACRO:GOLD": "GLD", "MACRO:BOND": "TLT",
    "MACRO:TECH": "XLK", "MACRO:BITCOIN": "IBIT"
}

def _compute_kalman_filter(prices: pd.Series) -> float:
    if prices.empty: return 0.0
    x_hat, p = prices.iloc[0], 1.0
    q, r = 1e-5, 0.01
    for z in prices:
        x_hat_minus = x_hat
        p_minus = p + q
        k = p_minus / (p_minus + r)
        x_hat = x_hat_minus + k * (z - x_hat_minus)
        p = (1 - k) * p_minus
    return float(x_hat)

def _detect_market_regime(vix: float, spy_price: float, spy_sma50: float) -> str:
    if vix >= 25.0: return "RISK_OFF_VOLATILE"
    if spy_price < spy_sma50: return "RISK_OFF_BEAR"
    return "RISK_ON"

@trace_node("Technical_TA")
async def node_technical_analysis(state: TradingState) -> dict:
    ts = datetime.now(timezone.utc).isoformat()
    t0 = time.perf_counter()
    loop = asyncio.get_running_loop()

    signal = state.get("ingress_signal", {})
    raw_ticker = signal.get("ticker", "ERROR")
    ticker = normalize_ticker(raw_ticker)
    
    if ticker == "UNKNOWN" or ticker == "ERROR":
         return {"risk_quant": {"routing_flag": "REJECTED", "discard_reason": f"Invalid Ticker: {raw_ticker}"}}

    yf_ticker = _MACRO_PROXY.get(ticker, ticker)
    tickers_to_poll = f"{yf_ticker} SPY ^VIX"

    # ── DATA ACQUISITION WITH RETRY CIRCUIT (RETRY LOGIC) ──
    df = None
    max_retries = 3
    for attempt in range(max_retries):
        try:
            df = await loop.run_in_executor(
                None, 
                lambda: yf.download(tickers_to_poll, period="60d", interval="1d", progress=False, auto_adjust=True)
            )
            if df is not None and not df.empty:
                break # Successful read
        except Exception as e:
            if "locked" in str(e).lower() and attempt < max_retries - 1:
                logger.warning(f"⚠️ [IO_JITTER] DB locked. Retry {attempt+1}/{max_retries}...")
                await asyncio.sleep(1 * (attempt + 1)) # Simple exponential backoff
                continue
            logger.error(f"🚨 [IO_FAULT] Critical sensor failure for {yf_ticker}: {e}")
            return {"logs": [f"[{ts}][node_technical] Network error: {str(e)}"]}

    if df is None or df.empty:
        return {"logs": ["Empty Dataset"]}

    # Signal Conditioning (Multi-Index Normalization)
    try:
        df_main = df.xs(yf_ticker, level=1, axis=1).dropna()
        df_spy = df.xs("SPY", level=1, axis=1).dropna()
        df_vix = df.xs("^VIX", level=1, axis=1).dropna()
    except Exception:
        if isinstance(df.columns, pd.MultiIndex):
             df_main = df.xs(yf_ticker, level=1, axis=1).dropna() if yf_ticker in df.columns.levels[1] else pd.DataFrame()
             df_spy = df.xs("SPY", level=1, axis=1).dropna() if "SPY" in df.columns.levels[1] else pd.DataFrame()
             df_vix = df.xs("^VIX", level=1, axis=1).dropna() if "^VIX" in df.columns.levels[1] else pd.DataFrame()
        else:
            df_main = df; df_spy = pd.DataFrame(); df_vix = pd.DataFrame()

    if df_main.empty: return {"logs": [f"[{ts}][node_technical] Empty main dataset for {yf_ticker}"]}

    # --- ELECTRICAL MEASUREMENTS ---
    current_price = float(df_main["Close"].dropna().iloc[-1])
    high, low, prev_close = df_main["High"], df_main["Low"], df_main["Close"].shift(1)
    tr = pd.concat([high-low, (high-prev_close).abs(), (low-prev_close).abs()], axis=1).max(axis=1)
    atr = float(tr.rolling(14).mean().iloc[-1]) if len(tr) >= 14 else 0.0

    support = float(df_main["Low"].tail(20).min())
    resistance = float(df_main["High"].tail(20).max())
    
    if support == resistance:
        support = current_price - (atr * 2.0) if atr > 0 else current_price * 0.98
        resistance = current_price + (atr * 2.0) if atr > 0 else current_price * 1.02

    kalman = _compute_kalman_filter(df_main["Close"])
    dyn_pop, agc_audit = await get_recommended_pop()
    
    vix = float(df_vix["Close"].iloc[-1]) if not df_vix.empty else 15.0
    spy_p = float(df_spy["Close"].iloc[-1]) if not df_spy.empty else 500.0
    spy_ma50 = float(df_spy["Close"].rolling(50).mean().iloc[-1]) if len(df_spy) >= 50 else spy_p

    regime = _detect_market_regime(vix, spy_p, spy_ma50)
    elapsed = (time.perf_counter() - t0) * 1000

    return {
        "current_price": round(current_price, 2),
        "sma_20": round(float(df_main["Close"].rolling(20).mean().iloc[-1]), 2),
        "atr_14": round(atr, 2),
        "support": round(support, 2),
        "resistance": round(resistance, 2),
        "kalman_price": round(kalman, 2),
        "market_regime": regime,
        "vix_level": round(vix, 2),
        "dynamic_pop_threshold": round(dyn_pop, 4),
        "logs": [
            f"📊 [DMM] {yf_ticker} measured at ${current_price:.2f} | Regime: {regime} | Latency: {elapsed:.0f}ms",
            f"🔄 [AGC] {agc_audit}"
        ]
    }