# minibot.py — v2.8.0
# - STOP.flag (.\flags\STOP.flag) полностью блокирует торговые операции
# - heartbeat: logs\minibot.heartbeat.json
# - PID-файл: minibot\minibot.pid
# - реагирует на flags\CLOSE_ALL.flag (заглушка закрытия позиций)
# - простой тикер-цикл без asyncio (совместим с Python 3.13)
# - v2.8.0: интеграция SignalsPipeline (fetch → classify → journal → publish)

import os, time, json, signal, logging
from pathlib import Path

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("minibot")

ROOT      = Path(__file__).resolve().parents[1]
FLAGS     = ROOT / "flags"
LOGS      = ROOT / "logs"
STATE     = ROOT / "minibot" / "state"
PIDF      = Path(__file__).resolve().with_suffix(".pid")
HB_FILE   = LOGS / "minibot.heartbeat.json"
STOP      = FLAGS / "STOP.flag"
CLOSE_ALL = FLAGS / "CLOSE_ALL.flag"

# Pipeline configuration
SIGNALS_INTERVAL_SEC = 300  # 5 minutes between signal cycles

RUN = True
_signals_pipeline = None
_last_signals_cycle = 0


def get_signals_pipeline():
    """Lazy-load signals pipeline to avoid import errors on startup."""
    global _signals_pipeline
    if _signals_pipeline is None:
        try:
            from core.signals_pipeline import SignalsPipeline
            _signals_pipeline = SignalsPipeline()
            logger.info("SignalsPipeline initialized")
        except ImportError as e:
            logger.warning("SignalsPipeline not available: %s", e)
    return _signals_pipeline


def write_pid():
    try:
        PIDF.write_text(str(os.getpid()), encoding="utf-8")
    except Exception:
        pass


def remove_pid():
    try:
        PIDF.unlink(missing_ok=True)
    except Exception:
        pass


def write_hb(ok=True, extra=None):
    """Write heartbeat with optional extra data."""
    LOGS.mkdir(parents=True, exist_ok=True)
    data = {"ok": bool(ok), "ts": time.time()}
    if extra:
        data.update(extra)
    HB_FILE.write_text(json.dumps(data), encoding="utf-8")


def on_signal(sig, frame):
    global RUN
    logger.info("Received signal %s, shutting down...", sig)
    RUN = False


def close_all_positions_stub():
    # Здесь должна быть реальная логика закрытия позиций на бирже.
    # В заглушке — просто сжигаем флаг.
    try:
        CLOSE_ALL.unlink(missing_ok=True)
        logger.info("CLOSE_ALL flag processed (stub)")
    except Exception:
        pass


def run_signals_cycle():
    """Run signals pipeline cycle if interval has passed."""
    global _last_signals_cycle

    now = time.time()
    if now - _last_signals_cycle < SIGNALS_INTERVAL_SEC:
        return

    pipeline = get_signals_pipeline()
    if pipeline is None:
        return

    logger.info("Running signals cycle...")
    try:
        result = pipeline.run_cycle()
        _last_signals_cycle = now

        # Log result
        logger.info(
            "Signals cycle: status=%s, events=%d, published=%d, signals=%d",
            result.status.value,
            result.events_collected,
            result.events_published,
            result.signals_generated,
        )

        if result.errors:
            for err in result.errors:
                logger.warning("  Pipeline error: %s", err)

        # Update heartbeat with pipeline status
        write_hb(True, {"pipeline_status": result.status.value})

    except Exception as e:
        logger.error("Signals cycle failed: %s", e, exc_info=True)
        write_hb(True, {"pipeline_status": "error", "pipeline_error": str(e)})


def loop_once():
    # 1) heartbeat
    write_hb(True)

    # 2) CLOSE_ALL.flag
    if CLOSE_ALL.exists():
        close_all_positions_stub()

    # 3) STOP.flag
    if STOP.exists():
        # в стопе не торгуем, просто выходим из шага
        return

    # 4) Signals pipeline (every SIGNALS_INTERVAL_SEC)
    run_signals_cycle()

    # 5) основная торговая логика (здесь твои сигналы/стратегии)
    return


def main():
    signal.signal(signal.SIGINT, on_signal)
    try:
        signal.signal(signal.SIGTERM, on_signal)
    except Exception:
        pass

    FLAGS.mkdir(parents=True, exist_ok=True)
    LOGS.mkdir(parents=True, exist_ok=True)
    STATE.mkdir(parents=True, exist_ok=True)

    logger.info("MiniBot v2.8.0 starting...")
    logger.info("  ROOT: %s", ROOT)
    logger.info("  STOP flag: %s", STOP)
    logger.info("  Signals interval: %ds", SIGNALS_INTERVAL_SEC)

    write_pid()
    try:
        # Initialize pipeline on startup
        get_signals_pipeline()

        while RUN:
            loop_once()
            time.sleep(1.0)
    except KeyboardInterrupt:
        logger.info("KeyboardInterrupt received")
    finally:
        remove_pid()
        logger.info("MiniBot stopped")


if __name__ == "__main__":
    main()
