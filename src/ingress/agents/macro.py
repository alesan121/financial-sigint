"""
ingress/agents/macro.py - Market Regime Sensor (Macro-Bias).
DEFCON 1 Version: KeyError Tolerance and Safe Asynchronous Sampling.

Hardware Analogy: This module is the system's 'Gain Bias'.
It measures the global temperature (VIX) and tension levels (DXY/BTC).
Implements logical "Pull-Up" resistors per channel to avoid ADC failures if a ticker closes.
"""

import asyncio
import logging
import time

import yfinance as yf
from pydantic import BaseModel

logger = logging.getLogger(__name__)


class MarketStats(BaseModel):
    vix: float = 20.0  # Fear (Neutral)
    dxy: float = 100.0  # Dollar (Neutral)
    btc: float = 60000.0  # Appetite (Neutral)
    timestamp: float = 0.0


class MacroProvider:
    """
    Low-latency macro data provider with timeout and robust failsafe.
    """

    def __init__(self, timeout: float = 10.0):
        self._timeout = timeout
        self._last_stats = MarketStats()

    async def get_current_regime(self) -> MarketStats:
        """
        Gets the macro 'Background Noise'.
        If the external bus fails, returns the last known 'Setpoint'.
        """
        try:
            loop = asyncio.get_running_loop()
            stats = await asyncio.wait_for(
                loop.run_in_executor(None, self._fetch_sync), timeout=self._timeout
            )
            self._last_stats = stats
            return stats
        except TimeoutError:
            logger.warning("⚠️ [MACRO] Timeout on the YFinance data bus. Using Failsafe.")
            return self._last_stats
        except Exception as e:
            logger.error(
                f"💥 [MACRO] Critical hardware failure in macro sensor: {e}. Using Failsafe."
            )
            return self._last_stats

    def _fetch_sync(self) -> MarketStats:
        """Synchronous read of the macro descriptors with channel protection."""
        try:
            # 🔧 SRE FIX: Request 5 days at daily resolution.
            # This guarantees having the latest data even on weekends/holidays.
            tickers = yf.Tickers("^VIX DX-Y.NYB BTC-USD")
            history = tickers.history(period="5d", interval="1d")

            if history.empty or "Close" not in history:
                raise ValueError("YFinance bus returned an empty frame")

            close_df = history["Close"]

            # 🔧 SRE FIX: Safe extraction function (Pull-Up Resistor)
            # Avoids KeyError if a ticker wasn't downloaded and handles NaNs.
            def _get_safe_val(ticker_name: str, default_val: float) -> float:
                if ticker_name in close_df.columns:
                    series = close_df[ticker_name].dropna()
                    if not series.empty:
                        val = series.iloc[-1]
                        return float(val) if val > 0 else default_val
                return default_val

            vix = _get_safe_val("^VIX", 20.0)
            dxy = _get_safe_val("DX-Y.NYB", 100.0)
            btc = _get_safe_val("BTC-USD", 60000.0)

            return MarketStats(vix=vix, dxy=dxy, btc=btc, timestamp=time.time())
        except Exception as e:
            logger.error(f"[MACRO] Internal error in _fetch_sync: {e}")
            raise  # Re-raise so the async handler uses the failsafe
