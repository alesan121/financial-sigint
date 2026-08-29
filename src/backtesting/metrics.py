"""
backtesting/metrics.py - Strategy performance metrics calculator.

Hardware analogy: this module is the post-execution 'spectrum analyzer'.
Just as an RF engineer analyzes a filter's frequency response
to verify its behavior, here we analyze the strategy's P&L
distribution to verify it has a positive edge.

Calculated metrics:
  - Sharpe Ratio: risk-adjusted return (goal: > 1.0)
  - Sortino Ratio: like Sharpe but only penalizes negative volatility (> 1.5)
  - Max Drawdown: maximum decline from a peak (goal: < 15%)
  - Win Rate: % of winning trades (with kelly: doesn't need to be >50%)
  - Expectancy: expected profit per trade (goal: > $0)
  - Profit Factor: sum of gains / sum of losses (goal: > 1.5)
  - CAGR: compound annual growth rate
  - Calmar Ratio: CAGR / Max Drawdown (strategy quality)
"""

import math
from dataclasses import dataclass, field

import numpy as np
import pandas as pd


@dataclass
class BacktestTrade:
    """Represents a single backtest trade."""
    ticker:        str
    entry_date:    str
    exit_date:     str
    direction:     str          # "LONG" | "SHORT"
    entry_price:   float
    exit_price:    float
    stop_loss:     float
    take_profit:   float
    shares:        float        # Number of shares (can be fractional)
    allocation_usd: float
    pnl_usd:       float        # Profit & Loss in USD
    pnl_pct:       float        # P&L as a % of the allocation
    exit_reason:   str          # "TAKE_PROFIT" | "STOP_LOSS" | "TIMEOUT" | "EOD"
    pop_score:     float        # PoP calculated by the Risk Engine
    kelly_frac:    float        # Kelly fraction used
    features:      dict[str, float] | None = None # Feature vector X for ML (Meta-Labeling)


@dataclass
class BacktestMetrics:
    """Performance metrics calculated over the set of trades."""
    total_trades:     int   = 0
    win_trades:       int   = 0
    loss_trades:      int   = 0
    win_rate:         float = 0.0
    total_pnl_usd:    float = 0.0
    avg_win_usd:      float = 0.0
    avg_loss_usd:     float = 0.0
    profit_factor:    float = 0.0
    expectancy_usd:   float = 0.0
    sharpe_ratio:     float = 0.0
    sortino_ratio:    float = 0.0
    max_drawdown_pct: float = 0.0
    max_drawdown_usd: float = 0.0
    cagr_pct:         float = 0.0
    calmar_ratio:     float = 0.0
    total_days:       int   = 0
    # Overall quality signal
    has_edge:         bool  = False
    summary:          str   = ""


def calculate_metrics(
    trades: list[BacktestTrade],
    initial_capital: float = 100_000.0,
    risk_free_rate: float = 0.05,   # 5% annual (approximate T-Bill rate)
) -> BacktestMetrics:
    """
    Calculates the full set of performance metrics over the list of trades.

    Args:
        trades: List of BacktestTrade executed during the backtest.
        initial_capital: Initial capital in USD.
        risk_free_rate: Annual risk-free rate used for the Sharpe calculation.

    Returns:
        BacktestMetrics: Object with all the calculated metrics.
    """
    m = BacktestMetrics()

    if not trades:
        m.summary = "⚠️ No trades in the analyzed period."
        return m

    m.total_trades = len(trades)
    pnl_list = [t.pnl_usd for t in trades]
    wins  = [p for p in pnl_list if p > 0]
    losses = [p for p in pnl_list if p <= 0]

    m.win_trades  = len(wins)
    m.loss_trades = len(losses)
    m.win_rate    = m.win_trades / m.total_trades if m.total_trades > 0 else 0.0
    m.total_pnl_usd = sum(pnl_list)
    m.avg_win_usd   = float(np.mean(wins))   if wins   else 0.0
    m.avg_loss_usd  = float(np.mean(losses)) if losses else 0.0

    # ── Profit Factor ─────────────────────────────────────────────────────────
    gross_gains  = sum(wins)
    gross_losses = abs(sum(losses))
    m.profit_factor = (gross_gains / gross_losses) if gross_losses > 0 else float("inf")

    # ── Expectancy (expected value per trade) ─────────────────────────────────
    # E = (Win Rate × Avg Win) - (Loss Rate × |Avg Loss|)
    loss_rate = 1.0 - m.win_rate
    m.expectancy_usd = (m.win_rate * m.avg_win_usd) - (loss_rate * abs(m.avg_loss_usd))

    # ── Equity Curve and Drawdown ─────────────────────────────────────────────
    equity = initial_capital
    peak   = initial_capital
    max_dd_usd = 0.0
    equity_curve = []

    for trade in sorted(trades, key=lambda t: t.entry_date):
        equity += trade.pnl_usd
        equity_curve.append(equity)
        if equity > peak:
            peak = equity
        drawdown = peak - equity
        max_dd_usd = max(max_dd_usd, drawdown)

    m.max_drawdown_usd = max_dd_usd
    m.max_drawdown_pct = (max_dd_usd / initial_capital) * 100 if initial_capital > 0 else 0.0

    # ── Sharpe Ratio ──────────────────────────────────────────────────────────
    # Sharpe = (Mean Return - Rf) / StdDev Return
    # Convert PnL to % returns for annualized Sharpe
    returns = [p / initial_capital for p in pnl_list]
    if len(returns) > 1:
        mean_return = float(np.mean(returns))
        std_return  = float(np.std(returns, ddof=1))
        daily_rf    = risk_free_rate / 252  # Approximate daily rate
        trades_per_year_sqrt = math.sqrt(252)  # Annualization assuming 1 trade/day in the sample
        if std_return > 0:
            m.sharpe_ratio = (mean_return - daily_rf) / std_return * trades_per_year_sqrt
        else:
            m.sharpe_ratio = float("inf") if mean_return > 0 else 0.0

    # ── Sortino Ratio ─────────────────────────────────────────────────────────
    # Only uses the standard deviation of negative returns (downside deviation)
    negative_returns = [r for r in returns if r < 0]
    if len(negative_returns) > 1:
        downside_std = float(np.std(negative_returns, ddof=1))
        mean_return  = float(np.mean(returns))
        daily_rf     = risk_free_rate / 252
        if downside_std > 0:
            m.sortino_ratio = (mean_return - daily_rf) / downside_std * math.sqrt(252)
    else:
        m.sortino_ratio = float("inf") if m.total_pnl_usd > 0 else 0.0

    # ── CAGR ─────────────────────────────────────────────────────────────────
    # Calculate total period in years
    if trades:
        dates_sorted = sorted([t.entry_date for t in trades])
        try:
            first = pd.Timestamp(dates_sorted[0])
            last  = pd.Timestamp(sorted([t.exit_date for t in trades])[-1])
            total_days = (last - first).days
            m.total_days = total_days
            if total_days > 0 and equity > 0:
                years = total_days / 365.25
                final_equity = initial_capital + m.total_pnl_usd
                m.cagr_pct = ((final_equity / initial_capital) ** (1 / years) - 1) * 100
        except Exception:
            pass

    # ── Calmar Ratio ──────────────────────────────────────────────────────────
    if m.max_drawdown_pct > 0:
        m.calmar_ratio = m.cagr_pct / m.max_drawdown_pct
    elif m.cagr_pct > 0:
        m.calmar_ratio = float("inf")

    # ── Edge Evaluation ───────────────────────────────────────────────────────
    # Minimum criteria to consider the strategy has statistical edge:
    m.has_edge = (
        m.sharpe_ratio >= 1.0 and
        m.expectancy_usd > 0 and
        m.profit_factor >= 1.2 and
        m.max_drawdown_pct < 20.0 and
        m.total_trades >= 10        # Minimum statistical sample
    )

    # ── Readable Summary ──────────────────────────────────────────────────────
    edge_icon = "✅ POSITIVE EDGE" if m.has_edge else "⚠️ NO EDGE CONFIRMED"
    m.summary = (
        f"{edge_icon} | "
        f"Trades: {m.total_trades} | "
        f"Win: {m.win_rate*100:.1f}% | "
        f"Sharpe: {m.sharpe_ratio:.2f} | "
        f"Sortino: {m.sortino_ratio:.2f} | "
        f"MaxDD: {m.max_drawdown_pct:.1f}% | "
        f"Expectancy: ${m.expectancy_usd:.0f} | "
        f"PF: {m.profit_factor:.2f} | "
        f"CAGR: {m.cagr_pct:.1f}% | "
        f"Calmar: {m.calmar_ratio:.2f} | "
        f"Total PnL: ${m.total_pnl_usd:,.0f}"
    )

    return m
