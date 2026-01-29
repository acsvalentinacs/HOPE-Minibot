# -*- coding: utf-8 -*-
# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-30 03:35:00 UTC
# Purpose: Eye of God Controller - Session management and Claude directives
# Contract: Valentin approves, Claude manages, system executes
# === END SIGNATURE ===
"""
EYE CONTROLLER - Управление Глазом Бога

Функции:
1. Отчёты для Claude анализа
2. Применение директив от Claude
3. Управление сессиями
4. Мониторинг состояния

Команды:
  python eye_controller.py --report           # Создать отчёт для Claude
  python eye_controller.py --status           # Текущий статус
  python eye_controller.py --apply directive.json  # Применить директиву
  python eye_controller.py --session          # Текущая сессия

Типы директив:
  FOCUS: Сфокусироваться на символах
  PAUSE: Приостановить символы
  ADJUST: Изменить параметры
  BLACKLIST: Добавить в чёрный список
  WHITELIST: Добавить в белый список
  RISK: Изменить уровень риска
"""

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).parent.parent))

# Paths
STATE_DIR = Path("state/ai/production")
STATS_FILE = STATE_DIR / "stats.json"
TRADES_FILE = STATE_DIR / "trades.jsonl"
DIRECTIVES_FILE = STATE_DIR / "directives.jsonl"
REPORTS_DIR = STATE_DIR / "reports"


class EyeController:
    """Controller for Eye of God management."""

    def __init__(self):
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        REPORTS_DIR.mkdir(parents=True, exist_ok=True)

    def get_current_session(self) -> Dict:
        """Get current trading session info."""
        from scripts.hope_production_engine import TradingSession, SESSION_CONFIG

        hour = datetime.now(timezone.utc).hour

        for session, config in SESSION_CONFIG.items():
            start, end = config["hours"]
            if start <= hour < end:
                return {
                    "session": session.value,
                    "hours": f"{start:02d}:00-{end:02d}:00 UTC",
                    "risk_mult": config["risk_mult"],
                    "strategy": config["strategy"],
                    "min_buys_sec": config["min_buys_sec"],
                    "current_hour": hour,
                }

        return {"session": "UNKNOWN", "current_hour": hour}

    def get_status(self) -> Dict:
        """Get full system status."""
        status = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "session": self.get_current_session(),
            "config": self._get_config(),
            "stats": self._get_stats(),
            "open_positions": self._get_positions(),
            "recent_trades": self._get_recent_trades(10),
        }
        return status

    def _get_config(self) -> Dict:
        """Get oracle configuration."""
        try:
            from core.oracle_config import get_config_manager
            cm = get_config_manager()
            cfg = cm.config
            return {
                "whitelist": sorted(cfg.whitelist),
                "blacklist": sorted(cfg.blacklist),
                "paused": sorted(cfg.paused) if hasattr(cfg, 'paused') else [],
                "min_confidence": cfg.min_confidence,
                "calibration": cfg.calibration,
                "risk_multiplier": cfg.risk_multiplier,
            }
        except Exception as e:
            return {"error": str(e)}

    def _get_stats(self) -> Dict:
        """Get trading statistics."""
        if STATS_FILE.exists():
            try:
                return json.loads(STATS_FILE.read_text(encoding="utf-8"))
            except Exception:
                pass
        return {"total_trades": 0}

    def _get_positions(self) -> List[Dict]:
        """Get open positions."""
        positions_file = STATE_DIR / "positions.json"
        if positions_file.exists():
            try:
                data = json.loads(positions_file.read_text(encoding="utf-8"))
                return [p for p in data.get("positions", []) if p.get("status") == "OPEN"]
            except Exception:
                pass
        return []

    def _get_recent_trades(self, limit: int = 10) -> List[Dict]:
        """Get recent trades from log."""
        if not TRADES_FILE.exists():
            return []

        trades = []
        try:
            lines = TRADES_FILE.read_text(encoding="utf-8").strip().split("\n")
            for line in lines[-limit:]:
                if line.strip():
                    trades.append(json.loads(line))
        except Exception:
            pass

        return trades

    def generate_report(self) -> Dict:
        """
        Generate comprehensive report for Claude analysis.

        Format designed for AI comprehension and decision-making.
        """
        status = self.get_status()
        stats = status.get("stats", {})

        # Calculate metrics
        total = stats.get("total_trades", 0)
        wins = stats.get("wins", 0)
        losses = stats.get("losses", 0)
        win_rate = wins / total if total > 0 else 0

        # Symbol performance
        by_symbol = stats.get("by_symbol", {})
        symbol_performance = []
        for symbol, sym_stats in by_symbol.items():
            s_wins = sym_stats.get("wins", 0)
            s_losses = sym_stats.get("losses", 0)
            s_total = s_wins + s_losses
            s_win_rate = s_wins / s_total if s_total > 0 else 0
            symbol_performance.append({
                "symbol": symbol,
                "trades": s_total,
                "wins": s_wins,
                "losses": s_losses,
                "win_rate": f"{s_win_rate:.1%}",
                "pnl": sym_stats.get("pnl", 0),
                "recommendation": self._symbol_recommendation(s_win_rate, s_total),
            })

        # Sort by win rate
        symbol_performance.sort(key=lambda x: float(x["win_rate"].rstrip("%")), reverse=True)

        # Session performance
        by_session = stats.get("by_session", {})
        session_performance = []
        for session, sess_stats in by_session.items():
            s_wins = sess_stats.get("wins", 0)
            s_losses = sess_stats.get("losses", 0)
            s_total = s_wins + s_losses
            s_win_rate = s_wins / s_total if s_total > 0 else 0
            session_performance.append({
                "session": session,
                "trades": s_total,
                "win_rate": f"{s_win_rate:.1%}",
                "pnl": sess_stats.get("pnl", 0),
            })

        report = {
            "report_id": f"report_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "current_session": status["session"],

            "summary": {
                "total_trades": total,
                "win_rate": f"{win_rate:.1%}",
                "wins": wins,
                "losses": losses,
                "open_positions": len(status.get("open_positions", [])),
            },

            "configuration": status.get("config", {}),

            "symbol_analysis": symbol_performance[:10],  # Top 10

            "session_analysis": session_performance,

            "recent_activity": status.get("recent_trades", [])[-5:],

            "recommendations": self._generate_recommendations(stats, status),

            "directive_suggestions": self._suggest_directives(stats),
        }

        # Save report
        report_file = REPORTS_DIR / f"{report['report_id']}.json"
        with open(report_file, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2, ensure_ascii=False)

        return report

    def _symbol_recommendation(self, win_rate: float, total: int) -> str:
        """Generate recommendation for a symbol."""
        if total < 3:
            return "INSUFFICIENT_DATA"
        if win_rate >= 0.80:
            return "STRONG_BUY"
        if win_rate >= 0.60:
            return "BUY"
        if win_rate >= 0.40:
            return "HOLD"
        if win_rate >= 0.20:
            return "REDUCE"
        return "AVOID"

    def _generate_recommendations(self, stats: Dict, status: Dict) -> List[str]:
        """Generate actionable recommendations."""
        recommendations = []

        total = stats.get("total_trades", 0)
        wins = stats.get("wins", 0)
        win_rate = wins / total if total > 0 else 0

        if total < 10:
            recommendations.append("NEED_MORE_DATA: Less than 10 trades, continue in current mode")

        if win_rate < 0.40 and total >= 10:
            recommendations.append("LOW_WIN_RATE: Consider reducing position size")

        if win_rate > 0.70 and total >= 20:
            recommendations.append("HIGH_WIN_RATE: Can consider increasing position size")

        # Check for underperforming symbols
        by_symbol = stats.get("by_symbol", {})
        for symbol, sym_stats in by_symbol.items():
            s_wins = sym_stats.get("wins", 0)
            s_losses = sym_stats.get("losses", 0)
            s_total = s_wins + s_losses
            if s_total >= 3:
                s_win_rate = s_wins / s_total
                if s_win_rate <= 0.20:
                    recommendations.append(f"BLACKLIST_CANDIDATE: {symbol} (win_rate={s_win_rate:.0%})")
                elif s_win_rate >= 0.80:
                    recommendations.append(f"WHITELIST_CANDIDATE: {symbol} (win_rate={s_win_rate:.0%})")

        # Session recommendations
        session = status.get("session", {}).get("session")
        if session == "NIGHT":
            recommendations.append("NIGHT_SESSION: Minimal risk mode active, pump overrides only")
        elif session == "US_OPEN":
            recommendations.append("US_OPEN_SESSION: High volatility expected, risk multiplier 1.2x")

        return recommendations

    def _suggest_directives(self, stats: Dict) -> List[Dict]:
        """Suggest directives based on analysis."""
        suggestions = []

        by_symbol = stats.get("by_symbol", {})
        for symbol, sym_stats in by_symbol.items():
            s_wins = sym_stats.get("wins", 0)
            s_losses = sym_stats.get("losses", 0)
            s_total = s_wins + s_losses
            if s_total >= 3:
                s_win_rate = s_wins / s_total
                if s_win_rate <= 0.20:
                    suggestions.append({
                        "type": "BLACKLIST",
                        "params": {"symbol": symbol},
                        "reason": f"Low win rate: {s_win_rate:.0%} over {s_total} trades",
                        "priority": "HIGH"
                    })
                elif s_win_rate >= 0.80:
                    suggestions.append({
                        "type": "WHITELIST",
                        "params": {"symbol": symbol},
                        "reason": f"High win rate: {s_win_rate:.0%} over {s_total} trades",
                        "priority": "MEDIUM"
                    })

        return suggestions

    def apply_directive(self, directive: Dict) -> Dict:
        """
        Apply a directive from Claude.

        Directive format:
        {
            "type": "FOCUS|PAUSE|BLACKLIST|WHITELIST|RISK|ADJUST",
            "params": {...},
            "reason": "Explanation",
            "approved_by": "Claude" or "Valentin"
        }
        """
        directive_type = directive.get("type", "").upper()
        params = directive.get("params", {})
        reason = directive.get("reason", "")

        result = {"success": False, "type": directive_type}

        try:
            from core.oracle_config import get_config_manager
            cm = get_config_manager()
            cfg = cm.config

            if directive_type == "BLACKLIST":
                symbol = params.get("symbol")
                if symbol:
                    cfg.blacklist.add(symbol)
                    cfg.whitelist.discard(symbol)
                    result["success"] = True
                    result["action"] = f"Added {symbol} to blacklist"

            elif directive_type == "WHITELIST":
                symbol = params.get("symbol")
                if symbol:
                    cfg.whitelist.add(symbol)
                    cfg.blacklist.discard(symbol)
                    result["success"] = True
                    result["action"] = f"Added {symbol} to whitelist"

            elif directive_type == "PAUSE":
                symbols = params.get("symbols", [])
                if not hasattr(cfg, 'paused'):
                    cfg.paused = set()
                cfg.paused.update(symbols)
                result["success"] = True
                result["action"] = f"Paused symbols: {symbols}"

            elif directive_type == "FOCUS":
                symbols = params.get("symbols", [])
                cfg.whitelist = set(symbols)
                result["success"] = True
                result["action"] = f"Focused on: {symbols}"

            elif directive_type == "RISK":
                multiplier = params.get("multiplier", 1.0)
                cfg.risk_multiplier = multiplier
                result["success"] = True
                result["action"] = f"Risk multiplier set to {multiplier}"

            elif directive_type == "ADJUST":
                for key, value in params.items():
                    if hasattr(cfg, key):
                        setattr(cfg, key, value)
                        result["success"] = True
                result["action"] = f"Adjusted: {list(params.keys())}"

            else:
                result["error"] = f"Unknown directive type: {directive_type}"
                return result

            # Save config
            cm.save(cfg, f"directive:{directive_type}")

            # Log directive
            self._log_directive(directive, result)

        except Exception as e:
            result["error"] = str(e)

        return result

    def _log_directive(self, directive: Dict, result: Dict):
        """Log applied directive."""
        DIRECTIVES_FILE.parent.mkdir(parents=True, exist_ok=True)

        entry = {
            "directive": directive,
            "result": result,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        # Add sha256
        canonical = json.dumps(
            {k: v for k, v in entry.items() if k != "sha256"},
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False
        ).encode()
        entry["sha256"] = "sha256:" + hashlib.sha256(canonical).hexdigest()[:16]

        with open(DIRECTIVES_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def main():
    parser = argparse.ArgumentParser(description="Eye of God Controller")
    parser.add_argument("--report", action="store_true", help="Generate report for Claude")
    parser.add_argument("--status", action="store_true", help="Show current status")
    parser.add_argument("--session", action="store_true", help="Show current session")
    parser.add_argument("--apply", type=str, help="Apply directive from JSON file")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    args = parser.parse_args()

    controller = EyeController()

    if args.session:
        session = controller.get_current_session()
        if args.json:
            print(json.dumps(session, indent=2))
        else:
            print(f"Session: {session['session']}")
            print(f"Hours: {session.get('hours', 'N/A')}")
            print(f"Risk: {session.get('risk_mult', 1.0)}x")
            print(f"Strategy: {session.get('strategy', 'N/A')}")

    elif args.status:
        status = controller.get_status()
        print(json.dumps(status, indent=2))

    elif args.report:
        report = controller.generate_report()
        if args.json:
            print(json.dumps(report, indent=2))
        else:
            print(f"Report generated: {report['report_id']}")
            print(f"\nSummary:")
            for k, v in report["summary"].items():
                print(f"  {k}: {v}")
            print(f"\nRecommendations:")
            for rec in report["recommendations"]:
                print(f"  - {rec}")
            print(f"\nSuggested directives: {len(report['directive_suggestions'])}")
            for sug in report["directive_suggestions"][:3]:
                print(f"  - {sug['type']}: {sug.get('params', {})}")

    elif args.apply:
        directive_file = Path(args.apply)
        if not directive_file.exists():
            print(f"Error: File not found: {args.apply}")
            sys.exit(1)

        directive = json.loads(directive_file.read_text(encoding="utf-8"))
        result = controller.apply_directive(directive)
        print(json.dumps(result, indent=2))

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
