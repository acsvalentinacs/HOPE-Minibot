import json
import time
from pathlib import Path
from typing import Dict, Any, Optional
from datetime import datetime, timezone

class LiveManager:
    """
    Хранит дневной PnL, баланс, активные позиции.
    Сохраняет:
      - state/live_daily_state.json
      - state/active_positions.json
    """

    def __init__(self, dry_run: bool = False, bot_version: str = "", environment: str = "paper"):
        self.root = Path(__file__).resolve().parent.parent
        self.state_dir = self.root / "state"
        self.state_dir.mkdir(exist_ok=True)

        self.daily_path = self.state_dir / "live_daily_state.json"
        self.pos_path = self.state_dir / "active_positions.json"

        self.dry_run = dry_run
        self.bot_version = bot_version
        self.environment = environment

        self.daily = self._load_json(self.daily_path, default={
            "date": self._today(),
            "start_balance": 0.0,
            "current_balance": 0.0,
            "daily_pnl": 0.0,
            "limit_hit": False,
        })

        self.positions: Dict[str, Dict[str, Any]] = self._load_json(self.pos_path, default={})

    # ----------------------------------------------------
    # Utils
    # ----------------------------------------------------
    def _today(self) -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")

    def _load_json(self, p: Path, default):
        try:
            if p.exists():
                return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            pass
        return default

    def _save(self):
        try:
            self.daily_path.write_text(json.dumps(self.daily, indent=2), encoding="utf-8")
            self.pos_path.write_text(json.dumps(self.positions, indent=2), encoding="utf-8")
        except Exception as e:
            print("LiveManager save error:", e)

    # ----------------------------------------------------
    # Balance update
    # ----------------------------------------------------
    def update_balance(self, balance: float):
        """
        Обновляет баланс и дневной PnL.
        """
        today = self._today()
        if self.daily["date"] != today:
            # Новый день — обнуление
            self.daily = {
                "date": today,
                "start_balance": balance,
                "current_balance": balance,
                "daily_pnl": 0.0,
                "limit_hit": False,
            }
        else:
            self.daily["current_balance"] = balance
            self.daily["daily_pnl"] = balance - self.daily.get("start_balance", balance)

        self._save()

    # ----------------------------------------------------
    # Trades
    # ----------------------------------------------------
    def on_trade_open(self, symbol: str, qty: float, price: float):
        self.positions[symbol] = {
            "symbol": symbol,
            "qty": qty,
            "open_price": price,
            "opened_at": time.time(),
        }
        self._save()

    def on_trade_close(self, symbol: str, pnl: float):
        if symbol in self.positions:
            del self.positions[symbol]
        # pnl учитывается только в смене баланса
        self._save()

    # ----------------------------------------------------
    # Limits
    # ----------------------------------------------------
    def mark_limit_hit(self):
        self.daily["limit_hit"] = True
        self._save()

    # ----------------------------------------------------
    # Status for /status
    # ----------------------------------------------------
    def get_status(self) -> Dict[str, Any]:
        return {
            "date": self.daily.get("date"),
            "bot_version": self.bot_version,
            "environment": self.environment,
            "daily_pnl": round(self.daily.get("daily_pnl", 0.0), 6),
            "limit_hit": self.daily.get("limit_hit", False),
            "active_positions": list(self.positions.keys()),
            "positions_count": len(self.positions),
        }
