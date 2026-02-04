# -*- coding: utf-8 -*-
# === AI SIGNATURE ===
# Created by: Claude (opus-4.5)
# Created at: 2026-01-30T21:30:00Z
# Purpose: Analyze MoonBot signal activity for AllowList recommendations
# === END SIGNATURE ===
"""
MoonBot Activity Analyzer

Analyzes signal log to recommend coins for AllowList based on:
- Signal frequency (activity level)
- Delta percentage (price movement)
- Strategy diversity (multiple detection methods)
- Volume characteristics
"""

import re
import json
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Any

# Raw log data from user
RAW_LOG = """
11:47:15   Signal USDT-SOMI Ask:0.26660 Delta: 1.48%
12:10:02   Signal USDT-D Ask:0.014060 Delta: 12.21%
12:40:55   Signal USDT-D Ask:0.014180 xPriceDelta: 3.3
12:45:07   Signal USDT-SOMI Ask:0.28390 Delta: 6.63%
12:49:31   Signal USDT-D Ask:0.013860 xPriceDelta: 3.0
12:50:06   Signal USDT-BIFI Ask:174.60 Delta: 18.61%
12:53:11   Signal USDT-BIFI Ask:184.70 xPriceDelta: 4.7
12:57:14   Signal USDT-BIFI Ask:177.50 xPriceDelta: 4.0
13:01:35   Signal USDT-BIFI Ask:179.70 xPriceDelta: 3.8
13:13:40   Signal USDT-BIFI Ask:177.20 xPriceDelta: 3.4
13:15:58   Signal USDT-NOM Ask:0.009910 xPriceDelta: 2.9
13:17:21   Signal USDT-ENSO Ask:1.30500 Delta: 6.37%
13:19:06   Signal USDT-SOMI Ask:0.29590 xPriceDelta: 3.4
13:20:05   Signal USDT-BIFI Ask:177.00 xPriceDelta: 3.7
13:22:20   Signal USDT-ENSO Ask:1.28200 xPriceDelta: 2.9
13:22:53   Signal USDT-D Ask:0.012800 xPriceDelta: 3.1
13:25:16   Signal USDT-D Ask:0.012880 Delta: 5.80%
13:26:06   Signal USDT-NOM Ask:0.009690 Delta: 5.48%
13:29:19   Signal USDT-SOMI Ask:0.29370 Delta: 5.38%
13:29:40   Signal USDT-ENSO Ask:1.34000 Delta: 5.64%
13:31:22   Signal USDT-BIFI Ask:173.30 xPriceDelta: 3.4
13:33:11   Signal USDT-SENT Ask:0.038420 xPriceDelta: 2.8
13:33:52   Signal USDT-SOMI Ask:0.28470 xPriceDelta: 3.0
13:34:01   Signal USDT-ENSO Ask:1.45700 xPriceDelta: 3.5
13:39:23   Signal USDT-BIFI Ask:165.50 xPriceDelta: 4.1
13:48:45   Signal USDT-BIFI Ask:165.70 Delta: 6.36%
13:56:15   Signal USDT-ZKC Ask:0.12480 Delta: 6.03%
14:00:01   Signal USDT-ZKC Ask:0.12450 xPriceDelta: 3.7
14:05:04   Signal USDT-BIFI Ask:167.10 xPriceDelta: 3.3
14:06:14   Signal USDT-ENSO Ask:1.49500 Delta: 7.17%
14:28:50   Signal USDT-0G Ask:0.85800 xPriceDelta: 3.0
14:28:50   Signal USDT-ENSO Ask:1.67100 xPriceDelta: 3.3
14:29:20   Signal USDT-SOMI Ask:0.28500 xPriceDelta: 2.7
14:29:39   Signal USDT-NOM Ask:0.010280 xPriceDelta: 2.7
14:29:45   Signal USDT-ZKC Ask:0.12620 xPriceDelta: 2.9
14:32:52   Signal USDT-INIT Ask:0.095200 xPriceDelta: 2.7
14:36:36   Signal USDT-ENSO Ask:1.68800 xPriceDelta: 3.6
14:39:08   Signal USDT-0G Ask:0.85100 xPriceDelta: 2.9
14:39:25   Signal USDT-NOM Ask:0.010370 xPriceDelta: 2.8
14:39:34   Signal USDT-ZKC Ask:0.13180 Delta: 6.72%
14:40:29   Signal USDT-ENSO Ask:1.61000 Delta: 8.74%
14:40:37   Signal USDT-ENSO Ask:1.62400 xPriceDelta: 3.9
14:41:08   Signal USDT-SOMI Ask:0.27670 xPriceDelta: 2.8
14:46:04   Signal USDT-ZKC Ask:0.13200 xPriceDelta: 3.0
14:51:29   Signal USDT-ZKC Ask:0.12840 Delta: 8.55%
14:52:15   Signal USDT-AUCTION Ask:5.84000 xPriceDelta: 2.9
14:55:21   Signal USDT-ENSO Ask:1.56100 xPriceDelta: 3.2
14:55:40   Signal USDT-ZKC Ask:0.12410 xPriceDelta: 2.9
15:01:05   Signal USDT-NOM Ask:0.010890 Delta: 7.98%
15:01:25   Signal USDT-NOM Ask:0.010760 xPriceDelta: 2.9
15:01:50   Signal USDT-SENT Ask:0.036360 xPriceDelta: 2.8
15:04:34   Signal USDT-ZKC Ask:0.12440 xPriceDelta: 3.1
15:16:24   Signal USDT-ENSO Ask:1.64000 Delta: 3.96%
15:20:08   Signal USDT-ENSO Ask:1.62500 xPriceDelta: 2.8
15:28:34   Signal USDT-ZKC Ask:0.12530 xPriceDelta: 2.8
15:31:23   Signal USDT-0G Ask:0.85900 xPriceDelta: 2.9
15:33:07   Signal USDT-EUL Ask:1.70000 Delta: 5.67%
15:35:02   Signal USDT-0G Ask:0.86600 Delta: 5.24%
15:36:50   Signal USDT-BIFI Ask:170.60 Delta: 5.05%
15:52:07   Signal USDT-ENSO Ask:1.62600 Delta: 3.95%
16:26:35   Signal USDT-NOM Ask:0.010110 xPriceDelta: 2.7
16:30:41   Signal USDT-AUCTION Ask:6.31000 xPriceDelta: 3.3
16:37:22   Signal USDT-AUCTION Ask:6.14000 xPriceDelta: 2.9
16:46:08   Signal USDT-FLOW Ask:0.064710 xPriceDelta: 3.2
16:50:10   Signal USDT-FLOW Ask:0.062960 xPriceDelta: 4.9
17:07:06   Signal USDT-ENSO Ask:1.60800 xPriceDelta: 2.9
17:08:36   Signal USDT-SPELL Ask:0.00023700 Delta: 4.31%
17:09:05   Signal USDT-ENSO Ask:1.58800 Delta: 4.38%
17:15:35   Signal USDT-USTC Ask:0.006640 Delta: 9.12%
17:18:28   Signal USDT-USTC Ask:0.006530 xPriceDelta: 3.4
17:24:55   Signal USDT-USTC Ask:0.006490 xPriceDelta: 2.9
17:37:12   Signal USDT-INIT Ask:0.10460 Delta: 7.34%
17:41:31   Signal USDT-INIT Ask:0.10150 xPriceDelta: 3.1
17:42:35   Signal USDT-FLOW Ask:0.063130 xPriceDelta: 2.9
17:43:20   Signal USDT-FLOW Ask:0.060770 Delta: 7.09%
17:50:04   Signal USDT-INIT Ask:0.10470 xPriceDelta: 2.9
17:58:33   Signal USDT-INIT Ask:0.10560 Delta: 4.97%
18:12:32   Signal USDT-ENSO Ask:1.57700 xPriceDelta: 2.8
18:14:04   Signal USDT-INIT Ask:0.10710 xPriceDelta: 2.9
18:14:15   Signal USDT-ENSO Ask:1.51400 Delta: 7.83%
18:27:13   Signal USDT-INIT Ask:0.10570 Delta: 6.33%
18:32:12   Signal USDT-ENSO Ask:1.51400 xPriceDelta: 2.9
19:06:36   Signal USDT-INIT Ask:0.10160 xPriceDelta: 3.9
19:09:03   Signal USDT-FIDA Ask:0.026900 Delta: 6.96%
19:26:44   Signal USDT-FIDA Ask:0.027800 xPriceDelta: 3.1
19:31:24   Signal USDT-FIDA Ask:0.027700 xPriceDelta: 3.6
19:32:15   Signal USDT-SENT Ask:0.035690 xPriceDelta: 2.7
19:50:59   Signal USDT-VANRY Ask:0.007622 xPriceDelta: 2.7
19:58:25   Signal USDT-VANRY Ask:0.007507 Delta: 6.45%
20:18:23   Signal USDT-币安人生 Ask:0.15510 xPriceDelta: 2.8
20:18:32   Signal USDT-币安人生 Ask:0.15380 Delta: 5.30%
20:20:07   Signal USDT-FIDA Ask:0.027000 xPriceDelta: 3.0
20:20:31   Signal USDT-FIDA Ask:0.026900 Delta: 6.55%
20:23:35   Signal USDT-币安人生 Ask:0.15390 Delta: 5.84%
20:24:41   Signal USDT-FIDA Ask:0.026700 Delta: 6.23%
20:27:13   Signal USDT-MANTA Ask:0.084500 xPriceDelta: 3.3
20:32:24   Signal USDT-MANTA Ask:0.082900 Delta: 6.23%
20:32:37   Signal USDT-FIDA Ask:0.026600 Delta: 6.12%
20:33:34   Signal USDT-MANTA Ask:0.081900 xPriceDelta: 2.9
21:03:38   Signal USDT-SYN PumpDetection Buys/sec: 31.54 PriceDelta: 2.0%
21:04:22   Signal USDT-ENA VolDetection
21:05:57   Signal USDT-SYN xPriceDelta: 7.1
21:07:46   Signal USDT-SYN PumpDetection Buys/sec: 19.58 PriceDelta: 2.1%
21:07:54   Signal USDT-FIDA xPriceDelta: 2.6
21:08:10   Signal USDT-FIDA xPriceDelta: 3.2
21:10:10   Signal USDT-MANTA Delta: 1.4%
21:11:07   Signal USDT-SYN xPriceDelta: 3.0
21:13:36   Signal USDT-MANTA Delta: 1.7%
21:13:43   Signal USDT-SYN PumpDetection Buys/sec: 6.30 PriceDelta: 2.0%
21:14:35   Signal USDT-SOMI PumpDetection Buys/sec: 47.56 PriceDelta: 2.0%
21:14:45   Signal USDT-SOMI VolDetection
21:15:10   Signal USDT-SYN xPriceDelta: 3.8
21:21:05   Signal USDT-SYN PumpDetection Buys/sec: 43.04 PriceDelta: 2.0%
21:21:32   Signal USDT-MANTA xPriceDelta: 2.7
21:23:45   Signal USDT-INIT xPriceDelta: 3.0
21:26:14   Signal USDT-SYN xPriceDelta: 2.6
21:26:31   Signal USDT-SYN PumpDetection Buys/sec: 11.50 PriceDelta: 2.0%
21:26:36   Signal USDT-INIT Delta: 2.2%
21:30:23   Signal USDT-FIDA Delta: 1.5%
21:32:29   Signal USDT-SYN xPriceDelta: 2.7
21:33:01   Signal USDT-SYN PumpDetection Buys/sec: 11.50 PriceDelta: 1.9%
21:35:14   Signal USDT-SYN Delta: 3.4%
21:35:50   Signal USDT-FLOW Delta: 2.2%
21:35:50   Signal USDT-FLOW xPriceDelta: 3.1
21:36:03   Signal USDT-FLOW xPriceDelta: 3.1
21:36:13   Signal USDT-FLOW PumpDetection Buys/sec: 98.63 PriceDelta: 2.0%
21:36:37   Signal USDT-FLOW Delta: 8.31%
21:38:58   Signal USDT-FLOW Delta: 2.8%
21:40:34   Signal USDT-FLOW PumpDetection Buys/sec: 51.11 PriceDelta: 1.9%
21:40:46   Signal USDT-FLOW Delta: 9.04%
21:42:03   Signal USDT-FLOW xPriceDelta: 2.9
21:42:07   Signal USDT-FLOW xPriceDelta: 3.4
21:43:23   Signal USDT-SYN xPriceDelta: 2.8
21:43:37   Signal USDT-FLOW Delta: 4.2%
21:44:32   Signal USDT-SYN PumpDetection Buys/sec: 4.01 PriceDelta: 1.9%
21:46:19   Signal USDT-SYN Delta: 1.4%
21:48:52   Signal USDT-FLOW PumpDetection Buys/sec: 61.38 PriceDelta: 1.9%
21:49:34   Signal USDT-FLOW xPriceDelta: 2.6
21:49:41   Signal USDT-FLOW xPriceDelta: 3.0
21:55:24   Signal USDT-FLOW xPriceDelta: 2.5
21:55:27   Signal USDT-SYN PumpDetection Buys/sec: 22.36 PriceDelta: 1.9%
21:55:55   Signal USDT-SYN Delta: 8.43%
21:56:11   Signal USDT-SYN xPriceDelta: 3.0
21:56:58   Signal USDT-SYN Delta: 3.8%
21:58:03   Signal USDT-ACT Delta: 1.3%
21:59:36   Signal USDT-SYN PumpDetection Buys/sec: 21.42 PriceDelta: 2.0%
22:00:42   Signal USDT-SYN xPriceDelta: 2.7
22:03:29   Signal USDT-FLOW xPriceDelta: 3.1
22:04:09   Signal USDT-SYN PumpDetection Buys/sec: 11.53 PriceDelta: 2.0%
22:04:45   Signal USDT-SYN xPriceDelta: 2.6
22:06:23   Signal USDT-FLOW Delta: 1.7%
22:06:24   Signal USDT-ENSO Delta: 1.4%
22:08:47   Signal USDT-SYN xPriceDelta: 2.5
22:09:25   Signal USDT-ENSO Delta: 2.7%
22:10:40   Signal USDT-ENSO xPriceDelta: 2.6
22:12:06   Signal USDT-FLOW Delta: 2.5%
22:13:39   Signal USDT-FLOW xPriceDelta: 2.6
22:14:14   Signal USDT-FLOW xPriceDelta: 3.0
22:15:27   Signal USDT-ENSO xPriceDelta: 2.5
22:17:24   Signal USDT-FLOW Delta: 5.85%
22:17:42   Signal USDT-ENSO xPriceDelta: 2.8
22:18:05   Signal USDT-SYN xPriceDelta: 2.5
22:18:42   Signal USDT-OPEN VolDetection
22:19:03   Signal USDT-SYN Delta: 5.85%
22:19:06   Signal USDT-FLOW PumpDetection Buys/sec: 54.58 PriceDelta: 1.9%
22:20:53   Signal USDT-FLOW Delta: 1.5%
22:23:17   Signal USDT-SYN Delta: 5.4%
22:25:16   Signal USDT-SYN PumpDetection Buys/sec: 21.30 PriceDelta: 2.1%
22:25:16   Signal USDT-FIDA Delta: 1.9%
22:25:30   Signal USDT-FIDA PumpDetection Buys/sec: 5.23 PriceDelta: 2.2%
22:26:11   Signal USDT-SYN xPriceDelta: 2.7
22:28:14   Signal USDT-FIDA xPriceDelta: 2.5
22:30:10   Signal USDT-SYN PumpDetection Buys/sec: 8.74 PriceDelta: 2.0%
22:31:42   Signal USDT-FLOW xPriceDelta: 2.8
22:32:20   Signal USDT-SYN Delta: 3.9%
22:34:40   Signal USDT-FLOW PumpDetection Buys/sec: 68.71 PriceDelta: 1.9%
22:35:46   Signal USDT-FLOW Delta: 1.8%
22:35:57   Signal USDT-FTT Delta: 4.0%
22:35:57   Signal USDT-FTT Delta: 11.06%
22:36:02   Signal USDT-FTT PumpDetection Buys/sec: 28.74 PriceDelta: 2.1%
22:37:58   Signal USDT-FTT xPriceDelta: 3.4
22:38:58   Signal USDT-FTT Delta: 5.4%
22:39:22   Signal USDT-FLOW PumpDetection Buys/sec: 59.17 PriceDelta: 1.9%
22:40:08   Signal USDT-FTT PumpDetection Buys/sec: 27.72 PriceDelta: 2.1%
22:40:12   Signal USDT-SYN xPriceDelta: 2.8
22:41:17   Signal USDT-FIDA xPriceDelta: 2.6
22:41:32   Signal USDT-FIDA xPriceDelta: 3.0
22:42:41   Signal USDT-FTT xPriceDelta: 4.1
22:42:47   Signal USDT-FLOW xPriceDelta: 2.6
22:42:57   Signal USDT-FLOW xPriceDelta: 3.0
22:44:31   Signal USDT-FLOW PumpDetection Buys/sec: 139.19 PriceDelta: 2.0%
22:46:44   Signal USDT-FTT xPriceDelta: 4.4
22:47:39   Signal USDT-FTT PumpDetection Buys/sec: 27.09 PriceDelta: 2.1%
22:48:41   Signal USDT-FLOW PumpDetection Buys/sec: 32.78 PriceDelta: 1.9%
22:50:53   Signal USDT-SYN xPriceDelta: 3.2
22:53:01   Signal USDT-FTT PumpDetection Buys/sec: 33.93 PriceDelta: 1.9%
22:54:44   Signal USDT-FLOW xPriceDelta: 2.7
22:55:14   Signal USDT-FTT xPriceDelta: 3.2
23:00:32   Signal USDT-FTT PumpDetection Buys/sec: 71.59 PriceDelta: 1.9%
23:01:54   Signal USDT-FTT Delta: 1.6%
23:03:12   Signal USDT-ENSO Delta: 1.7%
23:05:41   Signal USDT-0G PumpDetection Buys/sec: 15.53 PriceDelta: 1.9%
23:05:56   Signal USDT-FLOW Delta: 1.4%
23:06:32   Signal USDT-0G Delta: 2.5%
23:10:49   Signal USDT-FTT xPriceDelta: 3.2
23:20:07   Signal USDT-SYN PumpDetection Buys/sec: 17.27 PriceDelta: 1.9%
23:20:22   Signal USDT-FTT PumpDetection Buys/sec: 3.63 PriceDelta: 2.0%
23:20:50   Signal USDT-SYN Delta: 4.62%
23:21:36   Signal USDT-SYN Delta: 2.7%
23:22:03   Signal USDT-FTT xPriceDelta: 3.0
23:22:05   Signal USDT-FTT Delta: 1.5%
23:24:38   Signal USDT-SYN Delta: 3.3%
"""


def analyze_activity() -> Dict[str, Any]:
    """Analyze MoonBot signal activity."""

    # Count signals per coin
    coin_signals = defaultdict(list)
    coin_deltas = defaultdict(list)
    coin_strategies = defaultdict(set)

    # Parse simplified data
    lines = RAW_LOG.strip().split('\n')

    for line in lines:
        if not line.strip():
            continue

        # Extract symbol
        match = re.search(r'USDT-(\S+)', line)
        if not match:
            continue
        symbol = match.group(1) + "USDT"

        # Extract time
        time_match = re.search(r'(\d{2}:\d{2}:\d{2})', line)
        time_str = time_match.group(1) if time_match else "00:00:00"

        # Extract delta
        delta_match = re.search(r'Delta:\s*([\d.]+)%', line)
        xdelta_match = re.search(r'xPriceDelta:\s*([\d.]+)', line)

        delta = 0.0
        if delta_match:
            delta = float(delta_match.group(1))
        elif xdelta_match:
            delta = float(xdelta_match.group(1))

        # Detect strategy
        strategy = "Unknown"
        if "PumpDetection" in line:
            strategy = "PumpDetection"
        elif "VolDetection" in line:
            strategy = "VolumeDetection"
        elif "xPriceDelta" in line:
            strategy = "DropsDetection"
        elif "Delta:" in line:
            strategy = "TopMarket"

        coin_signals[symbol].append({
            "time": time_str,
            "delta": delta,
            "strategy": strategy
        })

        if delta > 0:
            coin_deltas[symbol].append(delta)
        coin_strategies[symbol].add(strategy)

    # Calculate metrics
    results = []
    for symbol, signals in coin_signals.items():
        deltas = coin_deltas.get(symbol, [0])
        strategies = coin_strategies.get(symbol, set())

        avg_delta = sum(deltas) / len(deltas) if deltas else 0
        max_delta = max(deltas) if deltas else 0

        # Score calculation
        # High frequency = good (active coin)
        # High delta = good (price movement)
        # Multiple strategies = good (confirmed signals)
        frequency_score = min(len(signals) / 5, 10)  # Max 10 points
        delta_score = min(avg_delta, 10)  # Max 10 points
        strategy_score = len(strategies) * 2.5  # Max 10 points (4 strategies)

        total_score = frequency_score + delta_score + strategy_score

        results.append({
            "symbol": symbol,
            "signal_count": len(signals),
            "avg_delta": round(avg_delta, 2),
            "max_delta": round(max_delta, 2),
            "strategies": list(strategies),
            "strategy_count": len(strategies),
            "score": round(total_score, 2),
            "recommendation": "ALLOW" if total_score >= 12 else ("REVIEW" if total_score >= 8 else "SKIP")
        })

    # Sort by score
    results.sort(key=lambda x: x["score"], reverse=True)

    return {
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "total_signals": sum(len(s) for s in coin_signals.values()),
        "unique_coins": len(coin_signals),
        "analysis": results
    }


def generate_allowlist_recommendation(analysis: Dict[str, Any]) -> str:
    """Generate AllowList recommendation based on analysis."""

    output = []
    output.append("=" * 70)
    output.append("AI ANALYSIS: MOONBOT ACTIVITY -> ALLOWLIST RECOMMENDATIONS")
    output.append("=" * 70)
    output.append(f"Timestamp: {analysis['timestamp']}")
    output.append(f"Total Signals Analyzed: {analysis['total_signals']}")
    output.append(f"Unique Coins: {analysis['unique_coins']}")
    output.append("")

    # Tier 1: ALLOW (high activity, high delta, multi-strategy)
    output.append("-" * 70)
    output.append("TIER 1: RECOMMENDED FOR ALLOWLIST (Score >= 12)")
    output.append("-" * 70)
    tier1 = [c for c in analysis["analysis"] if c["recommendation"] == "ALLOW"]
    for coin in tier1:
        output.append(f"  {coin['symbol']:12} | Signals: {coin['signal_count']:3} | "
                     f"Avg Delta: {coin['avg_delta']:5.1f}% | Max: {coin['max_delta']:5.1f}% | "
                     f"Strategies: {coin['strategy_count']} | Score: {coin['score']:5.1f}")

    # Tier 2: REVIEW
    output.append("")
    output.append("-" * 70)
    output.append("TIER 2: REVIEW BEFORE ADDING (Score 8-12)")
    output.append("-" * 70)
    tier2 = [c for c in analysis["analysis"] if c["recommendation"] == "REVIEW"]
    for coin in tier2:
        output.append(f"  {coin['symbol']:12} | Signals: {coin['signal_count']:3} | "
                     f"Avg Delta: {coin['avg_delta']:5.1f}% | Max: {coin['max_delta']:5.1f}% | "
                     f"Strategies: {coin['strategy_count']} | Score: {coin['score']:5.1f}")

    # Tier 3: SKIP
    output.append("")
    output.append("-" * 70)
    output.append("TIER 3: NOT RECOMMENDED (Score < 8)")
    output.append("-" * 70)
    tier3 = [c for c in analysis["analysis"] if c["recommendation"] == "SKIP"]
    for coin in tier3:
        output.append(f"  {coin['symbol']:12} | Signals: {coin['signal_count']:3} | "
                     f"Avg Delta: {coin['avg_delta']:5.1f}% | Max: {coin['max_delta']:5.1f}% | "
                     f"Strategies: {coin['strategy_count']} | Score: {coin['score']:5.1f}")

    # Generate AllowList snippet
    output.append("")
    output.append("=" * 70)
    output.append("SUGGESTED ALLOWLIST UPDATE")
    output.append("=" * 70)
    output.append("# Add to config/signal_filter_rules.json -> allowed_symbols:")
    output.append("")

    allowed = [c["symbol"] for c in tier1]
    output.append(json.dumps(allowed, indent=2))

    output.append("")
    output.append("=" * 70)
    output.append("SCORING METHODOLOGY")
    output.append("=" * 70)
    output.append("  Frequency Score: signals / 5 (max 10 pts)")
    output.append("  Delta Score: avg_delta (max 10 pts)")
    output.append("  Strategy Score: unique_strategies * 2.5 (max 10 pts)")
    output.append("  Total: 30 pts max")
    output.append("")
    output.append("  ALLOW:  >= 12 pts (active, volatile, multi-confirmed)")
    output.append("  REVIEW: 8-12 pts (moderate activity)")
    output.append("  SKIP:   < 8 pts (low activity or single strategy)")
    output.append("=" * 70)

    return "\n".join(output)


def main():
    """Main entry point."""
    print("Analyzing MoonBot signal activity...")

    analysis = analyze_activity()

    # Save raw analysis
    output_path = Path("state/ai/moonbot_activity_analysis.json")
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(analysis, f, indent=2, ensure_ascii=False)

    print(f"Raw analysis saved to: {output_path}")

    # Generate and print recommendation
    recommendation = generate_allowlist_recommendation(analysis)
    print()
    print(recommendation)

    # Save recommendation
    rec_path = Path("state/ai/allowlist_recommendation.txt")
    with open(rec_path, "w", encoding="utf-8") as f:
        f.write(recommendation)

    print(f"\nRecommendation saved to: {rec_path}")

    return analysis


if __name__ == "__main__":
    main()
