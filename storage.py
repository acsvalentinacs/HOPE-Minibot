import os
import sqlite3

DB_PATH = os.path.join("runs", "minibot.db")

COLUMNS = [
    "created_at","csv_path","equity","risk_per_trade",
    "sl_atr","tp_atr","atr_len","cooldown_min","enter_th","exit_th","exit_confirm_bars",
    "trades","winrate","pnl","return_pct","maxdd","neg_maxdd","sharpe",
    "raw_log_path","status","duration_sec"
]

def ensure_db(db_path: str = DB_PATH) -> sqlite3.Connection:
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT,
            csv_path TEXT,
            equity REAL,
            risk_per_trade REAL,
            sl_atr REAL,
            tp_atr REAL,
            atr_len INTEGER,
            cooldown_min INTEGER,
            enter_th REAL,
            exit_th REAL,
            exit_confirm_bars INTEGER,
            trades INTEGER,
            winrate REAL,
            pnl REAL,
            return_pct REAL,
            maxdd REAL,
            neg_maxdd REAL,
            sharpe REAL,
            raw_log_path TEXT,
            status TEXT,
            duration_sec REAL
        )
    """)
    conn.commit()
    return conn

def insert_row(conn: sqlite3.Connection, row: dict) -> None:
    cols = ",".join(COLUMNS)
    placeholders = ",".join(["?"] * len(COLUMNS))
    vals = [row.get(k) for k in COLUMNS]
    conn.execute(f"INSERT INTO runs ({cols}) VALUES ({placeholders})", vals)
    conn.commit()
