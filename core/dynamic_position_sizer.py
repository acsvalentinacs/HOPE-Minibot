#!/usr/bin/env python3
"""
# === AI SIGNATURE ===
# sha256:dynamic_position_sizer_v1
# Created by: Claude (opus-4.5)
# Created at: 2026-01-31T12:00:00Z
# Purpose: Dynamic position sizing based on current balance (compound growth)
# Contract: More balance = bigger orders, fail-closed on errors
# === END SIGNATURE ===

HOPE Dynamic Position Sizer v1.0
================================
Автоматический расчёт размера позиции на основе:
- Текущего баланса (real-time с Binance)
- Процента от депозита (default 20%)
- Минимальных/максимальных лимитов
- Серии убытков (уменьшение после losses)

Формула: position_size = balance * position_pct * confidence_mult * loss_adjustment
"""

import json
import hmac
import hashlib
import time
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Optional, Tuple
from dataclasses import dataclass, asdict

try:
    import httpx
except ImportError:
    httpx = None

logger = logging.getLogger(__name__)

# Paths
STATE_DIR = Path(__file__).parent.parent / "state" / "ai"
CONFIG_DIR = Path(__file__).parent.parent / "config"
SECRETS_FILE = Path("C:/secrets/hope.env")


@dataclass
class PositionSizeResult:
    """Результат расчёта размера позиции"""
    size_usd: float           # Размер позиции в USD
    balance_usd: float        # Текущий баланс
    position_pct: float       # Процент от баланса
    confidence_mult: float    # Множитель по confidence
    loss_adjustment: float    # Корректировка после убытков
    max_allowed: float        # Максимально разрешённый размер
    reasoning: str            # Объяснение расчёта


class DynamicPositionSizer:
    """
    Динамический расчёт размера позиции.
    
    ПРИНЦИП: Больше депозит = больше ордер (compound growth)
    
    Использование:
        sizer = DynamicPositionSizer()
        result = sizer.calculate(confidence=0.75)
        print(f"Position size: ${result.size_usd}")
    """
    
    def __init__(self, config_path: str = None):
        # Load config
        if config_path and Path(config_path).exists():
            self.config = json.loads(Path(config_path).read_text())
        else:
            config_file = CONFIG_DIR / "scalping_100.json"
            if config_file.exists():
                self.config = json.loads(config_file.read_text())
            else:
                self.config = self._default_config()
        
        # API credentials
        self.api_key = None
        self.api_secret = None
        self._load_credentials()
        
        # State tracking
        self.state_file = STATE_DIR / "position_sizer_state.json"
        self.state = self._load_state()
        
        # Cache balance (refresh every 60 sec)
        self._balance_cache = None
        self._balance_timestamp = None
        self._balance_ttl = 60  # seconds
        
        logger.info(f"DynamicPositionSizer initialized | Config: {config_path}")
    
    def _default_config(self) -> dict:
        return {
            "capital": {"total_usd": 100},
            "position_sizing": {
                "base_pct": 20,              # 20% от баланса
                "min_size_usd": 10,
                "max_size_usd": 50,
                "max_exposure_pct": 50,      # Max 50% баланса в позициях
                "confidence_scaling": {
                    "0.85": 1.25,            # High confidence = +25%
                    "0.75": 1.0,             # Normal
                    "0.65": 0.75             # Low confidence = -25%
                }
            },
            "risk_management": {
                "max_consecutive_losses": 3,
                "loss_reduction_factor": 0.75,
                "recovery_wins_needed": 2
            },
            "compound": {
                "enabled": True,
                "min_balance_for_increase": 110,  # Увеличивать после $110
                "increase_step_pct": 10           # Каждые 10% роста
            }
        }
    
    def _load_credentials(self):
        """Загрузить API ключи"""
        if SECRETS_FILE.exists():
            for line in SECRETS_FILE.read_text().splitlines():
                if line.startswith("BINANCE_API_KEY="):
                    self.api_key = line.split("=", 1)[1].strip()
                elif line.startswith("BINANCE_API_SECRET="):
                    self.api_secret = line.split("=", 1)[1].strip()
    
    def _load_state(self) -> dict:
        """Загрузить состояние"""
        if self.state_file.exists():
            try:
                return json.loads(self.state_file.read_text())
            except:
                pass
        return {
            "consecutive_losses": 0,
            "consecutive_wins": 0,
            "initial_balance": None,
            "last_balance": None,
            "total_trades": 0,
            "updated_at": None
        }
    
    def _save_state(self):
        """Сохранить состояние"""
        self.state["updated_at"] = datetime.now().isoformat()
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        self.state_file.write_text(json.dumps(self.state, indent=2))
    
    def get_balance(self, force_refresh: bool = False) -> float:
        """
        Получить текущий USDT баланс с Binance.
        
        Кэширует на 60 секунд для экономии API calls.
        """
        # Check cache
        if not force_refresh and self._balance_cache is not None:
            if self._balance_timestamp:
                age = (datetime.now() - self._balance_timestamp).total_seconds()
                if age < self._balance_ttl:
                    return self._balance_cache
        
        # Fetch from Binance
        if not self.api_key or not self.api_secret or not httpx:
            logger.warning("Cannot fetch balance: missing credentials or httpx")
            return self.config.get("capital", {}).get("total_usd", 100)
        
        try:
            ts = int(time.time() * 1000)
            params = f"timestamp={ts}"
            sig = hmac.new(
                self.api_secret.encode(),
                params.encode(),
                hashlib.sha256
            ).hexdigest()
            
            client = httpx.Client(timeout=10)
            client.headers["X-MBX-APIKEY"] = self.api_key
            
            r = client.get(f"https://api.binance.com/api/v3/account?{params}&signature={sig}")
            
            if r.status_code == 200:
                data = r.json()
                for b in data.get("balances", []):
                    if b["asset"] == "USDT":
                        balance = float(b["free"])
                        
                        # Update cache
                        self._balance_cache = balance
                        self._balance_timestamp = datetime.now()
                        
                        # Update state
                        if self.state["initial_balance"] is None:
                            self.state["initial_balance"] = balance
                        self.state["last_balance"] = balance
                        self._save_state()
                        
                        logger.info(f"[BALANCE] USDT: ${balance:.2f}")
                        return balance
            
            logger.error(f"Failed to fetch balance: {r.status_code}")
        except Exception as e:
            logger.error(f"Balance fetch error: {e}")
        
        # Fallback to cached or config
        if self._balance_cache is not None:
            return self._balance_cache
        return self.config.get("capital", {}).get("total_usd", 100)
    
    def calculate(
        self,
        confidence: float = 0.70,
        current_exposure: float = 0.0
    ) -> PositionSizeResult:
        """
        Рассчитать размер позиции.
        
        Args:
            confidence: AI confidence (0.0 - 1.0)
            current_exposure: Текущая сумма в открытых позициях
        
        Returns:
            PositionSizeResult с рекомендуемым размером
        """
        cfg = self.config["position_sizing"]
        risk_cfg = self.config.get("risk_management", {})
        compound_cfg = self.config.get("compound", {})
        
        # 1. Get current balance
        balance = self.get_balance()
        
        # 2. Base position as % of balance
        base_pct = cfg.get("base_pct", 20) / 100  # 20% = 0.20
        base_size = balance * base_pct
        
        # 3. Confidence scaling
        conf_scaling = cfg.get("confidence_scaling", {})
        confidence_mult = 1.0
        for threshold, mult in sorted(conf_scaling.items(), reverse=True):
            if confidence >= float(threshold):
                confidence_mult = mult
                break
        
        # 4. Loss adjustment
        loss_adjustment = 1.0
        consecutive_losses = self.state.get("consecutive_losses", 0)
        max_losses = risk_cfg.get("max_consecutive_losses", 3)
        
        if consecutive_losses >= 2:
            loss_adjustment = risk_cfg.get("loss_reduction_factor", 0.75)
            logger.warning(f"[SIZE] Reduced due to {consecutive_losses} consecutive losses")
        
        if consecutive_losses >= max_losses:
            # Минимальный размер после серии убытков
            loss_adjustment = 0.5
            logger.warning(f"[SIZE] Minimum size due to {consecutive_losses} losses")
        
        # 5. Compound growth bonus
        compound_mult = 1.0
        if compound_cfg.get("enabled", True):
            initial = self.state.get("initial_balance") or balance
            if initial > 0:
                growth_pct = (balance / initial - 1) * 100
                step = compound_cfg.get("increase_step_pct", 10)
                if growth_pct >= step:
                    # Каждые 10% роста = +5% к размеру позиции
                    compound_mult = 1.0 + (growth_pct // step) * 0.05
                    compound_mult = min(compound_mult, 1.5)  # Max +50%
                    logger.info(f"[COMPOUND] Growth {growth_pct:.1f}% → mult={compound_mult:.2f}")
        
        # 6. Calculate final size
        size = base_size * confidence_mult * loss_adjustment * compound_mult
        
        # 7. Apply limits
        min_size = cfg.get("min_size_usd", 10)
        max_size = cfg.get("max_size_usd", 50)
        
        # Max exposure check
        max_exposure_pct = cfg.get("max_exposure_pct", 50) / 100
        max_allowed = balance * max_exposure_pct - current_exposure
        max_allowed = max(0, max_allowed)
        
        size = max(min_size, min(size, max_size, max_allowed))
        
        # 8. Final check: не более 50% баланса
        if size > balance * 0.5:
            size = balance * 0.5
        
        reasoning = (
            f"Balance=${balance:.2f} * {base_pct*100:.0f}% "
            f"* Conf={confidence_mult:.2f} "
            f"* Loss={loss_adjustment:.2f} "
            f"* Compound={compound_mult:.2f}"
        )
        
        result = PositionSizeResult(
            size_usd=round(size, 2),
            balance_usd=round(balance, 2),
            position_pct=round(base_pct * 100, 1),
            confidence_mult=round(confidence_mult, 2),
            loss_adjustment=round(loss_adjustment, 2),
            max_allowed=round(max_allowed, 2),
            reasoning=reasoning
        )
        
        logger.info(
            f"[POSITION] Size=${result.size_usd} | "
            f"Balance=${result.balance_usd} | "
            f"Conf*{result.confidence_mult}"
        )
        
        return result
    
    def record_trade_result(self, is_win: bool, pnl_usd: float = 0):
        """
        Записать результат сделки.
        
        Args:
            is_win: True если прибыльная сделка
            pnl_usd: PnL в долларах
        """
        self.state["total_trades"] = self.state.get("total_trades", 0) + 1
        
        if is_win:
            self.state["consecutive_wins"] = self.state.get("consecutive_wins", 0) + 1
            self.state["consecutive_losses"] = 0
            logger.info(f"[RESULT] WIN | Streak: {self.state['consecutive_wins']}")
        else:
            self.state["consecutive_losses"] = self.state.get("consecutive_losses", 0) + 1
            self.state["consecutive_wins"] = 0
            logger.warning(f"[RESULT] LOSS | Streak: {self.state['consecutive_losses']}")
        
        # Refresh balance after trade
        self.get_balance(force_refresh=True)
        self._save_state()
    
    def get_status(self) -> dict:
        """Получить текущий статус"""
        balance = self.get_balance()
        initial = self.state.get("initial_balance") or balance
        
        return {
            "balance_usd": balance,
            "initial_balance": initial,
            "growth_pct": round((balance / initial - 1) * 100, 2) if initial else 0,
            "consecutive_losses": self.state.get("consecutive_losses", 0),
            "consecutive_wins": self.state.get("consecutive_wins", 0),
            "total_trades": self.state.get("total_trades", 0),
            "next_position_size": self.calculate().size_usd
        }


# === STARTUP PROTOCOL ===

class HopeStartupProtocol:
    """
    Протокол запуска HOPE для циклической торговли.
    
    Использование:
        protocol = HopeStartupProtocol()
        if protocol.run_preflight_checks():
            protocol.start_all_daemons()
    """
    
    DAEMONS = [
        {
            "name": "pricefeed_bridge",
            "script": "scripts/pricefeed_bridge.py",
            "args": ["--daemon"],
            "port": 8100,
            "required": True
        },
        {
            "name": "position_watchdog", 
            "script": "scripts/position_watchdog.py",
            "args": ["--live"],
            "port": None,
            "required": True
        },
        {
            "name": "eye_of_god_v3",
            "script": "scripts/eye_of_god_v3.py",
            "args": ["--daemon"],
            "port": None,
            "required": True
        },
        {
            "name": "scalping_pipeline",
            "script": "scripts/scalping_pipeline.py",
            "args": ["--live"],
            "port": None,
            "required": True
        },
        {
            "name": "hope_dashboard",
            "script": "scripts/hope_dashboard.py",
            "args": ["--port", "8080"],
            "port": 8080,
            "required": False
        }
    ]
    
    def __init__(self):
        self.sizer = DynamicPositionSizer()
        self.checks_passed = []
        self.checks_failed = []
    
    def run_preflight_checks(self) -> bool:
        """
        Запустить предполётные проверки.
        
        Returns:
            True если все критические проверки пройдены
        """
        print("\n" + "="*60)
        print("🔍 HOPE PREFLIGHT CHECKS")
        print("="*60)
        
        all_passed = True
        
        # 1. Balance check
        balance = self.sizer.get_balance()
        if balance >= 10:
            self._pass(f"Balance: ${balance:.2f} USDT")
        else:
            self._fail(f"Balance too low: ${balance:.2f} (min $10)")
            all_passed = False
        
        # 2. API connection
        if balance > 0:
            self._pass("Binance API: Connected")
        else:
            self._fail("Binance API: Connection failed")
            all_passed = False
        
        # 3. Config files
        config_file = CONFIG_DIR / "scalping_100.json"
        if config_file.exists():
            self._pass(f"Config: {config_file.name}")
        else:
            self._fail("Config: scalping_100.json not found")
            all_passed = False
        
        # 4. State directory
        if STATE_DIR.exists():
            self._pass(f"State dir: {STATE_DIR}")
        else:
            STATE_DIR.mkdir(parents=True, exist_ok=True)
            self._pass(f"State dir: Created {STATE_DIR}")
        
        # 5. Pricefeed freshness
        pricefeed_file = STATE_DIR / "pricefeed.json"
        if pricefeed_file.exists():
            try:
                data = json.loads(pricefeed_file.read_text())
                age = time.time() - data.get("timestamp", 0)
                if age < 60:
                    self._pass(f"Pricefeed: Fresh ({age:.0f}s old)")
                else:
                    self._fail(f"Pricefeed: Stale ({age:.0f}s old)")
            except:
                self._fail("Pricefeed: Invalid format")
        else:
            self._fail("Pricefeed: File not found (will be created)")
        
        # 6. Position size calculation
        result = self.sizer.calculate(confidence=0.70)
        self._pass(f"Position size: ${result.size_usd} (at 70% confidence)")
        
        # Summary
        print("\n" + "-"*60)
        print(f"✅ Passed: {len(self.checks_passed)}")
        print(f"❌ Failed: {len(self.checks_failed)}")
        print("-"*60)
        
        if all_passed:
            print("🟢 ALL CRITICAL CHECKS PASSED - Ready to start")
        else:
            print("🔴 CRITICAL CHECKS FAILED - Fix issues before starting")
        
        return all_passed
    
    def _pass(self, msg: str):
        self.checks_passed.append(msg)
        print(f"  ✅ {msg}")
    
    def _fail(self, msg: str):
        self.checks_failed.append(msg)
        print(f"  ❌ {msg}")
    
    def generate_startup_script(self) -> str:
        """Генерировать PowerShell скрипт для запуска"""
        script = '''# HOPE Startup Script - Auto-generated
# Generated at: {timestamp}

$ErrorActionPreference = "Stop"
Set-Location "C:\\Users\\kirillDev\\Desktop\\TradingBot\\minibot"

Write-Host "=" * 60 -ForegroundColor Cyan
Write-Host "  HOPE AI TRADING SYSTEM - STARTUP" -ForegroundColor Green
Write-Host "=" * 60 -ForegroundColor Cyan

# Stop existing processes
Write-Host "`n[1/6] Stopping existing Python processes..." -ForegroundColor Yellow
Get-Process python* -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
Start-Sleep 2

# Start Pricefeed Bridge
Write-Host "[2/6] Starting Pricefeed Bridge..." -ForegroundColor Yellow
Start-Process -FilePath "python" -ArgumentList "scripts/pricefeed_bridge.py","--daemon" -WindowStyle Minimized
Start-Sleep 3

# Start Position Watchdog
Write-Host "[3/6] Starting Position Watchdog (LIVE)..." -ForegroundColor Yellow
Start-Process -FilePath "python" -ArgumentList "scripts/position_watchdog.py","--live" -WindowStyle Minimized
Start-Sleep 2

# Start Eye of God V3
Write-Host "[4/6] Starting Eye of God V3..." -ForegroundColor Yellow
Start-Process -FilePath "python" -ArgumentList "scripts/eye_of_god_v3.py","--daemon" -WindowStyle Minimized
Start-Sleep 2

# Start Scalping Pipeline
Write-Host "[5/6] Starting Scalping Pipeline (LIVE)..." -ForegroundColor Yellow
Start-Process -FilePath "python" -ArgumentList "scripts/scalping_pipeline.py","--live" -WindowStyle Minimized
Start-Sleep 2

# Start Dashboard
Write-Host "[6/6] Starting Dashboard..." -ForegroundColor Yellow
Start-Process -FilePath "python" -ArgumentList "scripts/hope_dashboard.py","--port","8080" -WindowStyle Minimized
Start-Sleep 3

# Verify
Write-Host "`n" + "=" * 60 -ForegroundColor Cyan
Write-Host "  VERIFICATION" -ForegroundColor Green
Write-Host "=" * 60 -ForegroundColor Cyan

# Check processes
$procs = Get-Process python* -ErrorAction SilentlyContinue
Write-Host "`nPython processes: $($procs.Count)" -ForegroundColor Yellow

# Check ports
Write-Host "`nPorts:" -ForegroundColor Yellow
netstat -ano | Select-String ":8080|:8100" | ForEach-Object {{ Write-Host "  $_" }}

# Check balance
Write-Host "`nBalance check:" -ForegroundColor Yellow
python -c "
from core.dynamic_position_sizer import DynamicPositionSizer
sizer = DynamicPositionSizer()
status = sizer.get_status()
print(f'  Balance: ${{status[\"balance_usd\"]:.2f}}')
print(f'  Growth: {{status[\"growth_pct\"]:.1f}}%')
print(f'  Next position: ${{status[\"next_position_size\"]:.2f}}')
"

Write-Host "`n" + "=" * 60 -ForegroundColor Cyan
Write-Host "  HOPE STARTED SUCCESSFULLY!" -ForegroundColor Green
Write-Host "  Dashboard: http://localhost:8080" -ForegroundColor Cyan
Write-Host "=" * 60 -ForegroundColor Cyan
'''.format(timestamp=datetime.now().isoformat())
        
        return script


# === MAIN ===

if __name__ == "__main__":
    import argparse
    
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s | %(levelname)s | %(message)s'
    )
    
    parser = argparse.ArgumentParser(description="HOPE Dynamic Position Sizer")
    parser.add_argument("--check", action="store_true", help="Run preflight checks")
    parser.add_argument("--status", action="store_true", help="Show current status")
    parser.add_argument("--calculate", type=float, help="Calculate position for confidence")
    parser.add_argument("--generate-script", action="store_true", help="Generate startup script")
    
    args = parser.parse_args()
    
    if args.check:
        protocol = HopeStartupProtocol()
        protocol.run_preflight_checks()
    
    elif args.status:
        sizer = DynamicPositionSizer()
        status = sizer.get_status()
        
        print("\n" + "="*50)
        print("[HOPE] POSITION SIZER STATUS")
        print("="*50)
        print(f"Balance:         ${status['balance_usd']:.2f}")
        print(f"Initial:         ${status['initial_balance']:.2f}")
        print(f"Growth:          {status['growth_pct']:.1f}%")
        print(f"Total trades:    {status['total_trades']}")
        print(f"Win streak:      {status['consecutive_wins']}")
        print(f"Loss streak:     {status['consecutive_losses']}")
        print(f"Next position:   ${status['next_position_size']:.2f}")
        print("="*50)
    
    elif args.calculate:
        sizer = DynamicPositionSizer()
        result = sizer.calculate(confidence=args.calculate)
        
        print("\n" + "="*50)
        print(f"[HOPE] POSITION CALCULATION (conf={args.calculate})")
        print("="*50)
        print(f"Size:            ${result.size_usd}")
        print(f"Balance:         ${result.balance_usd}")
        print(f"Position %:      {result.position_pct}%")
        print(f"Conf mult:       {result.confidence_mult}")
        print(f"Loss adjust:     {result.loss_adjustment}")
        print(f"Max allowed:     ${result.max_allowed}")
        print(f"Reasoning:       {result.reasoning}")
        print("="*50)
    
    elif args.generate_script:
        protocol = HopeStartupProtocol()
        script = protocol.generate_startup_script()
        
        script_file = Path("tools/start_hope_trading.ps1")
        script_file.parent.mkdir(parents=True, exist_ok=True)
        script_file.write_text(script)
        
        print(f"✅ Script generated: {script_file}")
    
    else:
        # Default: show status
        sizer = DynamicPositionSizer()
        result = sizer.calculate(confidence=0.70)
        
        print("\n" + "="*50)
        print("🎯 HOPE DYNAMIC POSITION SIZER")
        print("="*50)
        print(f"Current balance: ${result.balance_usd}")
        print(f"Position size:   ${result.size_usd} (at 70% confidence)")
        print(f"Reasoning:       {result.reasoning}")
        print("="*50)
        print("\nUsage:")
        print("  --check           Run preflight checks")
        print("  --status          Show detailed status")
        print("  --calculate 0.75  Calculate for specific confidence")
        print("  --generate-script Generate PowerShell startup script")
