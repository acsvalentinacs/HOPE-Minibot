# minibot/risk.py — HOPE v2.2 (compat) — ВСЕ properties + aliases
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import logging
from typing import Optional, Any, Dict

__all__ = ["RiskConfig", "RiskManager", "SlippageGuard", "SlippageError"]

class SlippageError(RuntimeError):
    pass

@dataclass
class RiskConfig:
    daily_stop_usd: float = 50.0
    max_concurrent: int = 3
    tp_atr_x: float = 3.0
    sl_atr_x: float = 1.5
    slip_max_bps: float = 50.0

class SlippageGuard:
    def __init__(self, max_bps: float = 50.0):
        self.max_bps = float(max_bps)
    def _bps(self, ref_price: float, fill_price: float) -> float:
        try:
            ref = float(ref_price); got = float(fill_price)
        except Exception:
            return 1e9
        if ref <= 0: return 1e9
        return abs((got / ref - 1.0) * 10_000.0)
    def allow(self, ref_price: float, fill_price: float) -> bool:
        return self._bps(ref_price, fill_price) <= self.max_bps
    def enforce(self, ref_price: float, fill_price: float) -> None:
        bps = self._bps(ref_price, fill_price)
        if bps > self.max_bps:
            raise SlippageError(f"slippage {bps:.2f} bps > {self.max_bps:.2f} bps (ref={float(ref_price):.8f}, got={float(fill_price):.8f})")

class RiskManager:
    def __init__(self, cfg=None, *, logger=None, dry_run=True, project_root=None, daily_stop_usd=None, max_concurrent=None, tp_atr_x=None, sl_atr_x=None, slip_max_bps=None, slip_guard=None, tp_atr_mult=None, sl_atr_mult=None, **kwargs):
        self.cfg = cfg or RiskConfig()
        if daily_stop_usd is not None: self.cfg.daily_stop_usd = float(daily_stop_usd)
        if max_concurrent is not None: self.cfg.max_concurrent = int(max_concurrent)
        if tp_atr_x is not None: self.cfg.tp_atr_x = float(tp_atr_x)
        if sl_atr_x is not None: self.cfg.sl_atr_x = float(sl_atr_x)
        if slip_max_bps is not None: self.cfg.slip_max_bps = float(slip_max_bps)
        if tp_atr_mult is not None: self.cfg.tp_atr_x = float(tp_atr_mult)
        if sl_atr_mult is not None: self.cfg.sl_atr_x = float(sl_atr_mult)
        self._logger = logger or logging.getLogger("minibot.risk")
        self._dry_run = bool(dry_run)
        self._root = Path(project_root) if project_root else Path.cwd()
        self._slip = slip_guard or SlippageGuard(self.cfg.slip_max_bps)
        self._open_positions = 0
        self._daily_loss_usd = 0.0
        self._daily_reset_date = datetime.utcnow().date()
        self._stop_flag = False
        self._cooldown_until_ts = 0.0
        self._logger.info("RiskManager init: dry_run=%s, max_concurrent=%d, daily_stop_usd=%.2f, TPxATR=%.2f, SLxATR=%.2f, slip_max_bps=%.2f", self._dry_run, self.cfg.max_concurrent, self.cfg.daily_stop_usd, self.cfg.tp_atr_x, self.cfg.sl_atr_x, self.cfg.slip_max_bps)
    @property
    def dry_run(self): return self._dry_run
    @property
    def project_root(self): return self._root
    @property
    def max_concurrent(self): return self.cfg.max_concurrent
    @property
    def daily_stop_usd(self): return self.cfg.daily_stop_usd
    @property
    def tp_atr_x(self): return self.cfg.tp_atr_x
    @property
    def sl_atr_x(self): return self.cfg.sl_atr_x
    @property
    def tp_atr_mult(self): return self.cfg.tp_atr_x
    @property
    def sl_atr_mult(self): return self.cfg.sl_atr_x
    @property
    def slip_max_bps(self): return self.cfg.slip_max_bps
    @property
    def open_positions(self): return self._open_positions
    @property
    def daily_loss(self): return self._daily_loss_usd
    def daily_stop_hit(self): return self._daily_loss_usd >= self.cfg.daily_stop_usd
    def can_open(self):
        if self._stop_flag or self._open_positions >= self.cfg.max_concurrent or self.daily_stop_hit(): return False
        import time; return time.time() >= self._cooldown_until_ts
    def on_open(self): self._open_positions += 1
    def on_close(self, pnl_usd=None):
        self._open_positions = max(0, self._open_positions - 1)
        if pnl_usd is not None and pnl_usd < 0: self.add_loss(abs(pnl_usd))
    def add_loss(self, usd):
        try: v = float(usd)
        except: v = 0.0
        if v > 0: self._daily_loss_usd += v
    def reset_daily(self):
        self._daily_loss_usd = 0.0
        self._daily_reset_date = datetime.utcnow().date()
        self._logger.info("Daily loss counter reset to 0.0")
    def set_stop(self, enabled):
        self._stop_flag = bool(enabled)
        self._logger.info("STOP trading: %s", "ENABLED" if enabled else "DISABLED")
    def is_stop(self): return self._stop_flag
    def set_cooldown(self, seconds):
        import time; self._cooldown_until_ts = time.time() + float(seconds)
        self._logger.info("Cooldown set: %.1fs", float(seconds))
    def is_cooldown(self):
        import time; return time.time() < self._cooldown_until_ts
    def get_status(self):
        return {"dry_run": self._dry_run, "open_positions": self._open_positions, "max_concurrent": self.cfg.max_concurrent, "daily_loss_usd": self._daily_loss_usd, "daily_stop_usd": self.cfg.daily_stop_usd, "daily_stop_hit": self.daily_stop_hit(), "stop_trading": self._stop_flag, "cooldown_active": self.is_cooldown(), "tp_atr_x": self.cfg.tp_atr_x, "sl_atr_x": self.cfg.sl_atr_x, "slip_max_bps": self.cfg.slip_max_bps}
    @property
    def slippage_guard(self): return self._slip
