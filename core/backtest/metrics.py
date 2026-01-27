# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-27T22:00:00Z
# Purpose: Backtest metrics calculation (win rate, drawdown, Sharpe, etc.)
# Security: Pure computation, no side effects
# === END SIGNATURE ===
"""
Backtest Metrics Calculator.

Provides functions to calculate standard trading performance metrics:
- Win rate, profit factor
- Maximum drawdown (%, duration)
- Sharpe ratio (annualized)
- Average trade statistics

All functions are pure (no side effects) and fail-closed (return 0/empty on invalid input).
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import List, Tuple, Dict, Any, Optional

import numpy as np


@dataclass
class DrawdownInfo:
    """Drawdown analysis result."""
    max_drawdown_pct: float  # Peak-to-trough percentage
    max_drawdown_abs: float  # Absolute value
    max_duration_bars: int   # Bars in longest drawdown
    current_drawdown_pct: float  # Current DD from peak
    peak_equity: float
    trough_equity: float


def calculate_drawdown(equity_curve: List[float]) -> DrawdownInfo:
    """
    Calculate maximum drawdown from equity curve.

    Args:
        equity_curve: List of equity values over time

    Returns:
        DrawdownInfo with max DD %, duration, and details
    """
    if not equity_curve or len(equity_curve) < 2:
        return DrawdownInfo(
            max_drawdown_pct=0.0,
            max_drawdown_abs=0.0,
            max_duration_bars=0,
            current_drawdown_pct=0.0,
            peak_equity=equity_curve[0] if equity_curve else 0.0,
            trough_equity=equity_curve[0] if equity_curve else 0.0,
        )

    equity = np.array(equity_curve, dtype=np.float64)

    # Running maximum (peak)
    running_max = np.maximum.accumulate(equity)

    # Drawdown at each point
    drawdown = (running_max - equity) / np.where(running_max > 0, running_max, 1.0)

    # Maximum drawdown
    max_dd_idx = np.argmax(drawdown)
    max_dd_pct = float(drawdown[max_dd_idx])
    max_dd_abs = float(running_max[max_dd_idx] - equity[max_dd_idx])

    # Find peak before max drawdown
    peak_idx = np.argmax(equity[:max_dd_idx + 1]) if max_dd_idx > 0 else 0
    peak_equity = float(equity[peak_idx])
    trough_equity = float(equity[max_dd_idx])

    # Calculate drawdown duration (bars in drawdown)
    max_duration = 0
    current_duration = 0
    in_drawdown = False

    for i in range(len(equity)):
        if equity[i] < running_max[i]:
            current_duration += 1
            in_drawdown = True
        else:
            if in_drawdown:
                max_duration = max(max_duration, current_duration)
                current_duration = 0
                in_drawdown = False

    # Check final drawdown
    if in_drawdown:
        max_duration = max(max_duration, current_duration)

    # Current drawdown
    current_dd_pct = float(drawdown[-1]) if len(drawdown) > 0 else 0.0

    return DrawdownInfo(
        max_drawdown_pct=max_dd_pct,
        max_drawdown_abs=max_dd_abs,
        max_duration_bars=max_duration,
        current_drawdown_pct=current_dd_pct,
        peak_equity=peak_equity,
        trough_equity=trough_equity,
    )


def calculate_sharpe_ratio(
    returns: List[float],
    risk_free_rate: float = 0.0,
    periods_per_year: int = 252 * 96,  # 15-min bars in trading year
) -> float:
    """
    Calculate annualized Sharpe ratio.

    Args:
        returns: List of period returns (e.g., per-bar returns)
        risk_free_rate: Annual risk-free rate (default 0)
        periods_per_year: Trading periods per year (96 15-min bars/day * 252 days)

    Returns:
        Annualized Sharpe ratio (0.0 if insufficient data)
    """
    if not returns or len(returns) < 2:
        return 0.0

    returns_arr = np.array(returns, dtype=np.float64)

    # Remove NaN/Inf
    returns_arr = returns_arr[np.isfinite(returns_arr)]
    if len(returns_arr) < 2:
        return 0.0

    mean_return = np.mean(returns_arr)
    std_return = np.std(returns_arr, ddof=1)

    if std_return == 0 or not np.isfinite(std_return):
        return 0.0

    # Per-period risk-free rate
    rf_per_period = risk_free_rate / periods_per_year

    # Sharpe = (mean - rf) / std * sqrt(periods)
    sharpe = (mean_return - rf_per_period) / std_return * math.sqrt(periods_per_year)

    return float(sharpe) if np.isfinite(sharpe) else 0.0


def calculate_profit_factor(gross_profit: float, gross_loss: float) -> float:
    """
    Calculate profit factor (gross profit / gross loss).

    Returns:
        Profit factor (999.99 cap if no losses, 0.0 if no profits)
    """
    if gross_loss == 0:
        return 999.99 if gross_profit > 0 else 0.0
    if gross_profit <= 0:
        return 0.0
    return min(gross_profit / abs(gross_loss), 999.99)


def calculate_win_rate(winning_trades: int, total_trades: int) -> float:
    """Calculate win rate as percentage."""
    if total_trades == 0:
        return 0.0
    return winning_trades / total_trades


@dataclass
class TradeStats:
    """Statistics from a list of trades."""
    total_trades: int
    winning_trades: int
    losing_trades: int
    breakeven_trades: int

    gross_profit: float
    gross_loss: float
    net_pnl: float

    win_rate: float
    profit_factor: float

    avg_trade_pnl: float
    avg_win: float
    avg_loss: float

    largest_win: float
    largest_loss: float

    avg_bars_in_trade: float
    max_consecutive_wins: int
    max_consecutive_losses: int


def calculate_trade_stats(
    pnls: List[float],
    bars_in_trades: Optional[List[int]] = None,
) -> TradeStats:
    """
    Calculate comprehensive trade statistics.

    Args:
        pnls: List of trade P&L values
        bars_in_trades: Optional list of trade durations (bars)

    Returns:
        TradeStats dataclass with all metrics
    """
    if not pnls:
        return TradeStats(
            total_trades=0,
            winning_trades=0,
            losing_trades=0,
            breakeven_trades=0,
            gross_profit=0.0,
            gross_loss=0.0,
            net_pnl=0.0,
            win_rate=0.0,
            profit_factor=0.0,
            avg_trade_pnl=0.0,
            avg_win=0.0,
            avg_loss=0.0,
            largest_win=0.0,
            largest_loss=0.0,
            avg_bars_in_trade=0.0,
            max_consecutive_wins=0,
            max_consecutive_losses=0,
        )

    pnl_arr = np.array(pnls, dtype=np.float64)

    # Basic counts
    total = len(pnl_arr)
    wins = pnl_arr > 0
    losses = pnl_arr < 0
    breakeven = pnl_arr == 0

    winning_trades = int(np.sum(wins))
    losing_trades = int(np.sum(losses))
    breakeven_trades = int(np.sum(breakeven))

    # Profit/Loss
    gross_profit = float(np.sum(pnl_arr[wins])) if winning_trades > 0 else 0.0
    gross_loss = float(np.sum(pnl_arr[losses])) if losing_trades > 0 else 0.0
    net_pnl = float(np.sum(pnl_arr))

    # Rates
    win_rate = calculate_win_rate(winning_trades, total)
    profit_factor = calculate_profit_factor(gross_profit, abs(gross_loss))

    # Averages
    avg_trade_pnl = float(np.mean(pnl_arr))
    avg_win = float(np.mean(pnl_arr[wins])) if winning_trades > 0 else 0.0
    avg_loss = float(np.mean(pnl_arr[losses])) if losing_trades > 0 else 0.0

    # Extremes
    largest_win = float(np.max(pnl_arr)) if len(pnl_arr) > 0 else 0.0
    largest_loss = float(np.min(pnl_arr)) if len(pnl_arr) > 0 else 0.0

    # Bars in trade
    avg_bars = 0.0
    if bars_in_trades:
        avg_bars = float(np.mean(bars_in_trades))

    # Consecutive wins/losses
    max_wins, max_losses = _calculate_streaks(pnl_arr)

    return TradeStats(
        total_trades=total,
        winning_trades=winning_trades,
        losing_trades=losing_trades,
        breakeven_trades=breakeven_trades,
        gross_profit=gross_profit,
        gross_loss=gross_loss,
        net_pnl=net_pnl,
        win_rate=win_rate,
        profit_factor=profit_factor,
        avg_trade_pnl=avg_trade_pnl,
        avg_win=avg_win,
        avg_loss=avg_loss,
        largest_win=largest_win,
        largest_loss=largest_loss,
        avg_bars_in_trade=avg_bars,
        max_consecutive_wins=max_wins,
        max_consecutive_losses=max_losses,
    )


def _calculate_streaks(pnls: np.ndarray) -> Tuple[int, int]:
    """Calculate max consecutive wins and losses."""
    if len(pnls) == 0:
        return 0, 0

    max_wins = 0
    max_losses = 0
    current_wins = 0
    current_losses = 0

    for pnl in pnls:
        if pnl > 0:
            current_wins += 1
            current_losses = 0
            max_wins = max(max_wins, current_wins)
        elif pnl < 0:
            current_losses += 1
            current_wins = 0
            max_losses = max(max_losses, current_losses)
        else:
            # Breakeven resets both
            current_wins = 0
            current_losses = 0

    return max_wins, max_losses


def calculate_returns(equity_curve: List[float]) -> List[float]:
    """
    Calculate period returns from equity curve.

    Returns:
        List of returns (equity[i] / equity[i-1] - 1)
    """
    if len(equity_curve) < 2:
        return []

    equity = np.array(equity_curve, dtype=np.float64)

    # Avoid division by zero
    prev = equity[:-1]
    prev = np.where(prev != 0, prev, 1.0)

    returns = (equity[1:] / prev) - 1.0
    return returns.tolist()


def format_metrics_report(
    trade_stats: TradeStats,
    drawdown: DrawdownInfo,
    sharpe: float,
    initial_capital: float,
    final_equity: float,
) -> str:
    """
    Format metrics as human-readable report.

    Returns:
        Multi-line string with formatted metrics
    """
    total_return_pct = ((final_equity / initial_capital) - 1) * 100 if initial_capital > 0 else 0

    lines = [
        "=" * 50,
        "BACKTEST RESULTS",
        "=" * 50,
        "",
        f"Initial Capital:    ${initial_capital:,.2f}",
        f"Final Equity:       ${final_equity:,.2f}",
        f"Total Return:       {total_return_pct:+.2f}%",
        f"Net P&L:            ${trade_stats.net_pnl:+,.2f}",
        "",
        "-" * 30,
        "TRADE STATISTICS",
        "-" * 30,
        f"Total Trades:       {trade_stats.total_trades}",
        f"Winning Trades:     {trade_stats.winning_trades}",
        f"Losing Trades:      {trade_stats.losing_trades}",
        f"Win Rate:           {trade_stats.win_rate:.1%}",
        f"Profit Factor:      {trade_stats.profit_factor:.2f}",
        "",
        f"Avg Trade P&L:      ${trade_stats.avg_trade_pnl:+.2f}",
        f"Avg Win:            ${trade_stats.avg_win:+.2f}",
        f"Avg Loss:           ${trade_stats.avg_loss:+.2f}",
        f"Largest Win:        ${trade_stats.largest_win:+.2f}",
        f"Largest Loss:       ${trade_stats.largest_loss:+.2f}",
        "",
        f"Avg Bars/Trade:     {trade_stats.avg_bars_in_trade:.1f}",
        f"Max Win Streak:     {trade_stats.max_consecutive_wins}",
        f"Max Loss Streak:    {trade_stats.max_consecutive_losses}",
        "",
        "-" * 30,
        "RISK METRICS",
        "-" * 30,
        f"Max Drawdown:       {drawdown.max_drawdown_pct:.2%}",
        f"Max DD (abs):       ${drawdown.max_drawdown_abs:,.2f}",
        f"Max DD Duration:    {drawdown.max_duration_bars} bars",
        f"Sharpe Ratio:       {sharpe:.2f}",
        "",
        "=" * 50,
    ]

    return "\n".join(lines)
