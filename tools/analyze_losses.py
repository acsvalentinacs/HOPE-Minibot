# -*- coding: utf-8 -*-
# === AI SIGNATURE ===
# Created by: Claude (opus-4.5)
# Created at: 2026-02-05T02:50:00Z
# Purpose: Analyze losing trades to find patterns and train AI
# === END SIGNATURE ===
"""
Loss Pattern Analyzer - анализирует убыточные сделки для обучения AI.

Выходные данные:
1. patterns.json - найденные паттерны убытков
2. ai_training_data.jsonl - данные для обучения модели
3. recommendations.md - рекомендации по улучшению
"""

import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Input file
TRADES_FILE = Path(__file__).parent.parent / "tmp_import" / "trades_7days.json"
OUTPUT_DIR = Path(__file__).parent.parent / "state" / "ai"

def analyze_trades():
    """Main analysis function."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # Load trades
    with open(TRADES_FILE, encoding='utf-8') as f:
        data = json.load(f)

    trades = data.get("trades", [])
    summary = data.get("summary", {})

    print(f"\n{'='*60}")
    print(f"📊 TRADE ANALYSIS REPORT")
    print(f"{'='*60}")
    print(f"Total trades: {len(trades)}")
    print(f"Summary: {json.dumps(summary, indent=2)}")

    # Separate wins and losses
    wins = [t for t in trades if t.get("profit_usd", 0) > 0]
    losses = [t for t in trades if t.get("profit_usd", 0) <= 0]

    print(f"\nWins: {len(wins)}, Losses: {len(losses)}")

    # === PATTERN 1: By Symbol ===
    print(f"\n{'='*60}")
    print("📈 PATTERN 1: Performance by Symbol")
    print(f"{'='*60}")

    symbol_stats = defaultdict(lambda: {"wins": 0, "losses": 0, "total_pnl": 0.0, "trades": []})

    for t in trades:
        coin = t.get("coin", "UNKNOWN")
        pnl = t.get("profit_usd", 0)

        symbol_stats[coin]["total_pnl"] += pnl
        symbol_stats[coin]["trades"].append(t)

        if pnl > 0:
            symbol_stats[coin]["wins"] += 1
        else:
            symbol_stats[coin]["losses"] += 1

    # Sort by PnL
    sorted_symbols = sorted(symbol_stats.items(), key=lambda x: x[1]["total_pnl"])

    print("\n🔴 WORST PERFORMERS (BLACKLIST CANDIDATES):")
    for coin, stats in sorted_symbols[:10]:
        total = stats["wins"] + stats["losses"]
        wr = stats["wins"] / total * 100 if total > 0 else 0
        print(f"  {coin:10} | PnL: ${stats['total_pnl']:+.2f} | WR: {wr:.0f}% ({stats['wins']}/{total})")

    print("\n🟢 BEST PERFORMERS:")
    for coin, stats in sorted_symbols[-5:]:
        total = stats["wins"] + stats["losses"]
        wr = stats["wins"] / total * 100 if total > 0 else 0
        print(f"  {coin:10} | PnL: ${stats['total_pnl']:+.2f} | WR: {wr:.0f}% ({stats['wins']}/{total})")

    # === PATTERN 2: By Time ===
    print(f"\n{'='*60}")
    print("⏰ PATTERN 2: Performance by Hour (UTC)")
    print(f"{'='*60}")

    hour_stats = defaultdict(lambda: {"wins": 0, "losses": 0, "pnl": 0.0})

    for t in trades:
        try:
            buy_date = t.get("buy_date", "")
            if buy_date:
                dt = datetime.fromisoformat(buy_date.replace("Z", "+00:00"))
                hour = dt.hour
                pnl = t.get("profit_usd", 0)

                hour_stats[hour]["pnl"] += pnl
                if pnl > 0:
                    hour_stats[hour]["wins"] += 1
                else:
                    hour_stats[hour]["losses"] += 1
        except Exception:
            pass

    print("\n🔴 WORST HOURS (avoid trading):")
    sorted_hours = sorted(hour_stats.items(), key=lambda x: x[1]["pnl"])
    for hour, stats in sorted_hours[:5]:
        total = stats["wins"] + stats["losses"]
        wr = stats["wins"] / total * 100 if total > 0 else 0
        print(f"  {hour:02d}:00 UTC | PnL: ${stats['pnl']:+.2f} | WR: {wr:.0f}% ({total} trades)")

    # === PATTERN 3: By Trade Size ===
    print(f"\n{'='*60}")
    print("💰 PATTERN 3: Performance by Position Size")
    print(f"{'='*60}")

    size_brackets = {
        "micro (<$5)": (0, 5),
        "small ($5-10)": (5, 10),
        "medium ($10-20)": (10, 20),
        "large (>$20)": (20, 1000),
    }

    size_stats = {k: {"wins": 0, "losses": 0, "pnl": 0.0} for k in size_brackets}

    for t in trades:
        spent = t.get("spent_usd", 0)
        pnl = t.get("profit_usd", 0)

        for bracket, (low, high) in size_brackets.items():
            if low <= spent < high:
                size_stats[bracket]["pnl"] += pnl
                if pnl > 0:
                    size_stats[bracket]["wins"] += 1
                else:
                    size_stats[bracket]["losses"] += 1
                break

    for bracket, stats in size_stats.items():
        total = stats["wins"] + stats["losses"]
        wr = stats["wins"] / total * 100 if total > 0 else 0
        print(f"  {bracket:15} | PnL: ${stats['pnl']:+.2f} | WR: {wr:.0f}% ({total} trades)")

    # === PATTERN 4: Rapid Losses ===
    print(f"\n{'='*60}")
    print("⚡ PATTERN 4: Rapid Losses (bought & sold within seconds)")
    print(f"{'='*60}")

    rapid_losses = []
    for t in losses:
        buy_date = t.get("buy_date")
        sell_date = t.get("sell_date")

        if buy_date and sell_date:
            try:
                buy_dt = datetime.fromisoformat(buy_date.replace("Z", "+00:00"))
                sell_dt = datetime.fromisoformat(sell_date.replace("Z", "+00:00"))
                duration = (sell_dt - buy_dt).total_seconds()

                if duration < 60:  # Under 1 minute
                    rapid_losses.append({
                        "coin": t.get("coin"),
                        "duration_sec": duration,
                        "loss": t.get("profit_usd", 0),
                        "pct": t.get("profit_pct", 0)
                    })
            except Exception:
                pass

    print(f"Rapid losses (< 1 min): {len(rapid_losses)}")
    if rapid_losses:
        total_rapid_loss = sum(r["loss"] for r in rapid_losses)
        print(f"Total rapid loss: ${total_rapid_loss:.2f}")
        print("Top rapid losses:")
        for r in sorted(rapid_losses, key=lambda x: x["loss"])[:5]:
            print(f"  {r['coin']:10} | ${r['loss']:+.2f} ({r['pct']:.2f}%) in {r['duration_sec']:.0f}s")

    # === PATTERN 5: Consecutive Losses ===
    print(f"\n{'='*60}")
    print("📉 PATTERN 5: Loss Streaks")
    print(f"{'='*60}")

    # Sort trades by time
    trades_sorted = sorted(trades, key=lambda x: x.get("buy_date", ""))

    max_streak = 0
    current_streak = 0
    streak_symbols = []

    for t in trades_sorted:
        if t.get("profit_usd", 0) <= 0:
            current_streak += 1
            streak_symbols.append(t.get("coin"))
        else:
            if current_streak > max_streak:
                max_streak = current_streak
            current_streak = 0
            streak_symbols = []

    print(f"Max loss streak: {max_streak} trades")

    # === GENERATE RECOMMENDATIONS ===
    print(f"\n{'='*60}")
    print("🎯 AI RECOMMENDATIONS")
    print(f"{'='*60}")

    recommendations = []

    # Blacklist recommendation
    blacklist_candidates = [coin for coin, stats in sorted_symbols[:5]
                           if stats["wins"] + stats["losses"] >= 3 and
                           stats["wins"] / (stats["wins"] + stats["losses"]) < 0.35]

    if blacklist_candidates:
        rec = f"BLACKLIST: {', '.join(blacklist_candidates)} (WR < 35% on 3+ trades)"
        recommendations.append(rec)
        print(f"  1. {rec}")

    # Timing recommendation
    worst_hours = [h for h, _ in sorted_hours[:3]]
    if worst_hours:
        rec = f"AVOID HOURS: {', '.join(f'{h}:00' for h in worst_hours)} UTC"
        recommendations.append(rec)
        print(f"  2. {rec}")

    # Rapid loss recommendation
    if len(rapid_losses) > 5:
        rec = f"IMPLEMENT QUICK LOSS RULE: Close if -0.5% in first 5 min (would save ${abs(total_rapid_loss):.2f})"
        recommendations.append(rec)
        print(f"  3. {rec}")

    # Position size recommendation
    best_size = max(size_stats.items(), key=lambda x: x[1]["pnl"] if x[1]["wins"] + x[1]["losses"] > 0 else -1000)
    rec = f"OPTIMAL SIZE: {best_size[0]} bracket shows best performance"
    recommendations.append(rec)
    print(f"  4. {rec}")

    # === SAVE OUTPUTS ===

    # 1. Patterns JSON
    patterns = {
        "analysis_time": datetime.now(timezone.utc).isoformat(),
        "total_trades": len(trades),
        "win_rate": len(wins) / len(trades) * 100 if trades else 0,
        "worst_symbols": [
            {"coin": coin, "pnl": stats["total_pnl"], "win_rate": stats["wins"] / (stats["wins"] + stats["losses"]) * 100 if stats["wins"] + stats["losses"] > 0 else 0}
            for coin, stats in sorted_symbols[:10]
        ],
        "best_symbols": [
            {"coin": coin, "pnl": stats["total_pnl"], "win_rate": stats["wins"] / (stats["wins"] + stats["losses"]) * 100 if stats["wins"] + stats["losses"] > 0 else 0}
            for coin, stats in sorted_symbols[-5:]
        ],
        "worst_hours": [{"hour": h, "pnl": s["pnl"]} for h, s in sorted_hours[:5]],
        "rapid_loss_count": len(rapid_losses),
        "rapid_loss_total": sum(r["loss"] for r in rapid_losses),
        "max_loss_streak": max_streak,
        "recommendations": recommendations,
        "blacklist_candidates": blacklist_candidates,
    }

    patterns_file = OUTPUT_DIR / "loss_patterns.json"
    with open(patterns_file, 'w', encoding='utf-8') as f:
        json.dump(patterns, f, indent=2)
    print(f"\n✅ Saved patterns: {patterns_file}")

    # 2. AI Training Data (JSONL)
    training_file = OUTPUT_DIR / "ai_training_data.jsonl"
    with open(training_file, 'w', encoding='utf-8') as f:
        for t in trades:
            # Create training sample
            sample = {
                "input": {
                    "coin": t.get("coin"),
                    "hour_utc": 0,  # Will be filled
                    "spent_usd": t.get("spent_usd", 0),
                    "price": t.get("buy_price", 0),
                },
                "output": {
                    "is_win": 1 if t.get("profit_usd", 0) > 0 else 0,
                    "profit_pct": t.get("profit_pct", 0),
                    "profit_usd": t.get("profit_usd", 0),
                },
                "label": "WIN" if t.get("profit_usd", 0) > 0 else "LOSS"
            }

            try:
                buy_date = t.get("buy_date", "")
                if buy_date:
                    dt = datetime.fromisoformat(buy_date.replace("Z", "+00:00"))
                    sample["input"]["hour_utc"] = dt.hour
                    sample["input"]["day_of_week"] = dt.weekday()
            except Exception:
                pass

            f.write(json.dumps(sample) + "\n")

    print(f"✅ Saved training data: {training_file}")

    # 3. Recommendations MD
    rec_file = OUTPUT_DIR / "recommendations.md"
    with open(rec_file, 'w', encoding='utf-8') as f:
        f.write("# 🎯 AI-Generated Trading Recommendations\n\n")
        f.write(f"Analysis time: {patterns['analysis_time']}\n")
        f.write(f"Based on: {len(trades)} trades\n\n")

        f.write("## 📊 Summary\n\n")
        f.write(f"- Win Rate: {patterns['win_rate']:.1f}%\n")
        f.write(f"- Max Loss Streak: {max_streak}\n")
        f.write(f"- Rapid Losses: {len(rapid_losses)} (${abs(patterns['rapid_loss_total']):.2f})\n\n")

        f.write("## 🔴 Blacklist Candidates\n\n")
        for coin in blacklist_candidates:
            f.write(f"- {coin}\n")

        f.write("\n## ⏰ Avoid Trading Hours (UTC)\n\n")
        for h, s in sorted_hours[:3]:
            f.write(f"- {h:02d}:00 (PnL: ${s['pnl']:.2f})\n")

        f.write("\n## ✅ Recommendations\n\n")
        for i, rec in enumerate(recommendations, 1):
            f.write(f"{i}. {rec}\n")

    print(f"✅ Saved recommendations: {rec_file}")

    return patterns


if __name__ == "__main__":
    patterns = analyze_trades()
    print(f"\n{'='*60}")
    print("✅ ANALYSIS COMPLETE")
    print(f"{'='*60}")
