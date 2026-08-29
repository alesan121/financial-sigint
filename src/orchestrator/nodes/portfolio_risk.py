"""
orchestrator/nodes/portfolio_risk.py - Systemic Protections Panel.
DEFCON 1 Version: Bus Alignment v4.0 and Protected Async I/O.
"""

import asyncio
import logging
import os
from datetime import UTC, datetime

import joblib
import pandas as pd
import yfinance as yf

from orchestrator.core.config import get_orchestrator_settings
from orchestrator.core.telemetry import node_telemetry as trace_node
from orchestrator.state import TradingState

logger = logging.getLogger(__name__)

# ─── MODEL LOADING (ML Firmware) ───
# 🔧 SRE FIX: K8s/Docker Volume-compatible path resolution
MODEL_DIR = os.getenv(
    "MODEL_STORAGE_PATH", os.path.join(os.path.dirname(__file__), "../../meta_model")
)
_MODEL_PATH = os.path.join(MODEL_DIR, "meta_model.pkl")

_meta_model = None
if os.path.exists(_MODEL_PATH):
    try:
        _meta_model = joblib.load(_MODEL_PATH)
        logger.info(f"✅ [portfolio_risk] ML Firmware loaded from {_MODEL_PATH}")
    except Exception as e:
        logger.error(f"❌ [ML_FAULT] Error loading Meta-Model: {e}")

# =============================================================================
# GUARD CONFIGURATION (adjustable via .env without recompiling)
# =============================================================================
MAX_OPEN_POSITIONS = int(os.getenv("RISK_MAX_POSITIONS", "5"))
SECTOR_EXPOSURE_CAP = float(os.getenv("RISK_SECTOR_CAP", "0.30"))
MAX_DRAWDOWN_PCT = float(os.getenv("RISK_MAX_DRAWDOWN_PCT", "0.10"))
MAX_CORRELATION = float(os.getenv("RISK_MAX_CORRELATION", "0.85"))
CORRELATION_LOOKBACK_DAYS = int(os.getenv("RISK_CORR_LOOKBACK_DAYS", "252"))

_SECTOR_MAP = {
    "AAPL": "TECH",
    "MSFT": "TECH",
    "NVDA": "TECH",
    "AMD": "TECH",
    "INTC": "TECH",
    "GOOGL": "TECH",
    "GOOG": "TECH",
    "META": "TECH",
    "AVGO": "TECH",
    "QCOM": "TECH",
    "XLK": "TECH",
    "AMZN": "CONSUMER",
    "TSLA": "CONSUMER",
    "HD": "CONSUMER",
    "XLY": "CONSUMER",
    "JPM": "FINANCIALS",
    "GS": "FINANCIALS",
    "BAC": "FINANCIALS",
    "XLF": "FINANCIALS",
    "XLE": "ENERGY",
    "XOM": "ENERGY",
    "CVX": "ENERGY",
    "XLV": "HEALTH",
    "JNJ": "HEALTH",
    "PFE": "HEALTH",
    "SPY": "BROAD",
    "QQQ": "BROAD",
}


def _get_sector(ticker: str) -> str:
    clean = ticker.replace("MACRO:", "").upper()
    return _SECTOR_MAP.get(clean, "UNKNOWN")


def _check_max_positions(positions: list) -> tuple[bool, str]:
    n = len(positions)
    if n >= MAX_OPEN_POSITIONS:
        return False, f"MAX_POSITIONS reached: {n} >= {MAX_OPEN_POSITIONS}"
    return True, f"OK: {n}/{MAX_OPEN_POSITIONS} positions"


def _check_sector_exposure(
    ticker: str, alloc_usd: float, positions: list, equity: float
) -> tuple[bool, str]:
    if equity <= 0:
        return True, "SKIP: Equity is 0"
    sector = _get_sector(ticker)
    if sector == "UNKNOWN":
        return True, f"OK: Unknown sector for {ticker}"

    current_sector_usd = sum(
        abs(float(getattr(p, "market_value", 0) or 0))
        for p in positions
        if _get_sector(getattr(p, "symbol", "")) == sector
    )

    projected_pct = (current_sector_usd + alloc_usd) / equity
    if projected_pct > SECTOR_EXPOSURE_CAP:
        return (
            False,
            f"SECTOR_CAP: {sector} exp {projected_pct*100:.1f}% > {SECTOR_EXPOSURE_CAP*100:.0f}%",
        )
    return True, f"OK: {sector} exp {projected_pct*100:.1f}%"


def _check_drawdown_killswitch(equity: float, peak: float) -> tuple[bool, str]:
    if peak <= 0:
        return True, "SKIP: No peak data"
    dd = (peak - equity) / peak
    if dd >= MAX_DRAWDOWN_PCT:
        return False, f"KILL-SWITCH: Drawdown {dd*100:.1f}% >= {MAX_DRAWDOWN_PCT*100:.0f}%"
    return True, f"OK: Drawdown {dd*100:.1f}%"


def _check_correlation(new_ticker: str, open_tickers: list[str]) -> tuple[bool, str]:
    if not open_tickers:
        return True, "OK: No open positions"
    try:
        all_t = list(set([new_ticker] + open_tickers))
        df = yf.download(
            all_t, period=f"{CORRELATION_LOOKBACK_DAYS}d", progress=False, auto_adjust=True
        )["Close"]
        if df.empty or new_ticker not in df.columns:
            return True, "SKIP: No YF data"

        returns = df.pct_change().dropna()
        high_corr = [
            (t, float(returns[new_ticker].corr(returns[t])))
            for t in open_tickers
            if t in returns.columns and returns[new_ticker].corr(returns[t]) >= MAX_CORRELATION
        ]

        if high_corr:
            return (
                False,
                f"CORRELATION: {new_ticker} redundant with {high_corr[0][0]} ({high_corr[0][1]:.2f})",
            )
        return True, "OK: Diversified"
    except Exception as e:
        return True, f"SKIP: Corr error {e}"


def _check_meta_labeling(state: TradingState) -> tuple[bool, str]:
    if _meta_model is None:
        return True, "SKIP: No ML model"
    try:
        # 🔧 SRE FIX: Read from the v4.0 bus
        signal = state.get("ingress_signal", {})
        risk_q = state.get("risk_quant", {})

        features = {
            "vix_level": float(risk_q.get("vix_level", 20.0)),
            "atr_pct": 0.02,  # Placeholder to simplify the bus
            "kalman_diff_pct": 0.0,
            "pop_score": float(risk_q.get("pop_score", 0.5)),
            "stoch_confidence": float(signal.get("stoch_confidence", 0.5)),
            "impact_score": float(signal.get("impact_score", 0.0)),
            "reward_risk_ratio": float(risk_q.get("reward_risk_ratio", 2.0)),
            "is_long": 1.0 if signal.get("sentiment") == "bullish" else 0.0,
            "regime_risk_on": 1.0 if risk_q.get("market_regime") == "RISK_ON" else 0.0,
            "regime_bear": 1.0 if risk_q.get("market_regime") == "RISK_OFF_BEAR" else 0.0,
            "regime_volatile": 1.0 if risk_q.get("market_regime") == "RISK_OFF_VOLATILE" else 0.0,
        }
        df_x = pd.DataFrame([features])
        if _meta_model.predict(df_x)[0] == 0:
            return False, "ML_VETO: Prediction is Loss"
        return True, "OK: ML Approved"
    except Exception as e:
        return True, f"SKIP: ML error {e}"


@trace_node("Portfolio_Risk")
async def node_portfolio_risk_check(state: TradingState) -> dict:
    """
    Portfolio Impedance Verification Node.
    """
    ts = datetime.now(UTC).isoformat()
    loop = asyncio.get_running_loop()  # 🔧 SRE FIX: Async clock

    # 🔧 SRE FIX: Alignment with the v4.0 Data Bus
    signal = state.get("ingress_signal", {})
    risk_q = state.get("risk_quant", {})

    ticker = signal.get("ticker", "UNKNOWN")
    action = risk_q.get("routing_flag", "REJECTED")
    alloc_usd = risk_q.get("allocation_usd", 0.0)

    if action != "APPROVED":
        return {"logs": [f"[{ts}][portfolio_risk] Signal not approved. Guards bypassed."]}

    # 1. REAL TELEMETRY (Alpaca API via ThreadPool)
    try:
        cfg = get_orchestrator_settings()
        from alpaca.trading.client import TradingClient

        client = TradingClient(
            api_key=cfg.alpaca_api_key.get_secret_value(),
            secret_key=cfg.alpaca_secret_key.get_secret_value(),
            paper=cfg.alpaca_paper,
        )
        # 🔧 SRE FIX: Async I/O so we don't block the kernel
        account = await loop.run_in_executor(None, client.get_account)
        positions = await loop.run_in_executor(None, client.get_all_positions)

        equity = float(account.equity or 0)
        peak = float(account.last_equity or equity)
        open_tickers = [getattr(p, "symbol", "") for p in positions]

    except Exception as e:
        logger.error(f"🚨 [BROKER_FAULT] Error reading Alpaca telemetry: {e}")
        return _trip_breaker(risk_q, "Broker Telemetry Fault", [f"Error: {e}"])

    # 2. GUARD EXECUTION
    audit = []

    ok, msg = _check_max_positions(positions)
    audit.append(msg)
    if not ok:
        return _trip_breaker(risk_q, msg, audit)

    ok, msg = _check_sector_exposure(ticker, alloc_usd, positions, equity)
    audit.append(msg)
    if not ok:
        return _trip_breaker(risk_q, msg, audit)

    ok, msg = _check_drawdown_killswitch(equity, peak)
    audit.append(msg)
    if not ok:
        return _trip_breaker(risk_q, msg, audit)

    try:
        # 🔧 SRE FIX: Async I/O for Yahoo Finance
        ok, msg = await loop.run_in_executor(None, _check_correlation, ticker, open_tickers)
        audit.append(msg)
        if not ok:
            return _trip_breaker(risk_q, msg, audit)
    except Exception as e:
        logger.warning(f"⚠️ [CORR_FAULT] Correlation error: {e}")

    ok, msg = _check_meta_labeling(state)
    audit.append(msg)
    if not ok:
        # 🕹️ HITL BYPASS DIODE: If the Judge has very high conviction (>90%), we escalate to a human
        # 🔧 PATCH: If there's already a Manual Override, we respect that authority and don't ask again
        judge_conf = float(risk_q.get("judge_confidence", 0.0))
        is_manual = risk_q.get("manual_override", False)

        if judge_conf >= 0.90 and not is_manual:
            logger.info(
                f"⚡ [Bypass] ML Veto overridden by High Judge Confidence ({judge_conf:.2f}). Escalating to HITL."
            )
            return _trip_hitl(risk_q, msg, audit)

        if is_manual:
            logger.info("✅ [Manual_Bypass] Ignoring ML veto by direct partner order.")
        else:
            return _trip_breaker(risk_q, msg, audit)

    return {
        "portfolio_risk_status": "PASS",
        "portfolio_risk_audit": audit,
        "logs": [f"[{ts}][portfolio_risk] ✅ PASS for {ticker}"],
    }


def _trip_hitl(risk_dict: dict, reason: str, audit: list) -> dict:
    """Escalates to HITL (AMBIGUOUS) instead of rejecting."""
    updated_risk = risk_dict.copy()
    updated_risk["routing_flag"] = "AMBIGUOUS"
    updated_risk["discard_reason"] = (
        f"[High_Conviction_Bypass] {reason}. Requires human intervention."
    )
    return {
        "risk_quant": updated_risk,
        "portfolio_risk_status": "HITL_REQUIRED",
        "portfolio_risk_audit": audit,
        "logs": [f"🕹️ [HITL_Escalation] {reason} | Judge conviction detected."],
    }


def _trip_breaker(risk_dict: dict, reason: str, audit: list) -> dict:
    """Opens the circuit (REJECT)."""
    updated_risk = risk_dict.copy()
    updated_risk["routing_flag"] = "REJECTED"
    updated_risk["discard_reason"] = f"[Risk_Guard] {reason}"
    return {
        "risk_quant": updated_risk,
        "portfolio_risk_status": "BLOCKED",
        "portfolio_risk_audit": audit,
        "logs": [f"🚨 [Breaker_Tripped] {reason}"],
    }
