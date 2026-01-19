from __future__ import annotations
import csv, json, glob
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import Optional, Dict, Any, Iterable, List, Tuple
import logging
import math

class ExportPeriod(Enum):
    DAY = "day"; WEEK = "week"; MONTH = "month"

def export_period_from_arg(arg: str) -> ExportPeriod:
    a = (arg or "").lower()
    if a in ("day","d"): return ExportPeriod.DAY
    if a in ("week","w"): return ExportPeriod.WEEK
    if a in ("month","m"): return ExportPeriod.MONTH
    return ExportPeriod.DAY

@dataclass
class Telemetry:
    project_root: Path
    logger: logging.Logger

    @property
    def logs_dir(self) -> Path:
        p = self.project_root / "logs"
        p.mkdir(parents=True, exist_ok=True)
        return p

    def _trades_file_for_date(self, dt: datetime) -> Path:
        return self.logs_dir / f"trades_{dt.strftime('%Y%m%d')}.jsonl"

    def _now_utc(self) -> datetime:
        return datetime.now(timezone.utc)

    # --- запись трейдов ---
    def record_entry(self, symbol: str, side: str, qty: float|None, price: float,
                     atr: float|None, confidence: float|None, reasons: List[str]|None):
        row = {
            "ts": self._now_utc().isoformat(),
            "event": "entry", "symbol": symbol, "side": side, "qty": qty,
            "price": price, "atr": atr, "confidence": confidence,
            "reasons": reasons or []
        }
        self._append(row)

    def record_exit(self, symbol: str, side: str, qty: float|None, price: float,
                    pnl_usd: float|None, pnl_pct: float|None, reason: str):
        row = {
            "ts": self._now_utc().isoformat(),
            "event": "exit", "symbol": symbol, "side": side, "qty": qty,
            "price": price, "pnl_usd": pnl_usd, "pnl_pct": pnl_pct, "exit_reason": reason
        }
        self._append(row)

    def _append(self, row: Dict[str, Any]):
        f = self._trades_file_for_date(self._now_utc())
        with f.open("a", encoding="utf-8") as w:
            w.write(json.dumps(row, ensure_ascii=False) + "\n")

    # --- чтение ---
    def iter_trades(self, start: datetime, end: datetime) -> Iterable[Dict[str, Any]]:
        pattern = str(self.logs_dir / "trades_*.jsonl")
        for path in sorted(glob.glob(pattern)):
            try:
                with open(path, "r", encoding="utf-8") as r:
                    for line in r:
                        line = line.strip()
                        if not line: continue
                        obj = json.loads(line)
                        ts = obj.get("ts")
                        if not ts: continue
                        try:
                            dt = datetime.fromisoformat(ts)
                        except Exception:
                            continue
                        if dt.tzinfo is None: dt = dt.replace(tzinfo=timezone.utc)
                        if start <= dt <= end: yield obj
            except Exception:
                continue

    def tail_trades_text(self, n: int = 10) -> str:
        items: List[Tuple[datetime, Dict[str,Any]]] = []
        pattern = str(self.logs_dir / "trades_*.jsonl")
        for path in glob.glob(pattern):
            try:
                for line in open(path, "r", encoding="utf-8"):
                    line=line.strip(); 
                    if not line: continue
                    obj=json.loads(line); ts=obj.get("ts"); 
                    if not ts: continue
                    try: dt = datetime.fromisoformat(ts)
                    except: continue
                    if dt.tzinfo is None: dt = dt.replace(tzinfo=timezone.utc)
                    items.append((dt,obj))
            except: 
                continue
        items.sort(key=lambda x:x[0], reverse=True)
        items = items[:max(1,int(n))]
        lines=[]
        for dt,obj in items:
            ev=obj.get("event"); sym=obj.get("symbol"); px=obj.get("price")
            if ev=="entry":
                conf=obj.get("confidence"); lines.append(f"{dt.isoformat()} | entry {sym} @ {px} | conf={round((conf or 0)*100,1)}%")
            elif ev=="exit":
                pnlp=obj.get("pnl_pct"); pnlu=obj.get("pnl_usd"); reason=obj.get("exit_reason","")
                lines.append(f"{dt.isoformat()} | exit  {sym} @ {px} | pnl={pnlu} USD ({pnlp}%) | {reason}")
            else:
                lines.append(f"{dt.isoformat()} | {ev} {sym} @ {px}")
        return "\n".join(lines) if lines else "(no trades yet)"

    # --- CSV экспорт PnL ---
    def _period_range(self, period: ExportPeriod) -> tuple[datetime, datetime]:
        now = self._now_utc()
        if period == ExportPeriod.DAY:
            start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        elif period == ExportPeriod.WEEK:
            start = now - timedelta(days=7)
        else:
            start = now - timedelta(days=30)
        return start, now

    def export_csv(self, period: ExportPeriod) -> str:
        start, end = self._period_range(period)
        rows = list(self.iter_trades(start, end))
        out = self._write_csv(period, start, end, rows)
        return out

    def _write_csv(self, period: ExportPeriod, start: datetime, end: datetime, rows: List[Dict[str,Any]]) -> str:
        out_file = self.logs_dir / f"pnl_{period.value}_{end.strftime('%Y%m%d_%H%M%S')}.csv"
        fields = ["ts","event","symbol","side","qty","price","atr","confidence","reasons","pnl_usd","pnl_pct","exit_reason"]
        with out_file.open("w", newline="", encoding="utf-8") as w:
            cw = csv.DictWriter(w, fieldnames=fields); cw.writeheader()
            for obj in rows:
                o=dict(obj); 
                if isinstance(o.get("reasons"), list): o["reasons"]=" | ".join(o["reasons"])
                cw.writerow({k:o.get(k,"") for k in fields})
        self.logger.info("EXPORT CSV %s → %s", period.value, out_file)
        return str(out_file)

    # --- агрегаты и расширенная статистика ---
    def compute_stats(self, period: ExportPeriod) -> dict:
        start, end = self._period_range(period)
        rows = list(self.iter_trades(start, end))
        wins=losses=0; pnl_total=0.0
        pnl_w=[]; pnl_l=[]
        for r in rows:
            if r.get("event")!="exit": continue
            pnlu=float(r.get("pnl_usd") or 0.0)
            pnl_total+=pnlu
            if pnlu>=0: wins+=1; pnl_w.append(pnlu)
            else: losses+=1; pnl_l.append(pnlu)
        total_exits=wins+losses
        win_rate=(wins/total_exits*100.0) if total_exits else 0.0
        avg_win=sum(pnl_w)/len(pnl_w) if pnl_w else 0.0
        avg_loss=sum(pnl_l)/len(pnl_l) if pnl_l else 0.0
        return {"period":period.value,"total_exits":total_exits,"wins":wins,"losses":losses,
                "win_rate_pct":win_rate,"avg_win_usd":avg_win,"avg_loss_usd":avg_loss,"pnl_total_usd":pnl_total}

    def symbol_breakdown(self, period: ExportPeriod) -> list[dict]:
        start, end = self._period_range(period)
        rows = list(self.iter_trades(start, end))
        bysym=defaultdict(lambda: {"exits":0,"wins":0,"losses":0,"pnl_total_usd":0.0})
        for r in rows:
            if r.get("event")!="exit": continue
            sym=r.get("symbol"); pnlu=float(r.get("pnl_usd") or 0.0)
            bysym[sym]["exits"]+=1
            bysym[sym]["pnl_total_usd"]+=pnlu
            if pnlu>=0: bysym[sym]["wins"]+=1
            else: bysym[sym]["losses"]+=1
        out=[]
        for s,d in bysym.items():
            wr=(d["wins"]/d["exits"]*100.0) if d["exits"] else 0.0
            out.append({"symbol":s, **d, "win_rate_pct":wr})
        out.sort(key=lambda x: x["pnl_total_usd"], reverse=True)
        return out

    def export_symbol_breakdown_csv(self, period: ExportPeriod) -> str:
        data=self.symbol_breakdown(period)
        out_file = self.logs_dir / f"stats_symbols_{period.value}_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.csv"
        fields=["symbol","exits","wins","losses","win_rate_pct","pnl_total_usd"]
        with out_file.open("w", newline="", encoding="utf-8") as w:
            cw=csv.DictWriter(w, fieldnames=fields); cw.writeheader()
            for row in data: cw.writerow({k:row.get(k,"") for k in fields})
        self.logger.info("EXPORT SYMBOL STATS %s → %s", period.value, out_file)
        return str(out_file)

    def corr_matrix(self, period: ExportPeriod) -> tuple[list[str], list[list[float]]]:
        """
        Корреляции по дневным суммам PnL (exit) по символам.
        """
        start, end = self._period_range(period)
        rows = list(self.iter_trades(start, end))
        daily_sym = defaultdict(lambda: defaultdict(float))  # day_key -> sym -> pnl_usd
        for r in rows:
            if r.get("event")!="exit": continue
            ts=r.get("ts")
            try: dt = datetime.fromisoformat(ts)
            except: continue
            if dt.tzinfo is None: dt=dt.replace(tzinfo=timezone.utc)
            day = dt.strftime("%Y-%m-%d")
            sym=r.get("symbol"); pnlu=float(r.get("pnl_usd") or 0.0)
            daily_sym[day][sym]+=pnlu
        # выравниваем по дням
        syms=sorted({s for d in daily_sym.values() for s in d.keys()})
        days=sorted(daily_sym.keys())
        series={s: [daily_sym[day].get(s,0.0) for day in days] for s in syms}

        def corr(x:list[float], y:list[float]) -> float:
            n=len(x)
            if n==0: return 0.0
            mx=sum(x)/n; my=sum(y)/n
            num=sum((a-mx)*(b-my) for a,b in zip(x,y))
            denx=math.sqrt(sum((a-mx)**2 for a in x))
            deny=math.sqrt(sum((b-my)**2 for b in y))
            if denx==0 or deny==0: return 0.0
            return max(-1.0, min(1.0, num/(denx*deny)))

        mat=[[0.0 for _ in syms] for _ in syms]
        for i,s1 in enumerate(syms):
            for j,s2 in enumerate(syms):
                mat[i][j]=corr(series[s1], series[s2])
        return syms, mat

    def export_corr_csv(self, period: ExportPeriod) -> str:
        syms, mat = self.corr_matrix(period)
        out_file = self.logs_dir / f"stats_corr_{period.value}_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.csv"
        with out_file.open("w", newline="", encoding="utf-8") as w:
            cw = csv.writer(w)
            cw.writerow(["symbol"]+syms)
            for i,s in enumerate(syms):
                cw.writerow([s]+[f"{mat[i][j]:.4f}" for j in range(len(syms))])
        self.logger.info("EXPORT CORR %s → %s", period.value, out_file)
        return str(out_file)

    def export_stats_csv(self, period: ExportPeriod) -> tuple[str,str,str]:
        base = self.compute_stats(period)
        out_stats = self.logs_dir / f"stats_{period.value}_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.csv"
        with out_stats.open("w", newline="", encoding="utf-8") as w:
            cw = csv.writer(w)
            for k,v in base.items(): cw.writerow([k,v])
        self.logger.info("EXPORT STATS %s → %s", period.value, out_stats)

        out_symbols = self.export_symbol_breakdown_csv(period)
        out_corr = self.export_corr_csv(period)
        return str(out_stats), out_symbols, out_corr
