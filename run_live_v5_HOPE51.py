#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import json
import logging
import time
import os
from pathlib import Path
from datetime import datetime

try:
    from dotenv import load_dotenv
except ImportError:
    def load_dotenv(*args, **kwargs): pass

# ============================================================================
# LOGGER SETUP
# ============================================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(message)s",
    datefmt="%H:%M:%S"
)
logger = logging.getLogger("run_live_v5")

ROOT_DIR = Path(__file__).resolve().parents[1]
STATE_DIR = ROOT_DIR / "state"
SIGNALS_FILE = STATE_DIR / "signals_v5.jsonl"
HEALTH_FILE = STATE_DIR / "health_v5.json"
STOP_FLAG_FILE = STATE_DIR / "STOP.flag"

# ============================================================================
# RISK MANAGER IMPORT
# ============================================================================

try:
    from minibot.core.risk_manager_v1 import RiskManagerV1
    logger.info("✅ RiskManager imported")
except ImportError:
    logger.error("❌ RiskManager import failed")
    RiskManagerV1 = None


# ============================================================================
# TYPES & ENUMS
# ============================================================================

class EngineMode:
    DRY = "DRY"
    LIVE = "LIVE"


class PositionState:
    OPEN = "OPEN"
    CLOSED = "CLOSED"


# ============================================================================
# POSITION STORAGE
# ============================================================================

class PositionStorageV5:
    def __init__(self):
        self.pos_file = STATE_DIR / "positions_v5.json"
        STATE_DIR.mkdir(parents=True, exist_ok=True)

    def load_positions(self):
        if self.pos_file.exists():
            try:
                return json.loads(self.pos_file.read_text(encoding="utf-8"))
            except:
                pass
        return []

    def save_positions(self, positions):
        try:
            self.pos_file.write_text(json.dumps(positions, indent=2), encoding="utf-8")
        except Exception as e:
            logger.error(f"Save error: {e}")

    def append_trade_record(self, record):
        trade_file = STATE_DIR / "trades_v5.jsonl"
        try:
            with open(trade_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(record) + "\n")
        except Exception as e:
            logger.error(f"Trade record error: {e}")


# ============================================================================
# HEALTH MONITOR
# ============================================================================

class HealthMonitor:
    def __init__(self, filepath):
        self.filepath = Path(filepath)
        self.filepath.parent.mkdir(parents=True, exist_ok=True)

    def update(self, status_dict):
        try:
            self.filepath.write_text(json.dumps(status_dict, indent=2), encoding="utf-8")
        except Exception as e:
            logger.error(f"Health update error: {e}")


# ============================================================================
# MOCK EXCHANGE (for DRY mode)
# ============================================================================

class MockExchange:
    def __init__(self):
        self.balance = 1000.0
        self.positions = {}

    def fetch_balance(self):
        return {"total_usd": self.balance, "free_usd": self.balance}

    def fetch_last_price(self, symbol):
        return 42000.0  # Mock price

    def create_market_order(self, symbol, side, qty):
        price = self.fetch_last_price(symbol)
        if side == "BUY":
            cost = qty * price
            if cost > self.balance:
                raise Exception(f"Insufficient balance")
            self.balance -= cost
            self.positions[symbol] = {"qty": qty, "price": price}
        elif side == "SELL":
            if symbol not in self.positions:
                raise Exception(f"No position for {symbol}")
            del self.positions[symbol]
            self.balance += qty * price
        
        return {"price": price, "qty": qty, "status": "closed"}


# ============================================================================
# HOPE ENGINE V5
# ============================================================================

class HOPEEngineV5:
    def __init__(self, mode, storage, health, risk):
        self.mode = mode
        self.storage = storage
        self.health = health
        self.risk = risk
        self.exchange = MockExchange() if mode == EngineMode.DRY else None
        
        self._positions = self.storage.load_positions()
        self._seen_ids = set()
        self._trading_paused = False
        self.start_time = time.time()

    def update_health(self):
        open_pos = [p for p in self._positions if p.get("state") == PositionState.OPEN]
        
        status = {
            "timestamp": datetime.utcnow().isoformat(),
            "mode": self.mode,
            "open_positions": len(open_pos),
            "total_positions": len(self._positions),
            "daily_pnl": self.risk.daily_pnl,
            "daily_stop_hit": self.risk.is_locked,
            "uptime_sec": int(time.time() - self.start_time),
            "positions": open_pos
        }
        self.health.update(status)
        
        logger.debug(f"💚 Health: {len(open_pos)} open, PnL=${self.risk.daily_pnl:.2f}, Locked={self.risk.is_locked}")

    def process_signals(self):
        if STOP_FLAG_FILE.exists() and not self._trading_paused:
            self._trading_paused = True
            logger.warning("⛔ PAUSED (STOP.flag detected)")
        elif not STOP_FLAG_FILE.exists() and self._trading_paused:
            self._trading_paused = False
            logger.info("✅ RESUMED")

        if not SIGNALS_FILE.exists():
            return

        try:
            lines = SIGNALS_FILE.read_text(encoding="utf-8").splitlines()
            raw_signals = []
            for line in lines:
                if line.strip():
                    try:
                        raw_signals.append(json.loads(line))
                    except:
                        pass
            
            for raw in raw_signals:
                try:
                    sig_id = raw.get("signal_id", f"{raw.get('ts')}_{raw.get('symbol')}")
                    if sig_id in self._seen_ids:
                        continue
                    self._seen_ids.add(sig_id)

                    side = raw.get("side", "").upper()
                    
                    # Block if paused or locked
                    if (self._trading_paused or self.risk.is_locked) and side != "CLOSE":
                        logger.warning(f"🛑 Blocked {side} {raw.get('symbol')} (paused={self._trading_paused}, locked={self.risk.is_locked})")
                        continue

                    if side == "LONG":
                        self._do_long(raw)
                    elif side == "CLOSE":
                        self._do_close(raw)
                    
                except Exception as e:
                    logger.error(f"Signal error: {e}")

        except Exception as e:
            logger.error(f"Process signals error: {e}")

    def _do_long(self, raw):
        symbol = raw.get("symbol")
        
        # ====== RISK MANAGER CHECK ======
        curr_pos_count = len([p for p in self._positions if p.get("state") == PositionState.OPEN])
        equity = self.exchange.fetch_balance()["total_usd"]
        
        allowed, reason = self.risk.can_open_position(curr_pos_count, equity)
        
        if not allowed:
            logger.warning(f"🛡️ RISK BLOCK {symbol}: {reason}")
            return
        
        risk_usd = self.risk.get_risk_per_trade()
        # ================================

        price = float(raw.get("price", 0.0))
        if price <= 0:
            price = self.exchange.fetch_last_price(symbol)

        qty = risk_usd / price
        
        try:
            logger.info(f"🔄 BUY {symbol} (${risk_usd:.2f}) @ {price:.2f}")
            order = self.exchange.create_market_order(symbol, "BUY", qty)
            
            new_pos = {
                "symbol": symbol,
                "side": "LONG",
                "qty": qty,
                "entry_price": price,
                "state": PositionState.OPEN,
                "created_at": time.time()
            }
            self._positions.append(new_pos)
            self.storage.save_positions(self._positions)
            
            logger.info(f"✅ OPENED {symbol} x{qty:.4f}")
            
        except Exception as e:
            logger.error(f"BUY error: {e}")

    def _do_close(self, raw):
        symbol = raw.get("symbol")
        
        target = None
        for p in self._positions:
            if p.get("symbol") == symbol and p.get("state") == PositionState.OPEN:
                target = p
                break
        
        if not target:
            logger.warning(f"No open position for {symbol}")
            return

        exit_price = float(raw.get("price", 0.0))
        if exit_price <= 0:
            exit_price = self.exchange.fetch_last_price(symbol)

        try:
            logger.info(f"🔄 SELL {symbol} x{target['qty']:.4f} @ {exit_price:.2f}")
            order = self.exchange.create_market_order(symbol, "SELL", target["qty"])
            
            pnl = (exit_price - target["entry_price"]) * target["qty"]
            
            # ====== RISK UPDATE ======
            self.risk.update_pnl(pnl)
            # =========================

            record = {
                "ts": time.time(),
                "symbol": symbol,
                "side": "CLOSE",
                "entry": target["entry_price"],
                "exit": exit_price,
                "qty": target["qty"],
                "pnl": pnl
            }
            self.storage.append_trade_record(record)
            
            target["state"] = PositionState.CLOSED
            self._positions = [p for p in self._positions if p.get("state") == PositionState.OPEN]
            self.storage.save_positions(self._positions)
            
            logger.info(f"💰 CLOSED {symbol} | PnL: ${pnl:.2f}")
            
        except Exception as e:
            logger.error(f"SELL error: {e}")


# ============================================================================
# MAIN
# ============================================================================

def main():
    parser = argparse.ArgumentParser(description="HOPE Engine V5.1")
    parser.add_argument("--mode", choices=["DRY", "LIVE"], default="DRY")
    args = parser.parse_args()

    # Load env
    env_path = Path(r"C:\secrets\hope\.env") if Path(r"C:\secrets\hope\.env").exists() else Path(".env")
    if env_path.exists():
        load_dotenv(env_path)

    # Initialize
    risk = RiskManagerV1() if RiskManagerV1 else None
    if not risk:
        logger.error("❌ RiskManager not available. Exiting.")
        return

    storage = PositionStorageV5()
    health = HealthMonitor(str(HEALTH_FILE))
    engine = HOPEEngineV5(args.mode, storage, health, risk)

    logger.info(f"{'='*60}")
    logger.info(f"🚀 HOPE ENGINE V5.1 ({args.mode})")
    logger.info(f"{'='*60}")

    try:
        while True:
            engine.update_health()
            engine.process_signals()
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("⏹️ STOPPED")


if __name__ == "__main__":
    main()
