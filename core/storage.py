from __future__ import annotations
import json
from dataclasses import asdict
from pathlib import Path
from typing import List

# Импортируем обновленный тип
from .models import PositionInfo

class PositionStorage:
    def __init__(self, path_exec_positions: str, path_trades: str) -> None:
        self.exec_path = Path(path_exec_positions)
        self.trades_path = Path(path_trades)

        if self.exec_path.parent:
            self.exec_path.parent.mkdir(parents=True, exist_ok=True)
        if self.trades_path.parent:
            self.trades_path.parent.mkdir(parents=True, exist_ok=True)

    def load_positions(self) -> List[PositionInfo]:
        if not self.exec_path.exists():
            return []
        try:
            text = self.exec_path.read_text(encoding="utf-8")
            if not text.strip():
                return []
            raw = json.loads(text)
            if not isinstance(raw, list):
                # Если вдруг там словарь, возвращаем пустой список (защита от старых форматов)
                return []
            
            positions = []
            for item in raw:
                try:
                    positions.append(PositionInfo(**item))
                except Exception:
                    continue
            return positions
        except Exception:
            return []

    def save_positions(self, positions: List[PositionInfo]) -> None:
        try:
            data = [asdict(p) for p in positions]
            tmp_path = self.exec_path.with_suffix(self.exec_path.suffix + ".tmp")
            
            tmp_path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2),
                encoding="utf-8"
            )
            tmp_path.replace(self.exec_path)
        except Exception:
            pass

    def append_trade_record(self, trade_record: dict) -> None:
        try:
            line = json.dumps(trade_record, ensure_ascii=False)
            with self.trades_path.open("a", encoding="utf-8") as f:
                f.write(line + "\n")
        except Exception:
            pass
