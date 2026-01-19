#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
hope_liquidity_guard.py — P3 Liquidity Guard (HOPE Engine v5.14)

CRITICAL: Execution edge protection.
Prevents profit erosion from:
  - Wide spread (≥0.5% kills TP)
  - Thin orderbook (slippage on fill)
  - Market impact (your order moves price)

Philosophy: FAIL-CLOSED
  - When in doubt → BLOCK
  - Better to miss marginal entry than pay hidden "market tax"

Contract:
  check_liquidity(symbol, side, risk_usd, orderbook) → (allowed, reason)

  Returns:
    (True, "OK") → safe to trade
    (False, "REASON") → BLOCK with reason

  Reasons:
    - SPREAD_TOO_WIDE: bid-ask > max_spread_bps
    - DEPTH_INSUFFICIENT: orderbook depth < min_depth_multiplier * risk_usd
    - IMPACT_TOO_HIGH: estimated market impact > max_impact_bps
    - NO_ORDERBOOK: orderbook unavailable

Config (from risk_v5.yaml):
  liquidity:
    enabled: true
    max_spread_bps: 50        # 0.5% max spread
    min_depth_multiplier: 10  # depth must be 10x risk_usd
    max_impact_bps: 25        # 0.25% max market impact
"""

import logging
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)


class LiquidityGuard:
    """
    P3 Liquidity Guard — Pre-trade liquidity filter.

    Protects against execution-edge erosion by blocking trades in poor market conditions.
    """

    def __init__(self, cfg: Dict[str, Any]):
        """
        Initialize LiquidityGuard from config.

        Args:
            cfg: risk_v5.yaml config dict
        """
        liq_cfg = cfg.get("liquidity", {})

        self.enabled = liq_cfg.get("enabled", True)
        self.max_spread_bps = liq_cfg.get("max_spread_bps", 50)  # 0.5%
        self.min_depth_multiplier = liq_cfg.get("min_depth_multiplier", 10)  # 10x risk
        self.max_impact_bps = liq_cfg.get("max_impact_bps", 25)  # 0.25%

        logger.info(
            "LiquidityGuard initialized: enabled=%s, max_spread=%dbps, min_depth=%dx, max_impact=%dbps",
            self.enabled,
            self.max_spread_bps,
            self.min_depth_multiplier,
            self.max_impact_bps,
        )

    def check_liquidity(
        self,
        symbol: str,
        side: str,
        risk_usd: float,
        orderbook: Optional[Dict[str, Any]] = None,
    ) -> Tuple[bool, str]:
        """
        Check if market conditions are safe for entry/exit.

        Args:
            symbol: trading pair (e.g. "BTCUSDT")
            side: "BUY" or "SELL"
            risk_usd: position size in USD
            orderbook: {"bids": [[price, qty], ...], "asks": [[price, qty], ...]}

        Returns:
            (allowed, reason):
                - (True, "OK") → safe to trade
                - (False, "REASON") → block with reason
        """
        if not self.enabled:
            return (True, "OK")

        # --- 1. Orderbook availability ---
        if not orderbook or not isinstance(orderbook, dict):
            return (False, "NO_ORDERBOOK")

        bids = orderbook.get("bids", [])
        asks = orderbook.get("asks", [])

        if not bids or not asks:
            return (False, "NO_ORDERBOOK")

        # --- 2. Spread check ---
        best_bid = float(bids[0][0])
        best_ask = float(asks[0][0])
        mid_price = (best_bid + best_ask) / 2.0

        if mid_price <= 0:
            return (False, "NO_PRICE")

        spread = best_ask - best_bid
        spread_bps = (spread / mid_price) * 10000

        if spread_bps > self.max_spread_bps:
            return (
                False,
                f"SPREAD_TOO_WIDE({spread_bps:.1f}bps>{self.max_spread_bps}bps)",
            )

        # --- 3. Depth check ---
        # For BUY: check ask-side depth (we consume asks)
        # For SELL: check bid-side depth (we consume bids)
        relevant_levels = asks if side == "BUY" else bids

        total_depth_usd = 0.0
        for price_str, qty_str in relevant_levels:
            price = float(price_str)
            qty = float(qty_str)
            total_depth_usd += price * qty

        required_depth = risk_usd * self.min_depth_multiplier

        if total_depth_usd < required_depth:
            return (
                False,
                f"DEPTH_INSUFFICIENT(${total_depth_usd:.0f}<${required_depth:.0f})",
            )

        # --- 4. Market impact estimate ---
        # Simulate filling risk_usd and check average price vs mid
        filled_usd = 0.0
        filled_qty = 0.0
        weighted_price_sum = 0.0

        for price_str, qty_str in relevant_levels:
            if filled_usd >= risk_usd:
                break

            price = float(price_str)
            qty = float(qty_str)
            value = price * qty

            # Take what we need
            take_usd = min(value, risk_usd - filled_usd)
            take_qty = take_usd / price

            filled_usd += take_usd
            filled_qty += take_qty
            weighted_price_sum += price * take_qty

        if filled_qty > 0:
            avg_fill_price = weighted_price_sum / filled_qty
            impact = abs(avg_fill_price - mid_price) / mid_price * 10000

            if impact > self.max_impact_bps:
                return (
                    False,
                    f"IMPACT_TOO_HIGH({impact:.1f}bps>{self.max_impact_bps}bps)",
                )
        else:
            # Edge case: couldn't simulate fill
            return (False, "DEPTH_INSUFFICIENT(simulation_failed)")

        # --- All checks passed ---
        return (True, "OK")
