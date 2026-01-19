import typer
from pathlib import Path
from typing import Annotated

from . import config
from . import grid
from . import tune

app = typer.Typer(help="MiniBot: Backtesting and analysis CLI", add_completion=False)

@app.command(name="grid-search")
def grid_search_command(
    csv: Annotated[Path, typer.Option(
        "--csv",
        help="Path to CSV data file (OHLCV).",
        exists=True, file_okay=True, dir_okay=False, readable=True, resolve_path=True
    )] = Path("research/data/BTCUSDT_1h.csv"),
    equity: Annotated[float, typer.Option(help="Initial equity.")] = 10000.0,
    risk_per_trade: Annotated[float, typer.Option("--risk-per-trade", help="Risk fraction per trade.")] = 0.01,
):
    try:
        typer.echo("[MiniBot] Loading config 'risk_config.yaml'...")
        cfg = config.load_yaml("risk_config.yaml") or {}
        grid.grid_search(csv_path=str(csv), equity=float(equity), risk_per_trade=float(risk_per_trade), cfg=cfg)
        typer.echo("[MiniBot] Grid search complete.")
    except Exception as e:
        typer.echo(f"ERROR: Grid search failed. {e}", err=True)

@app.command(name="tune")
def tune_command(
    csv: Annotated[Path, typer.Option(
        "--csv",
        help="Path to CSV data file (OHLCV).",
        exists=True, file_okay=True, dir_okay=False, readable=True, resolve_path=True
    )] = Path("research/data/BTCUSDT_1h.csv"),
    equity: Annotated[float, typer.Option(help="Initial equity.")] = 10000.0,
    risk_per_trade: Annotated[float, typer.Option("--risk-per-trade", help="Risk fraction per trade.")] = 0.01,
    plateau_audit: Annotated[bool, typer.Option(
        "--plateau-audit",
        help="Run neighborhood robustness audit INSTEAD of WFA.",
        is_flag=True
    )] = False,
):
    """
    Run WFA or Plateau Audit on the best params from grid-search.
    """
    try:
        typer.echo("[MiniBot] Loading config 'risk_config.yaml'...")
        cfg = config.load_yaml("risk_config.yaml") or {}
        if plateau_audit:
            tune.run_plateau_audit(cfg=cfg)
            typer.echo("[MiniBot] Plateau audit complete.")
        else:
            tune.run_wfa(
                csv_path=str(csv),
                equity=float(equity),
                risk_per_trade=float(risk_per_trade),
                cfg=cfg
            )
            typer.echo("[MiniBot] WFA complete.")
    except Exception as e:
        typer.echo(f"ERROR: Tune failed. {e}", err=True)

@app.command(name="export")
def export_command(
    out: Annotated[Path, typer.Option("--out", help="Output CSV path.", resolve_path=True)] = Path("runs/top_results.csv"),
    limit: Annotated[int, typer.Option("--limit", "-n", help="Number of rows.")] = 100,
    sort_by: Annotated[str, typer.Option("--sort-by", "-s", help="Column to sort by.")] = "ret_pct",
):
    try:
        src = Path("runs/grid_results.csv")
        if not src.exists():
            raise FileNotFoundError("runs/grid_results.csv not found. Run grid-search first.")
        import pandas as pd
        df = pd.read_csv(src)
        if sort_by not in df.columns:
            sort_by = "ret_pct"
        df2 = df.sort_values(by=sort_by, ascending=False).head(int(limit)).reset_index(drop=True)
        df2.to_csv(out, index=False, encoding="utf-8")
        typer.echo(f"[MiniBot] Exported {len(df2)} rows to {out}")
    except Exception as e:
        typer.echo(f"ERROR: Export failed. {e}", err=True)

if __name__ == "__main__":
    app()
