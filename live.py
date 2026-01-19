from __future__ import annotations
import os
import time
import math
import json
import sqlite3
from typing import Dict, Any, Optional, Tuple
from pathlib import Path

from dotenv import load_dotenv, find_dotenv
dotenv_path = find_dotenv(usecwd=True)
if dotenv_path:
    load_dotenv(dotenv_path=dotenv_path)

import ccxt
import pandas as pd

from .monitor import Monitor
from .core import RunParams
from . import config
from .brain import init_brain
from .barfeed import BarFeeder
from . import metrics as m

# NTP (опционально)
try:
    import ntplib  # type: ignore
except Exception:
    ntplib = None

def _ensure_dir(p: Path):
    p.parent.mkdir(parents=True, exist_ok=True)

class OrderRunner:
    """
    Полный класс: SQLite-лёджер, PnL, streak-и, CSV-лог, идемпотентные ордера.
    """
    def __init__(self, exchange: ccxt.Exchange, db_path: str = "runs/live/state.db", fallback_fee_bps: float = 6.0):
        self.exchange = exchange
        self.db_path = Path(db_path)
        self.fallback_fee_bps = float(fallback_fee_bps)
        _ensure_dir(self.db_path)
        self._init_db()

    @staticmethod
    def _today() -> str:
        return time.strftime("%Y-%m-%d", time.gmtime())

    def _init_db(self):
        con = sqlite3.connect(self.db_path)
        cur = con.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS orders(
                client_id TEXT PRIMARY KEY,
                ts INTEGER,
                symbol TEXT,
                side TEXT,
                type TEXT,
                amount REAL,
                price REAL,
                fee REAL,
                status TEXT,
                raw TEXT
            )
        """)
        cur.execute("PRAGMA table_info(orders)")
        cols = [r[1] for r in cur.fetchall()]
        if "fee" not in cols:
            try:
                cur.execute("ALTER TABLE orders ADD COLUMN fee REAL")
            except Exception:
                pass

        cur.execute("""
            CREATE TABLE IF NOT EXISTS position(
                symbol TEXT PRIMARY KEY,
                side TEXT,
                qty REAL,
                avg_price REAL,
                updated_ts INTEGER
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS pnl(
                day TEXT PRIMARY KEY,
                realized REAL
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS counters(
                day TEXT PRIMARY KEY,
                trades INTEGER,
                consecutive_losses INTEGER
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS runtime(
                key TEXT PRIMARY KEY,
                value TEXT
            )
        """)
        con.commit()
        con.close()

    # ---------- runtime KV ----------
    def get_runtime(self, key: str, default: Optional[str] = None) -> Optional[str]:
        con = sqlite3.connect(self.db_path)
        cur = con.cursor()
        cur.execute("SELECT value FROM runtime WHERE key=?", (key,))
        row = cur.fetchone()
        con.close()
        if not row:
            return default
        return str(row[0])

    def set_runtime(self, key: str, value: Any):
        con = sqlite3.connect(self.db_path)
        cur = con.cursor()
        cur.execute(
            "INSERT INTO runtime(key,value) VALUES(?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, str(value)),
        )
        con.commit()
        con.close()

    # ---------- counters ----------
    def _ensure_counters_today(self, con, cur):
        day = self._today()
        cur.execute("INSERT OR IGNORE INTO counters(day, trades, consecutive_losses) VALUES(?,0,0)", (day,))

    def inc_trade_count(self):
        con = sqlite3.connect(self.db_path)
        cur = con.cursor()
        self._ensure_counters_today(con, cur)
        cur.execute("UPDATE counters SET trades = trades + 1 WHERE day=?", (self._today(),))
        con.commit()
        con.close()

    def get_trades_today(self) -> int:
        con = sqlite3.connect(self.db_path)
        cur = con.cursor()
        self._ensure_counters_today(con, cur)
        cur.execute("SELECT trades FROM counters WHERE day=?", (self._today(),))
        row = cur.fetchone()
        con.close()
        return int(row[0]) if row else 0

    def inc_loss_streak(self):
        con = sqlite3.connect(self.db_path)
        cur = con.cursor()
        self._ensure_counters_today(con, cur)
        cur.execute("UPDATE counters SET consecutive_losses = consecutive_losses + 1 WHERE day=?", (self._today(),))
        con.commit()
        con.close()

    def reset_loss_streak(self):
        con = sqlite3.connect(self.db_path)
        cur = con.cursor()
        self._ensure_counters_today(con, cur)
        cur.execute("UPDATE counters SET consecutive_losses = 0 WHERE day=?", (self._today(),))
        con.commit()
        con.close()

    def get_loss_streak(self) -> int:
        con = sqlite3.connect(self.db_path)
        cur = con.cursor()
        self._ensure_counters_today(con, cur)
        cur.execute("SELECT consecutive_losses FROM counters WHERE day=?", (self._today(),))
        row = cur.fetchone()
        con.close()
        return int(row[0]) if row else 0

    # ---------- позиция / PnL ----------
    def _apply_fill(self, symbol: str, side: str, amount: float, price: float, fee_cost: float) -> Tuple[float, bool, Optional[bool]]:
        """
        Возвращает: realized, closed_prev, closed_loss (None если позиция не закрывалась)
        """
        day = time.strftime("%Y-%m-%d", time.gmtime())
        con = sqlite3.connect(self.db_path)
        cur = con.cursor()
        cur.execute("SELECT side, qty, avg_price FROM position WHERE symbol=?", (symbol,))
        row = cur.fetchone()
        prev_side = row[0] if row else "FLAT"
        qty       = float(row[1]) if row else 0.0
        avg       = float(row[2]) if row else 0.0

        realized = 0.0
        s = side.lower()
        pos_side = prev_side
        closed_prev = False
        closed_loss: Optional[bool] = None

        if s == "buy":
            if prev_side == "FLAT":
                pos_side, qty, avg = "LONG", amount, price
            elif prev_side == "LONG":
                new_qty = qty + amount
                avg = ((qty * avg) + (amount * price)) / new_qty if new_qty > 0 else 0.0
                qty = new_qty
                pos_side = "LONG"
            elif prev_side == "SHORT":
                close_qty = min(qty, amount)
                realized += (avg - price) * close_qty
                qty -= close_qty
                if amount > close_qty:
                    closed_prev = True
                    closed_loss = (realized < 0)
                    open_qty = amount - close_qty
                    pos_side, qty, avg = "LONG", open_qty, price
                else:
                    if qty <= 1e-9:
                        closed_prev = True
                        closed_loss = (realized < 0)
                        pos_side, qty, avg = "FLAT", 0.0, 0.0
                    else:
                        pos_side = "SHORT"

        elif s == "sell":
            if prev_side == "FLAT":
                pos_side, qty, avg = "SHORT", amount, price
            elif prev_side == "SHORT":
                new_qty = qty + amount
                avg = ((qty * avg) + (amount * price)) / new_qty if new_qty > 0 else 0.0
                qty = new_qty
                pos_side = "SHORT"
            elif prev_side == "LONG":
                close_qty = min(qty, amount)
                realized += (price - avg) * close_qty
                qty -= close_qty
                if amount > close_qty:
                    closed_prev = True
                    closed_loss = (realized < 0)
                    open_qty = amount - close_qty
                    pos_side, qty, avg = "SHORT", open_qty, price
                else:
                    if qty <= 1e-9:
                        closed_prev = True
                        closed_loss = (realized < 0)
                        pos_side, qty, avg = "FLAT", 0.0, 0.0
                    else:
                        pos_side = "LONG"

        realized -= abs(float(fee_cost or 0.0))

        cur.execute(
            "INSERT INTO pnl(day, realized) VALUES(?, COALESCE(?,0)) "
            "ON CONFLICT(day) DO UPDATE SET realized = realized + EXCLUDED.realized",
            (day, realized)
        )
        cur.execute(
            "INSERT OR REPLACE INTO position(symbol, side, qty, avg_price, updated_ts) VALUES(?,?,?,?,?)",
            (symbol, pos_side, qty, avg, int(time.time()))
        )
        con.commit()
        con.close()
        return realized, closed_prev, closed_loss

    def daily_realized_pnl(self) -> float:
        day = self._today()
        con = sqlite3.connect(self.db_path)
        cur = con.cursor()
        cur.execute("SELECT realized FROM pnl WHERE day=?", (day,))
        row = cur.fetchone()
        con.close()
        return float(row[0]) if row else 0.0

    def position(self, symbol: str) -> Tuple[str, float, float]:
        con = sqlite3.connect(self.db_path)
        cur = con.cursor()
        cur.execute("SELECT side, qty, avg_price FROM position WHERE symbol=?", (symbol,))
        row = cur.fetchone()
        con.close()
        if not row:
            return "FLAT", 0.0, 0.0
        return row[0], float(row[1]), float(row[2])

    # ---------- CSV лог ----------
    def _append_trade_csv(self, *, ts: int, client_id: str, symbol: str, side: str, amount: float,
                          price: Optional[float], fee: float, realized: Optional[float], status: str, shadow: int):
        path = Path("runs/live/trades_log.csv")
        path.parent.mkdir(parents=True, exist_ok=True)
        header_needed = not path.exists()
        line = f"{ts},{client_id},{symbol},{side},{amount},{price if price is not None else ''},{fee},{'' if realized is None else realized},{status},{shadow}\n"
        with open(path, "a", encoding="utf-8") as f:
            if header_needed:
                f.write("ts,client_id,symbol,side,amount,price,fee,realized,status,shadow\n")
            f.write(line)

    # ---------- вставка/поиск ордера ----------
    def _insert_order(self, rec: Dict[str, Any]):
        con = sqlite3.connect(self.db_path)
        cur = con.cursor()
        cur.execute(
            "INSERT OR IGNORE INTO orders(client_id, ts, symbol, side, type, amount, price, fee, status, raw) "
            "VALUES(?,?,?,?,?,?,?,?,?,?)",
            (
                rec["client_id"], rec["ts"], rec["symbol"], rec["side"],
                rec["type"], rec["amount"], rec.get("price"), rec.get("fee"),
                rec.get("status", ""), json.dumps(rec.get("raw", {}))
            )
        )
        con.commit()
        con.close()

    def order_exists(self, client_id: str) -> bool:
        con = sqlite3.connect(self.db_path)
        cur = con.cursor()
        cur.execute("SELECT 1 FROM orders WHERE client_id=?", (client_id,))
        row = cur.fetchone()
        con.close()
        return row is not None

    # ---------- fee helper ----------
    def _extract_fee_cost(self, order: Dict[str, Any], amount: float, price: Optional[float]) -> float:
        fee_total = 0.0
        fee = order.get("fee")
        fees = order.get("fees")
        if isinstance(fee, dict):
            try:
                fee_total += float(fee.get("cost") or 0.0)
            except Exception:
                pass
        if isinstance(fees, list):
            for f in fees:
                try:
                    fee_total += float((f or {}).get("cost") or 0.0)
                except Exception:
                    pass
        if fee_total <= 0.0:
            px = (price if price is not None else (order.get("average") or order.get("price")))
            try:
                px = float(px) if px is not None else None
            except Exception:
                px = None
            if px is None:
                try:
                    px = float(self.exchange.fetch_ticker(order.get("symbol"))["last"])
                except Exception:
                    px = 0.0
            fee_total = float(px) * float(amount) * (self.fallback_fee_bps / 10000.0)
        return float(fee_total)

    # ---------- ордера ----------
    def create_market_order_idem(self, symbol: str, side: str, amount: float, client_id: Optional[str] = None,
                                 max_retries: int = 5, retry_sleep: float = 0.6) -> Dict[str, Any]:
        if client_id is None:
            client_id = f"MB-{symbol.replace('/', '')}-{side.upper()}-{int(time.time()*1000)}"

        params = {"newClientOrderId": client_id}
        for attempt in range(max_retries):
            try:
                if self.order_exists(client_id):
                    return {"clientOrderId": client_id, "status": "duplicate-skipped"}

                order = self.exchange.create_order(symbol, "market", side, amount, None, params)
                price = order.get("average") or order.get("price") or None
                try:
                    price_f = float(price) if price is not None else None
                except Exception:
                    price_f = None

                fee_cost = self._extract_fee_cost(order, amount=float(amount), price=price_f)

                rec = {
                    "client_id": client_id, "ts": int(time.time() * 1000), "symbol": symbol, "side": side,
                    "type": "market", "amount": float(amount),
                    "price": float(price_f) if price_f else None,
                    "fee": float(fee_cost),
                    "status": order.get("status", ""), "raw": order
                }
                self._insert_order(rec)

                realized = None
                if price_f is not None:
                    realized, closed_prev, closed_loss = self._apply_fill(symbol, side, float(amount), float(price_f), fee_cost)
                    if closed_prev:
                        if closed_loss:
                            self.inc_loss_streak()
                        else:
                            self.reset_loss_streak()

                self.inc_trade_count()

                self._append_trade_csv(
                    ts=rec["ts"], client_id=client_id, symbol=symbol, side=side, amount=float(amount),
                    price=price_f, fee=float(fee_cost), realized=realized, status=rec["status"], shadow=0
                )

                return order

            except Exception as e:
                if attempt + 1 >= max_retries:
                    raise
                time.sleep(retry_sleep * (2 ** attempt))

    def simulate_market_fill(self, symbol: str, side: str, amount: float, price: float, client_id: Optional[str] = None) -> Dict[str, Any]:
        if client_id is None:
            client_id = f"MB-{symbol.replace('/', '')}-{side.upper()}-SHADOW-{int(time.time()*1000)}"
        fee_cost = float(price) * float(amount) * (self.fallback_fee_bps / 10000.0)
        rec = {
            "client_id": client_id, "ts": int(time.time() * 1000), "symbol": symbol, "side": side,
            "type": "market", "amount": float(amount),
            "price": float(price), "fee": float(fee_cost),
            "status": "simulated", "raw": {"shadow": True}
        }
        self._insert_order(rec)

        realized, closed_prev, closed_loss = self._apply_fill(symbol, side, float(amount), float(price), fee_cost)
        if closed_prev:
            if closed_loss:
                self.inc_loss_streak()
            else:
                self.reset_loss_streak()

        self.inc_trade_count()
        self._append_trade_csv(
            ts=rec["ts"], client_id=client_id, symbol=symbol, side=side, amount=float(amount),
            price=float(price), fee=float(fee_cost), realized=realized, status="simulated", shadow=1
        )
        return rec


class LiveExecutor:
    def __init__(self, symbol: str = None, timeframe: str = None):
        # Метрики (поднимем сервер, если ещё не поднят)
        port = m.ensure_server()

        # базовые настройки
        self.symbol = symbol or os.getenv("MB_SYMBOL", "BTC/USDT")
        self.timeframe = timeframe or os.getenv("MB_TIMEFRAME", "1h")

        self.monitor = Monitor()
        self.cfg = config.load_yaml("risk_config.yaml") or {}
        self._cfg_mtime = self._get_cfg_mtime()
        self.risk_rules = (self.cfg.get("risk") or {})
        cost_cfg = (self.cfg.get("costs") or {})
        safety_cfg = (self.cfg.get("safety") or {})
        self.fallback_fee_bps = float(cost_cfg.get("fee_bps", 6.0))

        # безопасные флаги
        self.shadow = bool(int(os.getenv("MB_SHADOW_MODE", "0"))) or bool(safety_cfg.get("shadow_mode", False))
        self.kill_switch_path = str(safety_cfg.get("kill_switch_path", "runs/live/STOP"))

        # параметры стратегии
        self.best_params = self._load_best_params()

        # биржа + тестнет
        self.exchange = self._make_exchange()
        self.runner = OrderRunner(self.exchange, db_path="runs/live/state.db", fallback_fee_bps=self.fallback_fee_bps)
        self.feed = BarFeeder(self.exchange, self.symbol, self.timeframe)

        # guards
        self._last_bar_ts: Optional[pd.Timestamp] = None
        self._last_entry_ts: Optional[float] = None
        try:
            ts = self.runner.get_runtime("last_entry_ts", None)  # type: ignore
            self._last_entry_ts = float(ts) if ts is not None else None
        except Exception:
            self._last_entry_ts = None

        # NTP
        self._ntp_enabled: bool = bool(safety_cfg.get("enable_ntp_check", True))
        self._ntp_host: str = str(safety_cfg.get("ntp_host", "time.google.com"))
        self._ntp_max_drift: float = float(safety_cfg.get("ntp_max_drift_sec", 2.0))
        self._ntp_recheck_sec: int = int(safety_cfg.get("ntp_recheck_sec", 900))
        self._last_ntp_check_ts: float = 0.0
        self._last_ntp_offset: Optional[float] = None

        # метрики стартовые
        m.shadow_mode.set(1 if self.shadow else 0)
        m.interlock_ok.set(1.0)

    def _get_cfg_mtime(self) -> float:
        try:
            return os.path.getmtime("risk_config.yaml")
        except Exception:
            return 0.0

    def _hot_reload_if_changed(self):
        try:
            mtime = os.path.getmtime("risk_config.yaml")
        except Exception:
            return
        if mtime != self._cfg_mtime:
            try:
                new_cfg = config.load_yaml("risk_config.yaml") or {}
                self.cfg = new_cfg
                self.risk_rules = (new_cfg.get("risk") or {})
                cost_cfg = (new_cfg.get("costs") or {})
                safety_cfg = (new_cfg.get("safety") or {})
                self.fallback_fee_bps = float(cost_cfg.get("fee_bps", 6.0))
                self.shadow = bool(int(os.getenv("MB_SHADOW_MODE", "0"))) or bool(safety_cfg.get("shadow_mode", False))
                self.kill_switch_path = str(safety_cfg.get("kill_switch_path", "runs/live/STOP"))
                self._ntp_enabled = bool(safety_cfg.get("enable_ntp_check", True))
                self._ntp_host = str(safety_cfg.get("ntp_host", "time.google.com"))
                self._ntp_max_drift = float(safety_cfg.get("ntp_max_drift_sec", 2.0))
                self._ntp_recheck_sec = int(safety_cfg.get("ntp_recheck_sec", 900))
                self._cfg_mtime = mtime
                m.shadow_mode.set(1 if self.shadow else 0)
                self.monitor.notify("Hot-reloaded risk_config.yaml")
            except Exception as e:
                self.monitor.alert(f"Hot-reload failed: {e}")

    def _make_exchange(self):
        api_key = os.getenv("BINANCE_API_KEY", "")
        api_secret = os.getenv("BINANCE_API_SECRET", "")
        ex = ccxt.binanceusdm({
            "apiKey": api_key,
            "secret": api_secret,
            "enableRateLimit": True,
            "options": {"defaultType": "future"},
        })
        try:
            ex.set_sandbox_mode(True)
        except Exception:
            pass
        ex.load_markets()
        return ex

    def _load_best_params(self) -> RunParams:
        df = pd.read_csv("runs/grid_results.csv")
        best = df.sort_values(by=["ret_pct", "profit_factor", "sharpe", "trades"],
                              ascending=[False, False, False, False]).iloc[0]
        return RunParams(
            sl_atr=float(best["sl_atr"]),
            tp_atr=float(best["tp_atr"]),
            atr_len=int(best["atr_len"]),
            cooldown_min=int(best["cooldown_min"]),
            enter_th=float(best["enter_th"]),
            exit_th=float(best["exit_th"]),
            exit_confirm_bars=int(best["exit_confirm_bars"]),
            enter_on_next_open=False
        )

    # ---------- helpers ----------
    def _last_price(self) -> float:
        t = self.exchange.fetch_ticker(self.symbol)
        px = float(t["last"])
        m.price_last.set(px)
        return px

    def _round_amount(self, amount: float) -> float:
        mkt = self.exchange.market(self.symbol)
        prec = (mkt.get("precision") or {}).get("amount")
        if prec is not None:
            step = 10 ** (-prec)
            return math.floor(amount / step) * step
        return float(f"{amount:.6f}")

    # ---------- NTP ----------
    def _check_clock_drift(self) -> bool:
        if not self._ntp_enabled:
            return True
        now = time.time()
        if (now - self._last_ntp_check_ts) < max(5.0, float(self._ntp_recheck_sec)):
            if self._last_ntp_offset is None:
                return True
            m.ntp_offset.set(self._last_ntp_offset)
            return abs(self._last_ntp_offset) <= self._ntp_max_drift

        self._last_ntp_check_ts = now
        if ntplib is None:
            self.monitor.alert("NTP check enabled, but ntplib not installed — skipping check.")
            return True
        try:
            client = ntplib.NTPClient()
            resp = client.request(self._ntp_host, version=3, timeout=3)
            self._last_ntp_offset = float(resp.offset)
            m.ntp_offset.set(self._last_ntp_offset)
            ok = abs(self._last_ntp_offset) <= self._ntp_max_drift
            if not ok:
                self.monitor.alert(f"CLOCK DRIFT {self._last_ntp_offset:.3f}s > {self._ntp_max_drift:.2f}s — trading halted")
            return ok
        except Exception as e:
            self.monitor.alert(f"NTP check failed: {e} — proceeding cautiously")
            return True

    # ---------- интерлоки ----------
    def _update_daily_metrics(self):
        pnl = self.runner.daily_realized_pnl()
        m.daily_pnl.set(pnl)
        m.loss_streak.set(self.runner.get_loss_streak())
        m.trades_today.set(self.runner.get_trades_today())

    def _check_daily_loss(self) -> bool:
        base_equity = float(os.getenv("MB_EQUITY", self.risk_rules.get("equity_base", 10000)))
        max_loss_pct = float(self.risk_rules.get("max_daily_loss_pct", 2.5))
        realized = self.runner.daily_realized_pnl()
        loss_pct = (-realized / base_equity * 100.0) if realized < 0 else 0.0
        if loss_pct > max_loss_pct:
            self.monitor.alert(f"INTERLOCK: daily loss {loss_pct:.2f}% > {max_loss_pct:.2f}% — trading halted")
            return False
        return True

    def _check_notional_limit(self, extra_notional: float = 0.0) -> bool:
        limit_usd = float(self.risk_rules.get("max_notional_usd", 500.0))
        pos_side, qty, _ = self.runner.position(self.symbol)
        px = self._last_price()
        cur_notional = abs(qty * px)
        ok = (cur_notional + max(0.0, extra_notional)) <= limit_usd + 1e-6
        if not ok:
            self.monitor.alert(f"INTERLOCK: notional {cur_notional + extra_notional:.2f} > {limit_usd:.2f} USD")
        return ok

    def _check_daily_trades_limit(self) -> bool:
        max_trades = int(self.risk_rules.get("max_daily_trades", 50))
        trades = self.runner.get_trades_today()
        if trades >= max_trades:
            self.monitor.alert(f"INTERLOCK: daily trades {trades} >= {max_trades} — trading halted")
            return False
        return True

    def _check_loss_streak(self) -> bool:
        max_streak = int(self.risk_rules.get("max_consecutive_losses", 15))
        streak = self.runner.get_loss_streak()
        if streak >= max_streak:
            self.monitor.alert(f"INTERLOCK: losing streak {streak} >= {max_streak} — trading halted")
            return False
        return True

    def _enforce_interlocks(self, extra_notional: float = 0.0) -> bool:
        ok = (self._check_clock_drift()
              and self._check_daily_loss()
              and self._check_daily_trades_limit()
              and self._check_loss_streak()
              and self._check_notional_limit(extra_notional=extra_notional))
        m.interlock_ok.set(1.0 if ok else 0.0)
        self._update_daily_metrics()
        return ok

    # ---------- kill-switch ----------
    def _check_kill_switch(self) -> bool:
        return os.path.exists(self.kill_switch_path)

    def _panic_exit(self):
        side, qty, _ = self.runner.position(self.symbol)
        if qty <= 0:
            return
        opp = "sell" if side == "LONG" else "buy"
        try:
            price = self._last_price()
        except Exception:
            price = None
        try:
            if self.shadow:
                px = price if price is not None else 0.0
                self.runner.simulate_market_fill(self.symbol, opp, float(qty), float(px), client_id=f"MB-PANIC-{int(time.time()*1000)}")
            else:
                self.runner.create_market_order_idem(self.symbol, opp, float(qty), client_id=f"MB-PANIC-{int(time.time()*1000)}")
        except Exception as e:
            self.monitor.alert(f"Panic exit order failed: {e}")

    # ---------- логика сигналов / ордеров ----------
    def _compute_qty_for_entry(self) -> float:
        entry_notional = float(self.risk_rules.get("entry_notional_usd", 50.0))
        px = max(1e-9, self._last_price())
        qty = self._round_amount(entry_notional / px)
        return max(qty, 0.0)

    def _place_idem(self, side: str, amount: float, bar_ts: pd.Timestamp) -> Optional[Dict[str, Any]]:
        client_id = f"MB-{self.symbol.replace('/', '')}-{side.upper()}-{int(bar_ts.value // 1_000_000)}"
        try:
            if self.shadow:
                price = self._last_price()
                self.runner.simulate_market_fill(self.symbol, side, amount, price, client_id=client_id)
                self.monitor.notify(f"[SHADOW] {side.upper()} {self.symbol} qty={amount} @~{price:.2f}")
                return {"clientOrderId": client_id, "status": "shadow"}
            else:
                return self.runner.create_market_order_idem(self.symbol, side, amount, client_id=client_id)
        except Exception as e:
            self.monitor.alert(f"ORDER ERROR: {side} {amount} {e}")
            return None

    def _in_cooldown(self) -> bool:
        cd_min = int(getattr(self.best_params, "cooldown_min", 0) or 0)
        if cd_min <= 0 or self._last_entry_ts is None:
            return False
        return (time.time() - self._last_entry_ts) < (cd_min * 60)

    def _decide_and_execute(self):
        # Берём ТОЛЬКО закрытые свечи (анти-lookahead) + кэш из BarFeeder
        df = self.feed.get_closed_df()
        last_bar_ts = df["ts"].iloc[-1]
        last_close = float(df["close"].iloc[-1])

        if self._last_bar_ts is not None and last_bar_ts == self._last_bar_ts:
            return
        self._last_bar_ts = last_bar_ts

        brain = init_brain(self.cfg, df)
        v = float(brain.vote(df, len(df) - 1))
        m.vote_last.set(v)

        pos_side, qty, _ = self.runner.position(self.symbol)
        allow_short = bool(self.risk_rules.get("allow_short", False))
        partial_exit_frac = float(self.risk_rules.get("partial_exit_frac", 1.0))
        partial_exit_frac = min(max(partial_exit_frac, 0.0), 1.0)
        short_enter_th = float(self.risk_rules.get("short_enter_th", 0.15))
        reverse_on_exit = bool(self.risk_rules.get("reverse_on_exit", False))
        reverse_on_enter = bool(self.risk_rules.get("reverse_on_enter", False))

        # ----- EXIT / REDUCE / REVERSE -----
        if v <= self.best_params.exit_th and pos_side == "LONG" and qty > 0:
            sell_qty = max(self._round_amount(qty * partial_exit_frac), 0.0)
            if sell_qty <= 0: return
            if allow_short and reverse_on_exit and sell_qty >= qty - 1e-9:
                sell_qty = self._round_amount(qty + self._compute_qty_for_entry())
            if self._enforce_interlocks(extra_notional=0.0):
                if self._place_idem("sell", sell_qty, last_bar_ts):
                    self.monitor.notify(f"EXIT/REDUCE long {self.symbol} qty={sell_qty} @~{last_close:.2f} vote={v:.3f}")
            return

        if allow_short and v >= self.best_params.enter_th and pos_side == "SHORT" and qty > 0:
            buy_qty = max(self._round_amount(qty * partial_exit_frac), 0.0)
            if reverse_on_enter and buy_qty >= qty - 1e-9:
                buy_qty = self._round_amount(qty + self._compute_qty_for_entry())
            if buy_qty > 0 and self._enforce_interlocks(extra_notional=0.0):
                if self._place_idem("buy", buy_qty, last_bar_ts):
                    self.monitor.notify(f"COVER/REVERSE short {self.symbol} qty={buy_qty} @~{last_close:.2f} vote={v:.3f}")
            return

        # ----- ENTER / ADD -----
        if self._in_cooldown():
            return

        if v >= self.best_params.enter_th:
            if pos_side in ("FLAT",) or (allow_short and pos_side == "SHORT"):
                qty_to_buy = self._compute_qty_for_entry()
                extra_notional = qty_to_buy * last_close
                if qty_to_buy > 0 and self._enforce_interlocks(extra_notional=extra_notional):
                    if self._place_idem("buy", qty_to_buy, last_bar_ts):
                        self._last_entry_ts = time.time()
                        self.runner.set_runtime("last_entry_ts", self._last_entry_ts)
                        self.monitor.notify(f"ENTER long {self.symbol} qty={qty_to_buy} @~{last_close:.2f} vote={v:.3f}")
            return

        if allow_short and v <= short_enter_th:
            if pos_side in ("FLAT",) or pos_side == "LONG":
                qty_to_sell = self._compute_qty_for_entry()
                if qty_to_sell > 0 and self._enforce_interlocks(extra_notional=0.0):
                    if self._place_idem("sell", qty_to_sell, last_bar_ts):
                        self._last_entry_ts = time.time()
                        self.runner.set_runtime("last_entry_ts", self._last_entry_ts)
                        self.monitor.notify(f"ENTER short {self.symbol} qty={qty_to_sell} @~{last_close:.2f} vote={v:.3f}")
            return

    # ---------- основной цикл ----------
    def run_main_loop(self, sleep_sec: int = 60):
        self.monitor.notify(f"LiveExecutor started on Testnet. shadow_mode={self.shadow}")
        while True:
            try:
                self._hot_reload_if_changed()

                if self._check_kill_switch():
                    self.monitor.alert("KILL SWITCH ACTIVATED — PANIC EXIT")
                    self._panic_exit()
                    break

                if not self._enforce_interlocks():
                    self.monitor.alert("PANIC EXIT (interlock)")
                    self._panic_exit()
                    break

                self._decide_and_execute()

            except Exception as e:
                self.monitor.alert(f"Loop error: {e}")

            time.sleep(sleep_sec)

if __name__ == "__main__":
    LiveExecutor().run_main_loop(sleep_sec=60)
