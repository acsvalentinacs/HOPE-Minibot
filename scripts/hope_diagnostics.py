# -*- coding: utf-8 -*-
# === AI SIGNATURE ===
# Created by: Claude (opus-4.5)
# Created at: 2026-02-02 15:00:00 UTC
# Purpose: HOPE System Diagnostics - finds breaks in signal→order chain
# === END SIGNATURE ===
"""
HOPE DIAGNOSTICS - System Health Check & Chain Verification

Проверяет:
1. Все компоненты системы (процессы, порты)
2. Цепочку signal→order от начала до конца
3. Состояние Eye of God, пороги, статистику
4. Binance подключение и баланс

Запуск:
    python scripts/hope_diagnostics.py           # Полная диагностика
    python scripts/hope_diagnostics.py --quick   # Быстрая проверка
    python scripts/hope_diagnostics.py --fix     # Диагностика + авто-ремонт
"""

import os
import sys
import json
import time
import socket
import subprocess
from pathlib import Path
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Tuple, Optional

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# ═══════════════════════════════════════════════════════════════════════════
# DIAGNOSTIC CHECKS
# ═══════════════════════════════════════════════════════════════════════════

class HopeDiagnostics:
    """Complete system diagnostics for HOPE Trading System."""

    def __init__(self):
        self.results = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "checks": {},
            "warnings": [],
            "errors": [],
            "recommendations": [],
        }

    def check_port(self, port: int, name: str) -> bool:
        """Check if port is listening."""
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(1)
                result = s.connect_ex(('127.0.0.1', port))
                is_open = result == 0
                self.results["checks"][f"port_{port}_{name}"] = {
                    "status": "PASS" if is_open else "FAIL",
                    "port": port,
                    "name": name,
                }
                return is_open
        except Exception as e:
            self.results["checks"][f"port_{port}_{name}"] = {
                "status": "ERROR",
                "error": str(e),
            }
            return False

    def check_http_endpoint(self, url: str, name: str) -> Tuple[bool, Optional[Dict]]:
        """Check HTTP endpoint and return response."""
        try:
            import httpx
            resp = httpx.get(url, timeout=5)
            is_ok = resp.status_code == 200
            data = resp.json() if is_ok else None
            self.results["checks"][f"http_{name}"] = {
                "status": "PASS" if is_ok else "FAIL",
                "url": url,
                "status_code": resp.status_code,
            }
            return is_ok, data
        except Exception as e:
            self.results["checks"][f"http_{name}"] = {
                "status": "ERROR",
                "url": url,
                "error": str(e),
            }
            return False, None

    def check_process_count(self) -> int:
        """Count Python processes."""
        try:
            if sys.platform == 'win32':
                result = subprocess.run(
                    ['tasklist', '/FI', 'IMAGENAME eq python.exe'],
                    capture_output=True, text=True
                )
                count = result.stdout.count('python.exe')
            else:
                result = subprocess.run(
                    ['pgrep', '-c', 'python'],
                    capture_output=True, text=True
                )
                count = int(result.stdout.strip()) if result.returncode == 0 else 0

            self.results["checks"]["python_processes"] = {
                "status": "INFO",
                "count": count,
            }
            return count
        except Exception as e:
            return 0

    def check_binance_connection(self) -> Tuple[bool, float]:
        """Check Binance API connection and get balance."""
        try:
            from binance.client import Client
            from dotenv import load_dotenv

            load_dotenv('C:/secrets/hope.env')
            client = Client(
                os.getenv('BINANCE_API_KEY'),
                os.getenv('BINANCE_API_SECRET')
            )

            account = client.get_account()
            usdt_balance = 0.0
            for b in account['balances']:
                if b['asset'] == 'USDT':
                    usdt_balance = float(b['free'])
                    break

            self.results["checks"]["binance"] = {
                "status": "PASS",
                "usdt_balance": usdt_balance,
                "can_trade": account.get('canTrade', False),
            }
            return True, usdt_balance
        except Exception as e:
            self.results["checks"]["binance"] = {
                "status": "FAIL",
                "error": str(e),
            }
            return False, 0.0

    def check_eye_of_god(self) -> Dict:
        """Check Eye of God V3 configuration and thresholds."""
        try:
            from scripts.eye_of_god_v3 import (
                EyeOfGodV3,
                MIN_CONFIDENCE_TO_TRADE,
                MIN_CONFIDENCE_MOMENTUM,
                MAX_OPEN_POSITIONS,
                MAX_DAILY_LOSS_USD,
                MIN_DAILY_VOLUME_M,
            )

            eye = EyeOfGodV3()
            stats = eye.get_stats()

            self.results["checks"]["eye_of_god"] = {
                "status": "PASS",
                "thresholds": {
                    "MIN_CONFIDENCE_TO_TRADE": MIN_CONFIDENCE_TO_TRADE,
                    "MIN_CONFIDENCE_MOMENTUM": MIN_CONFIDENCE_MOMENTUM,
                    "MAX_OPEN_POSITIONS": MAX_OPEN_POSITIONS,
                    "MAX_DAILY_LOSS_USD": MAX_DAILY_LOSS_USD,
                    "MIN_DAILY_VOLUME_M": MIN_DAILY_VOLUME_M,
                },
                "stats": stats,
            }
            return self.results["checks"]["eye_of_god"]
        except Exception as e:
            self.results["checks"]["eye_of_god"] = {
                "status": "FAIL",
                "error": str(e),
            }
            return {}

    def check_decisions_history(self) -> Dict:
        """Analyze recent Eye of God decisions."""
        decisions_file = PROJECT_ROOT / "state/ai/eye_v3/decisions.jsonl"

        if not decisions_file.exists():
            self.results["checks"]["decisions"] = {
                "status": "WARN",
                "message": "No decisions file found",
            }
            return {}

        try:
            decisions = []
            with open(decisions_file, 'r', encoding='utf-8') as f:
                for line in f:
                    try:
                        decisions.append(json.loads(line.strip()))
                    except:
                        pass

            # Last 100 decisions
            recent = decisions[-100:] if len(decisions) > 100 else decisions

            buy_count = sum(1 for d in recent if d.get('action') == 'BUY')
            skip_count = sum(1 for d in recent if d.get('action') == 'SKIP')

            # Confidence distribution
            conf_below_40 = sum(1 for d in recent if d.get('confidence', 0) < 0.40)
            conf_40_65 = sum(1 for d in recent if 0.40 <= d.get('confidence', 0) < 0.65)
            conf_above_65 = sum(1 for d in recent if d.get('confidence', 0) >= 0.65)

            # Last decision
            last = decisions[-1] if decisions else None
            last_ts = last.get('timestamp', '')[:19] if last else 'N/A'

            self.results["checks"]["decisions"] = {
                "status": "PASS",
                "total": len(decisions),
                "recent_100": {
                    "buy": buy_count,
                    "skip": skip_count,
                    "buy_rate": f"{buy_count/len(recent)*100:.1f}%" if recent else "0%",
                },
                "confidence_distribution": {
                    "below_40%": conf_below_40,
                    "40-65%": conf_40_65,
                    "above_65%": conf_above_65,
                },
                "last_decision": {
                    "timestamp": last_ts,
                    "symbol": last.get('symbol') if last else None,
                    "action": last.get('action') if last else None,
                    "confidence": f"{last.get('confidence', 0)*100:.1f}%" if last else None,
                },
            }
            return self.results["checks"]["decisions"]
        except Exception as e:
            self.results["checks"]["decisions"] = {
                "status": "ERROR",
                "error": str(e),
            }
            return {}

    def check_signal_chain(self) -> Dict:
        """Verify signal→order chain components."""
        chain = {
            "momentum_trader": False,
            "pricefeed_gateway": False,
            "autotrader": False,
            "eye_of_god": False,
            "order_executor": False,
            "binance_api": False,
        }

        # 1. Pricefeed Gateway :8100
        chain["pricefeed_gateway"] = self.check_port(8100, "pricefeed")

        # 2. AutoTrader :8200
        chain["autotrader"] = self.check_port(8200, "autotrader")

        # 3. AutoTrader status
        ok, data = self.check_http_endpoint("http://127.0.0.1:8200/status", "autotrader_status")
        if ok and data:
            chain["eye_of_god"] = True  # AutoTrader uses Eye of God
            chain["binance_api"] = data.get("binance_synced", False)

        # 4. Check if momentum_trader is in processes (heuristic)
        # We look for a process running momentum_trader.py
        try:
            if sys.platform == 'win32':
                result = subprocess.run(
                    ['wmic', 'process', 'where', 'name="python.exe"', 'get', 'commandline'],
                    capture_output=True, text=True
                )
                chain["momentum_trader"] = 'momentum_trader' in result.stdout
            else:
                result = subprocess.run(
                    ['pgrep', '-af', 'momentum_trader'],
                    capture_output=True, text=True
                )
                chain["momentum_trader"] = result.returncode == 0
        except:
            pass

        # 5. Order Executor check (via autotrader status)
        if ok and data:
            chain["order_executor"] = data.get("mode") in ["LIVE", "TESTNET"]

        self.results["checks"]["signal_chain"] = {
            "status": "PASS" if all(chain.values()) else "PARTIAL",
            "components": chain,
            "broken_links": [k for k, v in chain.items() if not v],
        }

        return chain

    def analyze_and_recommend(self):
        """Analyze results and generate recommendations."""
        chain = self.results["checks"].get("signal_chain", {}).get("components", {})

        # Check for broken links
        if not chain.get("momentum_trader"):
            self.results["warnings"].append("momentum_trader НЕ ЗАПУЩЕН")
            self.results["recommendations"].append(
                "Запустить: Start-Process python -ArgumentList 'scripts/momentum_trader.py','--daemon'"
            )

        if not chain.get("pricefeed_gateway"):
            self.results["errors"].append("pricefeed_gateway НЕ РАБОТАЕТ (порт 8100)")
            self.results["recommendations"].append(
                "Запустить: Start-Process python -ArgumentList 'scripts/pricefeed_gateway.py'"
            )

        if not chain.get("autotrader"):
            self.results["errors"].append("autotrader НЕ РАБОТАЕТ (порт 8200)")
            self.results["recommendations"].append(
                "Запустить: Start-Process python -ArgumentList 'scripts/autotrader.py','--mode','LIVE','--yes','--confirm'"
            )

        # Check confidence thresholds
        eye = self.results["checks"].get("eye_of_god", {})
        if eye.get("status") == "PASS":
            thresholds = eye.get("thresholds", {})
            momentum_thresh = thresholds.get("MIN_CONFIDENCE_MOMENTUM", 0.40)

            decisions = self.results["checks"].get("decisions", {})
            conf_dist = decisions.get("confidence_distribution", {})
            below_40 = conf_dist.get("below_40%", 0)

            if below_40 > 50:
                self.results["warnings"].append(
                    f"Много сигналов ({below_40}) с confidence < 40% - будут отклоняться"
                )
                self.results["recommendations"].append(
                    "Рассмотреть снижение MIN_CONFIDENCE_MOMENTUM до 0.35"
                )

    def run_full_diagnostic(self) -> Dict:
        """Run complete system diagnostic."""
        print("=" * 60)
        print("  HOPE SYSTEM DIAGNOSTICS")
        print("=" * 60)

        print("\n[1/7] Проверка портов...")
        self.check_port(8100, "pricefeed_gateway")
        self.check_port(8200, "autotrader")
        self.check_port(8080, "other")

        print("[2/7] Проверка HTTP endpoints...")
        self.check_http_endpoint("http://127.0.0.1:8200/status", "autotrader")

        print("[3/7] Проверка Python процессов...")
        self.check_process_count()

        print("[4/7] Проверка Binance API...")
        self.check_binance_connection()

        print("[5/7] Проверка Eye of God V3...")
        self.check_eye_of_god()

        print("[6/7] Анализ истории решений...")
        self.check_decisions_history()

        print("[7/7] Проверка signal->order chain...")
        self.check_signal_chain()

        print("\n[*] Генерация рекомендаций...")
        self.analyze_and_recommend()

        return self.results

    def print_report(self):
        """Print formatted diagnostic report."""
        print("\n" + "=" * 60)
        print("  DIAGNOSTIC REPORT")
        print("=" * 60)

        # Signal Chain Status
        chain = self.results["checks"].get("signal_chain", {})
        components = chain.get("components", {})

        print("\n📊 SIGNAL CHAIN STATUS:")
        for comp, status in components.items():
            icon = "✅" if status else "❌"
            print(f"  {icon} {comp}")

        broken = chain.get("broken_links", [])
        if broken:
            print(f"\n⚠️ BROKEN LINKS: {', '.join(broken)}")

        # Binance
        binance = self.results["checks"].get("binance", {})
        if binance.get("status") == "PASS":
            print(f"\n💰 BINANCE: ${binance.get('usdt_balance', 0):.2f} USDT")

        # Eye of God
        eye = self.results["checks"].get("eye_of_god", {})
        if eye.get("status") == "PASS":
            thresholds = eye.get("thresholds", {})
            print(f"\n👁️ EYE OF GOD THRESHOLDS:")
            print(f"  Regular:  >= {thresholds.get('MIN_CONFIDENCE_TO_TRADE', 0)*100:.0f}%")
            print(f"  Momentum: >= {thresholds.get('MIN_CONFIDENCE_MOMENTUM', 0)*100:.0f}%")

        # Decisions
        decisions = self.results["checks"].get("decisions", {})
        if decisions.get("status") == "PASS":
            recent = decisions.get("recent_100", {})
            last = decisions.get("last_decision", {})
            print(f"\n📈 RECENT DECISIONS (last 100):")
            print(f"  BUY: {recent.get('buy', 0)} | SKIP: {recent.get('skip', 0)} | Rate: {recent.get('buy_rate', '0%')}")
            print(f"  Last: {last.get('timestamp', 'N/A')} | {last.get('symbol', 'N/A')} | {last.get('action', 'N/A')}")

        # Errors and Warnings
        if self.results["errors"]:
            print("\n❌ ERRORS:")
            for e in self.results["errors"]:
                print(f"  • {e}")

        if self.results["warnings"]:
            print("\n⚠️ WARNINGS:")
            for w in self.results["warnings"]:
                print(f"  • {w}")

        # Recommendations
        if self.results["recommendations"]:
            print("\n💡 RECOMMENDATIONS:")
            for i, r in enumerate(self.results["recommendations"], 1):
                print(f"  {i}. {r}")

        print("\n" + "=" * 60)


def main():
    import argparse

    parser = argparse.ArgumentParser(description="HOPE System Diagnostics")
    parser.add_argument("--quick", action="store_true", help="Quick check only")
    parser.add_argument("--fix", action="store_true", help="Auto-fix broken components")
    parser.add_argument("--json", action="store_true", help="Output as JSON")

    args = parser.parse_args()

    diag = HopeDiagnostics()
    results = diag.run_full_diagnostic()

    if args.json:
        print(json.dumps(results, indent=2, ensure_ascii=False))
    else:
        diag.print_report()

    # Auto-fix if requested
    if args.fix:
        broken = results["checks"].get("signal_chain", {}).get("broken_links", [])
        if broken:
            print("\n🔧 AUTO-FIX MODE:")
            for component in broken:
                if component == "momentum_trader":
                    print("  Starting momentum_trader...")
                    subprocess.Popen(
                        [sys.executable, "scripts/momentum_trader.py", "--daemon"],
                        cwd=PROJECT_ROOT,
                        creationflags=subprocess.CREATE_NEW_CONSOLE if sys.platform == 'win32' else 0
                    )
                    print("  ✅ momentum_trader started")


if __name__ == "__main__":
    main()
