"""
backtesting/engine.py - SIGINT Vectorized Backtesting Engine.

Hardware analogy: this module is the system's 'circuit simulator' (SPICE).
Before building the real circuit (trading with real money), we simulate its behavior
over historical data to verify that our signal logic has a positive edge.

Simulation strategy:
  For each ticker and historical period:
  1. Download OHLCV (Open/High/Low/Close/Volume) via yfinance
  2. Calculate the exact technical indicators used by the Risk Engine:
     - ATR(14), SMA(20), SMA(50)
     - Support = price - 1.5 * ATR | Resistance = price + 4.5 * ATR
  3. Simulate entry signals on random days (baseline random) and on days
     with favorable conditions (PoP > 0.75, price > SMA20)
  4. Execute the order: Long or Short depending on simulated sentiment
  5. Close the position when TP or SL is hit, or the horizon (N days) expires
  6. Record the P&L and pass the result to the metrics.py module

Available backtesting modes:
  - BASELINE: Random entries (always assuming bullish sentiment) — lower bound
  - FILTERED: Entries only when simulated PoP > threshold — measures the filter's value
  - FULL: Complete pipeline replicating the SIGINT logic (PoP + Kelly allocation)
"""

import logging
import random

import numpy as np
import pandas as pd
import yfinance as yf

from backtesting.metrics import BacktestMetrics, BacktestTrade, calculate_metrics

logger = logging.getLogger(__name__)

# =============================================================================
# ENGINE CONSTANTS (must match risk_engine.py)
# =============================================================================

ATR_PERIOD: int = 14
SMA_SHORT: int = 20
SMA_LONG: int = 50

ATR_STOP_MULT: float = 1.5  # Stop loss = price - 1.5*ATR
ATR_TARGET_MULT: float = 4.5  # Take profit = price + 4.5*ATR

MIN_POP: float = 0.75
MIN_ALLOCATION: float = 2_500.0
VIRTUAL_BALANCE: float = 100_000.0
KELLY_DIVISOR: float = 2.0

# Maximum number of days to hold a position before closing it (timeout)
MAX_HOLDING_DAYS: int = 10


# =============================================================================
# TECHNICAL INDICATOR CALCULATION (vectorized pandas version)
# =============================================================================


def compute_technical_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """
    Calculates technical indicators on an OHLCV DataFrame.

    Exactly replicates the same logic as the orchestrator's technical.py:
    ATR(14), SMA(20), SMA(50), dynamic Support and Resistance.

    Args:
        df: yfinance DataFrame with Open/High/Low/Close/Volume columns.

    Returns:
        DataFrame enriched with technical indicators.
    """
    df = df.copy()

    # True Range (for ATR)
    high = df["High"]
    low = df["Low"]
    close = df["Close"]

    tr1 = high - low
    tr2 = (high - close.shift(1)).abs()
    tr3 = (low - close.shift(1)).abs()
    true_range = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

    # ATR(14) — Exponential Moving Average of the True Range
    df["atr_14"] = true_range.ewm(span=ATR_PERIOD, adjust=False).mean()

    # SMAs
    df["sma_20"] = close.rolling(SMA_SHORT).mean()
    df["sma_50"] = close.rolling(SMA_LONG).mean()

    # Support and Resistance (same formula as technical.py)
    df["support"] = close - ATR_STOP_MULT * df["atr_14"]
    df["resistance"] = close + ATR_TARGET_MULT * df["atr_14"]

    # Trend signal (True if price is above the moving average)
    df["bullish_trend"] = close > df["sma_20"]

    # Relative volatility: ATR / Close (the daily range as a %)
    df["atr_pct"] = df["atr_14"] / close

    # Kalman Filter over the historical vector
    # Kalman is recursive, computed once over the whole series
    kalman_series = []
    x_hat = df["Close"].iloc[0] if not df.empty else 0.0
    p = 1.0
    q = 1e-5

    for i in range(len(df)):
        price = df["Close"].iloc[i]
        r = df["atr_pct"].iloc[i] if "atr_pct" in df.columns and "Close" in df.columns else 0.01
        if r <= 0 or pd.isna(r):
            r = 0.01

        # Prediction
        x_hat_minus = x_hat
        p_minus = p + q

        # Update
        k = p_minus / (p_minus + r)
        x_hat = x_hat_minus + k * (price - x_hat_minus)
        p = (1 - k) * p_minus
        kalman_series.append(x_hat)

    df["kalman_price"] = kalman_series

    return df.dropna()


# =============================================================================
# PoP SIMULATOR (replicates _calculate_pop from risk_engine)
# =============================================================================


def simulate_pop(
    price: float,
    sma_20: float,
    support: float,
    resistance: float,
    confidence: float,  # simulated p (stoch_confidence)
    impact_score: float,  # simulated LLM impact_score
    sentiment: str = "bullish",
) -> float:
    """
    Replicates the MoE fusion (Channel A: LLM + Channel B: TA) from risk_engine.py.
    Allows computing the PoP for each historical bar without invoking the LLM.
    """
    # Channel A (LLM)
    llm_signal = (impact_score + 1.0) / 2.0
    if sentiment == "bearish":
        llm_signal = 1.0 - llm_signal
    channel_a = confidence * abs(llm_signal)

    # Channel B (TA)
    trend_score = 0.0
    if sma_20 > 0 and price > sma_20:
        trend_score += 0.40
    else:
        trend_score += 0.10

    if resistance > support > 0:
        price_range = resistance - support
        relative_pos = (price - support) / price_range if price_range > 0 else 0.5
        if 0.20 < relative_pos < 0.70:
            trend_score += 0.35
        elif relative_pos <= 0.20:
            trend_score += 0.10
        else:
            trend_score += 0.15
        rr_pct = price_range / price
        if 0.02 < rr_pct < 0.15:
            trend_score += 0.25

    if sentiment == "bearish":
        trend_score = max(0.0, 0.75 - trend_score)

    channel_b = min(trend_score, 1.0)

    if channel_a + channel_b <= 0:
        return 0.0
    return (2 * channel_a * channel_b) / (channel_a + channel_b)


# =============================================================================
# SINGLE-TRADE SIMULATION
# =============================================================================


def simulate_trade(
    df: pd.DataFrame,
    entry_idx: int,
    direction: str,
    allocation_usd: float,
    kelly_frac: float,
    pop_score: float,
) -> BacktestTrade | None:
    """
    Simulates a single trade starting from the entry day.

    Close logic (first condition met wins):
    1. TAKE_PROFIT: Day's High > resistance (for LONG) or Low < support (SHORT)
    2. STOP_LOSS:   Day's Low < support (LONG) or High > resistance (SHORT)
    3. TIMEOUT:     MAX_HOLDING_DAYS reached without hitting TP or SL -> close at Close
    """
    if entry_idx + 1 >= len(df):
        return None

    entry_row = df.iloc[entry_idx]
    entry_date = str(df.index[entry_idx].date())
    entry_price = float(entry_row["Close"])
    stop_loss = float(entry_row["support"])
    take_profit = float(entry_row["resistance"])

    if direction == "SHORT":
        stop_loss, take_profit = take_profit, entry_row["support"]
        stop_loss = float(entry_row["resistance"])
        take_profit = float(entry_row["support"])

    if allocation_usd <= 0 or entry_price <= 0:
        return None

    shares = allocation_usd / entry_price

    # Simulate the following days until TP, SL, or timeout
    exit_price = entry_price
    exit_date = entry_date
    exit_reason = "TIMEOUT"

    end_idx = min(entry_idx + 1 + MAX_HOLDING_DAYS, len(df))
    for i in range(entry_idx + 1, end_idx):
        row = df.iloc[i]
        day_high = float(row["High"])
        day_low = float(row["Low"])

        if direction == "LONG":
            if day_high >= take_profit:
                exit_price = take_profit
                exit_reason = "TAKE_PROFIT"
                exit_date = str(df.index[i].date())
                break
            if day_low <= stop_loss:
                exit_price = stop_loss
                exit_reason = "STOP_LOSS"
                exit_date = str(df.index[i].date())
                break
        else:  # SHORT
            if day_low <= take_profit:
                exit_price = take_profit
                exit_reason = "TAKE_PROFIT"
                exit_date = str(df.index[i].date())
                break
            if day_high >= stop_loss:
                exit_price = stop_loss
                exit_reason = "STOP_LOSS"
                exit_date = str(df.index[i].date())
                break
    else:
        # Timeout: close at the last Close of the period
        exit_price = float(df.iloc[end_idx - 1]["Close"])
        exit_date = str(df.index[end_idx - 1].date())

    # Calculate P&L
    if direction == "LONG":
        pnl_usd = (exit_price - entry_price) * shares
    else:
        pnl_usd = (entry_price - exit_price) * shares

    pnl_pct = (pnl_usd / allocation_usd) * 100

    return BacktestTrade(
        ticker=str(df.columns[0]) if hasattr(df.columns, "__iter__") else "?",
        entry_date=entry_date,
        exit_date=exit_date,
        direction=direction,
        entry_price=entry_price,
        exit_price=exit_price,
        stop_loss=stop_loss,
        take_profit=take_profit,
        shares=shares,
        allocation_usd=allocation_usd,
        pnl_usd=pnl_usd,
        pnl_pct=pnl_pct,
        exit_reason=exit_reason,
        pop_score=pop_score,
        kelly_frac=kelly_frac,
    )


# =============================================================================
# MAIN BACKTESTING ENGINE
# =============================================================================


class SIGINTBacktester:
    """
    Vectorized backtesting engine for the SIGINT strategy.

    Simulates the behavior of the complete pipeline (Ingress -> Risk Engine -> Execution)
    over historical yfinance data, without needing to run the real LLM.

    To simulate the LLM signals, it uses statistical distributions based
    on the configured parameters (p_range, impact_range).
    """

    def __init__(
        self,
        tickers: list[str],
        period: str = "2y",  # Historical period: "1y", "2y", "5y"
        sentiment_split: float = 0.6,  # % of signals that are bullish vs bearish
        p_mean: float = 0.65,  # Mean of the stoch_confidence distribution
        p_std: float = 0.15,  # Standard deviation of stoch_confidence
        impact_mean: float = 0.45,  # Mean of the simulated impact_score
        impact_std: float = 0.25,  # Standard deviation of impact_score
        entry_frequency: str = "weekly",  # "daily" | "weekly" | "biweekly"
        seed: int = 42,
        min_pop_filter: float = MIN_POP,  # Allows ignoring it to generate a Machine Learning dataset
    ) -> None:
        self.tickers = tickers
        self.period = period
        self.sentiment_split = sentiment_split
        self.p_mean = p_mean
        self.p_std = p_std
        self.impact_mean = impact_mean
        self.impact_std = impact_std
        self.entry_frequency = entry_frequency
        self.seed = seed
        self.min_pop_filter = min_pop_filter
        random.seed(seed)
        np.random.seed(seed)

    def _download_ohlcv(
        self, ticker: str, spy_df: pd.DataFrame | None = None, vix_df: pd.DataFrame | None = None
    ) -> pd.DataFrame | None:
        """Downloads and prepares the OHLCV data for a ticker, integrating Macro (SPY/VIX)."""
        try:
            df = yf.Ticker(ticker).history(period=self.period, auto_adjust=True)
            if df.empty or len(df) < 60:  # Minimum 60 days of history
                logger.warning("[Backtester][%s] Insufficient data (%d days)", ticker, len(df))
                return None

            # Remove the index's timezone and normalize to midnight for clean joins
            if df.index.tz is not None:
                df.index = df.index.tz_convert(None)
            df.index = df.index.normalize()

            df = compute_technical_indicators(df)

            # Integrate macroeconomic data for the Antenna Array (Meta-Labeling)
            if spy_df is not None and not spy_df.empty:
                df = df.join(spy_df[["spy_close", "spy_sma_50"]], how="left")
                df["spy_close"] = df["spy_close"].ffill()
                df["spy_sma_50"] = df["spy_sma_50"].ffill()
            else:
                df["spy_close"] = 1.0
                df["spy_sma_50"] = 1.0

            if vix_df is not None and not vix_df.empty:
                df = df.join(vix_df[["vix_level"]], how="left")
                df["vix_level"] = df["vix_level"].ffill()
            else:
                df["vix_level"] = 15.0  # Default normal value

            # Compute Market Regime for each row
            regimes = []
            for i in range(len(df)):
                vix = float(df["vix_level"].iloc[i])
                spy_c = float(df["spy_close"].iloc[i])
                spy_s = float(df["spy_sma_50"].iloc[i])

                if vix >= 25.0:
                    regimes.append("RISK_OFF_VOLATILE")
                elif spy_c < spy_s:
                    regimes.append("RISK_OFF_BEAR")
                else:
                    regimes.append("RISK_ON")
            df["market_regime"] = regimes

            logger.info(
                "[Backtester][%s] %d days of OHLCV loaded with Quant indicators", ticker, len(df)
            )
            return df.dropna()
        except Exception as e:
            logger.error("[Backtester][%s] Error downloading OHLCV: %s", ticker, e)
            return None

    def _select_entry_days(self, df: pd.DataFrame) -> list[int]:
        """Selects the entry days according to the configured frequency."""
        n = len(df)
        step = {"daily": 1, "weekly": 5, "biweekly": 10}.get(self.entry_frequency, 5)
        # Skip the first 60 days (indicator warm-up) and the last MAX_HOLDING_DAYS
        return list(range(60, n - MAX_HOLDING_DAYS - 1, step))

    def run(self) -> tuple[list[BacktestTrade], BacktestMetrics]:
        """
        Runs the backtest over all tickers and returns the metrics.

        Returns:
            (trades, metrics): List of executed trades and their aggregated metrics.
        """
        all_trades: list[BacktestTrade] = []

        # Pre-download Macro Data (SPY, VIX) only once
        logger.info("[Backtester] Downloading Macroeconomic history (SPY, ^VIX)...")
        spy_raw = yf.Ticker("SPY").history(period=self.period, auto_adjust=True)
        if not spy_raw.empty:
            if spy_raw.index.tz is not None:
                spy_raw.index = spy_raw.index.tz_convert(None)
            spy_raw.index = spy_raw.index.normalize()
            spy_raw["spy_close"] = spy_raw["Close"]
            spy_raw["spy_sma_50"] = spy_raw["Close"].rolling(50).mean()

        vix_raw = yf.Ticker("^VIX").history(period=self.period, auto_adjust=True)
        if not vix_raw.empty:
            if vix_raw.index.tz is not None:
                vix_raw.index = vix_raw.index.tz_convert(None)
            vix_raw.index = vix_raw.index.normalize()
            vix_raw["vix_level"] = vix_raw["Close"]

        for ticker in self.tickers:
            logger.info("[Backtester] Processing %s...", ticker)
            df = self._download_ohlcv(ticker, spy_raw, vix_raw)
            if df is None:
                continue

            entry_days = self._select_entry_days(df)

            for idx in entry_days:
                row = df.iloc[idx]
                price = float(row["Close"])
                sma_20 = float(row["sma_20"])
                support = float(row["support"])
                resistance = float(row["resistance"])
                atr = float(row["atr_14"])

                # Simulate LLM signal with a gaussian distribution
                p_sim = float(np.clip(np.random.normal(self.p_mean, self.p_std), 0.3, 0.99))
                impact_sim = float(
                    np.clip(np.random.normal(self.impact_mean, self.impact_std), -0.9, 0.9)
                )
                direction = (
                    "LONG" if random.random() < self.sentiment_split else "SHORT"  # nosec B311
                )
                sentiment = "bullish" if direction == "LONG" else "bearish"

                # Calculate PoP with the same MoE fusion as the Risk Engine
                pop = simulate_pop(price, sma_20, support, resistance, p_sim, impact_sim, sentiment)

                # Hard Gate (Configurable)
                if pop < self.min_pop_filter:
                    continue

                # Fractional Kelly
                if direction == "LONG":
                    risk_usd = price - support
                    reward_usd = resistance - price
                else:
                    risk_usd = resistance - price
                    reward_usd = price - support

                if risk_usd <= 0 or reward_usd <= 0:
                    continue

                b = reward_usd / risk_usd
                f_star = (pop * b - (1 - pop)) / b
                kelly_frac = max(0.0, f_star / KELLY_DIVISOR)
                allocation = kelly_frac * VIRTUAL_BALANCE

                if allocation < MIN_ALLOCATION:
                    if self.min_pop_filter > 0.0:
                        continue
                    else:
                        allocation = MIN_ALLOCATION  # Force execution for ML label collection
                        kelly_frac = MIN_ALLOCATION / VIRTUAL_BALANCE

                trade = simulate_trade(df, idx, direction, allocation, kelly_frac, pop)
                if trade:
                    trade.ticker = ticker

                    # Generate Market Features (T0) for Meta-Labeling
                    kalman = float(row.get("kalman_price", price))
                    vix = float(row.get("vix_level", 15.0))
                    regime = str(row.get("market_regime", "RISK_ON"))
                    atr_p = float(row.get("atr_pct", atr / price))

                    # Basic One-Hot encoding for Regime
                    is_risk_on = 1.0 if regime == "RISK_ON" else 0.0
                    is_bear = 1.0 if regime == "RISK_OFF_BEAR" else 0.0
                    is_vol = 1.0 if regime == "RISK_OFF_VOLATILE" else 0.0
                    is_long = 1.0 if direction == "LONG" else 0.0

                    features = {
                        "vix_level": vix,
                        "atr_pct": atr_p,
                        "kalman_diff_pct": (kalman - price) / price,
                        "pop_score": pop,
                        "stoch_confidence": p_sim,
                        "impact_score": impact_sim,
                        "reward_risk_ratio": b,
                        "is_long": is_long,
                        "regime_risk_on": is_risk_on,
                        "regime_bear": is_bear,
                        "regime_volatile": is_vol,
                    }
                    trade.features = features

                    all_trades.append(trade)

        logger.info("[Backtester] Total simulated trades: %d", len(all_trades))
        metrics = calculate_metrics(all_trades)
        logger.info("[Backtester] %s", metrics.summary)
        return all_trades, metrics

    def run_baseline(self) -> tuple[list[BacktestTrade], BacktestMetrics]:
        """
        Runs the backtest WITHOUT the PoP filter (random entries).
        Serves as a lower bound to check whether the filter adds value.
        If BASELINE and FULL have similar performance -> the filter adds nothing.
        If FULL >> BASELINE -> the filter (PoP + Kelly) has real edge.
        """
        all_trades: list[BacktestTrade] = []

        for ticker in self.tickers:
            df = self._download_ohlcv(ticker)
            if df is None:
                continue

            entry_days = self._select_entry_days(df)

            for idx in entry_days:
                # BASELINE: no filter, fixed allocation of 5% of capital
                direction = "LONG" if random.random() < 0.5 else "SHORT"  # nosec B311
                allocation = VIRTUAL_BALANCE * 0.05  # fixed 5%, no Kelly

                trade = simulate_trade(df, idx, direction, allocation, 0.05, 0.5)
                if trade:
                    trade.ticker = ticker
                    all_trades.append(trade)

        metrics = calculate_metrics(all_trades)
        logger.info("[Backtester][BASELINE] %s", metrics.summary)
        return all_trades, metrics
