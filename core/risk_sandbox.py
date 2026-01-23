#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# === AI SIGNATURE ===
# Created by: Kirill Dev
# Created at: 2026-01-19 18:24:32 UTC
# Modified by: Claude (opus-4)
# Modified at: 2026-01-23 11:30:00 UTC
# === END SIGNATURE ===
"""
core.risk_sandbox
Небольшой стенд для ручного теста RiskManager.can_open().

Запуск (из корня проекта, в активном .venv):

    python -m minibot.core.risk_sandbox

или:

    python ./minibot/core/risk_sandbox.py
"""

from __future__ import annotations

import time
from typing import List

from .models import TradeSignal, TradeSide, PositionInfo, PositionState
from .risk_manager import RiskManager, RiskConfig


def make_default_config() -> RiskConfig:
    """
    Базовый конфиг для тестов.
    """
    return RiskConfig(
        daily_stop_usd=50.0,
        max_equity_per_trade_usd=100.0,
        max_open_positions=3,
        max_portfolio_load_pct=80.0,
        per_symbol_caps_usd={
            "BTCUSDT": 150.0,
            "ETHUSDT": 150.0,
        },
    )


def make_signal(symbol: str, risk_usd: float) -> TradeSignal:
    """
    Простой helper для генерации сигнала.
    """
    now = time.time()
    return TradeSignal(
        symbol=symbol,
        side=TradeSide.LONG,
        risk_usd=risk_usd,
        signal_price=50000.0,
        source="sandbox",
        signal_id=f"sandbox-{symbol}-{int(now)}",
        timestamp=now,
        extra={},
    )


def make_position(
    symbol: str,
    size_usd: float,
    avg_price: float = 50000.0,
    qty: float = 0.001,
) -> PositionInfo:
    """
    Helper для создания простой позиции.
    """
    now = time.time()
    return PositionInfo(
        symbol=symbol,
        side=TradeSide.LONG,
        qty=qty,
        avg_price=avg_price,
        size_usd=size_usd,
        state=PositionState.OPEN,
        created_at=now,
        updated_at=now,
        tags={},
    )


def print_scenario(
    title: str,
    rm: RiskManager,
    signal: TradeSignal,
    positions: List[PositionInfo],
    equity_usd: float,
) -> None:
    print("=" * 80)
    print(f"СЦЕНАРИЙ: {title}")
    print(f"Equity: {equity_usd:.2f} USDT")
    print(f"Daily PnL: {rm.daily_pnl_usd:.2f} USDT (stop={rm.config.daily_stop_usd})")
    print(f"Открытые позиции: {len(positions)}")
    for p in positions:
        print(
            f"  - {p.symbol} size={p.size_usd:.2f} side={p.side.value} state={p.state.value}"
        )

    ok, reason, allowed = rm.can_open(signal, positions, equity_usd)
    print("-" * 80)
    print(f"Signal: {signal.symbol}, risk_usd={signal.risk_usd}")
    print(f"can_open: ok={ok}, reason={reason}, allowed_size_usd={allowed:.2f}")
    print("=" * 80)
    print()


def main() -> None:
    cfg = make_default_config()
    rm = RiskManager(cfg)

    equity_usd = 1000.0

    # 1) Пустой портфель, нормальный сигнал BTC
    sig1 = make_signal("BTCUSDT", risk_usd=80.0)
    print_scenario("Пустой портфель, первый вход BTC", rm, sig1, [], equity_usd)

    # 2) Уже есть позиция BTC на 120 USDT, кап по BTC = 150
    positions2 = [
        make_position("BTCUSDT", size_usd=120.0),
    ]
    sig2 = make_signal("BTCUSDT", risk_usd=80.0)
    print_scenario(
        "Есть BTC на 120 USDT, кап по BTC=150 → должно разрешить только ~30 USDT",
        rm,
        sig2,
        positions2,
        equity_usd,
    )

    # 3) Портфель почти забит (3 позиции по 250 USDT), лимит 80% от 1000 → 800 USDT
    positions3 = [
        make_position("BTCUSDT", size_usd=250.0),
        make_position("ETHUSDT", size_usd=250.0),
        make_position("SOLUSDT", size_usd=250.0),
    ]
    sig3 = make_signal("BNBUSDT", risk_usd=100.0)
    print_scenario(
        "Портфель почти забит (750/800 USDT) → ожидание ленточного ограничителя",
        rm,
        sig3,
        positions3,
        equity_usd,
    )

    # 4) Дневной стоп уже пробит
    rm.notify_trade_result(-60.0)  # PnL = -60, стоп = 50
    sig4 = make_signal("BTCUSDT", risk_usd=50.0)
    print_scenario(
        "Дневной стоп пробит (PnL=-60 <= -50) → вход запрещён",
        rm,
        sig4,
        [],
        equity_usd,
    )


if __name__ == "__main__":
    main()

