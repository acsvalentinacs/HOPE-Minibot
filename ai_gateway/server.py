# -*- coding: utf-8 -*-
# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-29 04:00:00 UTC
# Modified by: Claude (opus-4.5)
# Modified at: 2026-02-03 19:20:00 UTC
# Change: Added DecisionEngine HTTP endpoints (/decision/evaluate, /decision/stats)
# Purpose: FastAPI HTTP server for AI-Gateway
# === END SIGNATURE ===
"""
AI-Gateway Server: HTTP API for AI module access.

Runs as separate process from Trading Core.
Provides REST API for:
- Running AI modules
- Querying status
- Managing module configuration
"""

from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from .contracts import ModuleStatus
from .jsonl_writer import get_writer, read_valid
from .status_manager import get_status_manager

logger = logging.getLogger(__name__)


# Check if FastAPI is available
try:
    from fastapi import FastAPI, HTTPException, BackgroundTasks
    from fastapi.responses import JSONResponse
    from pydantic import BaseModel
    FASTAPI_AVAILABLE = True
except ImportError:
    FASTAPI_AVAILABLE = False
    logger.warning("FastAPI not installed - server will not start")


# === Request/Response Models ===

if FASTAPI_AVAILABLE:

    class ModuleRunRequest(BaseModel):
        """Request to run an AI module."""
        module: str
        symbol: str = "BTCUSDT"
        params: Dict[str, Any] = {}

    class ModuleStatusResponse(BaseModel):
        """Module status response."""
        module: str
        status: str
        emoji: str
        last_run: Optional[str] = None
        error_count: int = 0
        enabled: bool = False

    class GatewayStatusResponse(BaseModel):
        """Full gateway status response."""
        gateway_status: str
        gateway_emoji: str
        active_modules: int
        total_modules: int
        modules: List[ModuleStatusResponse]
        updated_at: str

    class ArtifactResponse(BaseModel):
        """Artifact query response."""
        found: bool
        expired: bool = False
        artifact: Optional[Dict[str, Any]] = None


# === Background Tasks ===

async def run_sentiment_task(symbol: str, params: Dict[str, Any]) -> None:
    """Run sentiment analysis in background."""
    try:
        from .modules.sentiment import SentimentAnalyzer

        analyzer = SentimentAnalyzer()
        news = params.get("news_headlines", [])
        fg_index = params.get("fear_greed_index")
        price_change = params.get("price_change_24h")

        await analyzer.analyze(
            symbol=symbol,
            news_headlines=news,
            fear_greed_index=fg_index,
            price_change_24h=price_change,
        )
        logger.info(f"Sentiment analysis completed for {symbol}")
    except Exception as e:
        logger.error(f"Sentiment task failed: {e}")
        get_status_manager().mark_error("sentiment", str(e))


async def run_regime_task(symbol: str, params: Dict[str, Any]) -> None:
    """Run regime detection in background."""
    try:
        from .modules.regime import RegimeDetector
        from .modules.regime.detector import OHLCV

        candles_raw = params.get("candles", [])
        timeframe = params.get("timeframe", "4h")

        if not candles_raw:
            raise ValueError("No candles provided")

        candles = [
            OHLCV(
                timestamp=c.get("timestamp", 0),
                open=c.get("open", 0),
                high=c.get("high", 0),
                low=c.get("low", 0),
                close=c.get("close", 0),
                volume=c.get("volume", 0),
            )
            for c in candles_raw
        ]

        detector = RegimeDetector()
        detector.detect(symbol, candles, timeframe)
        logger.info(f"Regime detection completed for {symbol}")
    except Exception as e:
        logger.error(f"Regime task failed: {e}")
        get_status_manager().mark_error("regime", str(e))


async def run_doctor_task(strategy_id: str, params: Dict[str, Any]) -> None:
    """Run strategy diagnostics in background."""
    try:
        from .modules.doctor import StrategyDoctor

        trades = params.get("trades", [])
        regime = params.get("current_regime")

        doctor = StrategyDoctor()
        await doctor.diagnose(strategy_id, trades, regime)
        logger.info(f"Strategy diagnostics completed for {strategy_id}")
    except Exception as e:
        logger.error(f"Doctor task failed: {e}")
        get_status_manager().mark_error("doctor", str(e))


async def run_anomaly_task(params: Dict[str, Any]) -> None:
    """Run anomaly scan in background."""
    try:
        from .modules.anomaly import AnomalyScanner

        tickers = params.get("tickers", [])
        trades = params.get("recent_trades")

        scanner = AnomalyScanner()
        scanner.scan(tickers, trades)
        logger.info("Anomaly scan completed")
    except Exception as e:
        logger.error(f"Anomaly task failed: {e}")
        get_status_manager().mark_error("anomaly", str(e))


# === FastAPI App ===

def create_app() -> "FastAPI":
    """Create FastAPI application."""
    if not FASTAPI_AVAILABLE:
        raise RuntimeError("FastAPI not installed")

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        """Application lifespan handler."""
        logger.info("=" * 50)
        logger.info("AI-GATEWAY v2.1.0 STARTING")
        logger.info("=" * 50)

        # Validate configuration (fail-open with warnings)
        from .config import validate_config
        config_errors = validate_config()
        for error in config_errors:
            logger.warning("CONFIG: %s", error)

        # Initialize scheduler
        from .scheduler import get_scheduler
        scheduler = get_scheduler()

        # Auto-start enabled modules
        try:
            results = await scheduler.start_all_enabled()
            for module_id, success in results.items():
                if success:
                    logger.info(f"Module {module_id} auto-started")
        except Exception as e:
            logger.warning(f"Auto-start failed: {e}")

        # Start Price Feed Bridge for real-time outcome tracking
        try:
            from .feeds.price_bridge import get_price_bridge
            bridge = get_price_bridge()
            if await bridge.start():
                logger.info("PriceFeedBridge auto-started for outcome tracking")

                # Auto-subscribe to SCALP_FRIENDLY_COINS for real-time tracking
                try:
                    from config.heavy_coins_blacklist import SCALP_FRIENDLY_COINS
                    feed = bridge._get_feed()
                    if feed and SCALP_FRIENDLY_COINS:
                        await feed.subscribe(list(SCALP_FRIENDLY_COINS))
                        logger.info(f"Auto-subscribed to {len(SCALP_FRIENDLY_COINS)} scalp-friendly coins")
                except ImportError:
                    logger.debug("heavy_coins_blacklist not available, skipping auto-subscribe")
                except Exception as sub_err:
                    logger.warning(f"Auto-subscribe to scalp coins failed: {sub_err}")
        except Exception as e:
            logger.warning(f"PriceFeedBridge auto-start failed: {e}")

        yield

        # Stop all modules gracefully
        logger.info("AI-Gateway server shutting down...")

        # Stop Price Feed Bridge
        try:
            from .feeds.price_bridge import get_price_bridge
            bridge = get_price_bridge()
            await bridge.stop()
            logger.info("PriceFeedBridge stopped")
        except Exception as e:
            logger.warning(f"PriceFeedBridge shutdown failed: {e}")

        try:
            await scheduler.stop_all(timeout=10.0)
        except Exception as e:
            logger.warning(f"Graceful shutdown failed: {e}")

    app = FastAPI(
        title="HOPE AI-Gateway",
        description="Intelligence Layer for HOPE Trading Bot",
        version="2.1.0",
        lifespan=lifespan,
    )

    # === Health Endpoints ===

    @app.get("/health")
    async def health_check():
        """Health check endpoint."""
        return {"status": "ok", "timestamp": datetime.utcnow().isoformat() + "Z"}

    @app.get("/status", response_model=GatewayStatusResponse)
    async def get_gateway_status():
        """Get full gateway status."""
        sm = get_status_manager()

        modules = []
        for module in ["sentiment", "regime", "doctor", "anomaly", "self_improver"]:
            last_run = sm.get_last_run(module)
            modules.append(ModuleStatusResponse(
                module=module,
                status=sm.get_status(module).value,
                emoji=sm.get_emoji(module),
                last_run=last_run.isoformat() + "Z" if last_run else None,
                error_count=sm.get_error_count(module),
                enabled=sm.is_enabled(module),
            ))

        gateway_status = sm.get_gateway_status()
        return GatewayStatusResponse(
            gateway_status=gateway_status.value,
            gateway_emoji={"healthy": "🟢", "warning": "🟡", "error": "🔴", "disabled": "⚪"}.get(gateway_status.value, "⚪"),
            active_modules=sm.get_active_count(),
            total_modules=5,
            modules=modules,
            updated_at=datetime.utcnow().isoformat() + "Z",
        )

    @app.get("/status/{module}", response_model=ModuleStatusResponse)
    async def get_module_status(module: str):
        """Get specific module status."""
        sm = get_status_manager()

        if module not in ["sentiment", "regime", "doctor", "anomaly", "self_improver"]:
            raise HTTPException(status_code=404, detail=f"Module '{module}' not found")

        last_run = sm.get_last_run(module)
        return ModuleStatusResponse(
            module=module,
            status=sm.get_status(module).value,
            emoji=sm.get_emoji(module),
            last_run=last_run.isoformat() + "Z" if last_run else None,
            error_count=sm.get_error_count(module),
            enabled=sm.is_enabled(module),
        )

    # === Module Control ===

    @app.post("/modules/{module}/enable")
    async def enable_module(module: str):
        """Enable a module."""
        sm = get_status_manager()
        if sm.enable_module(module):
            return {"status": "enabled", "module": module}
        raise HTTPException(status_code=404, detail=f"Module '{module}' not found")

    @app.post("/modules/{module}/disable")
    async def disable_module(module: str):
        """Disable a module."""
        sm = get_status_manager()
        if sm.disable_module(module):
            return {"status": "disabled", "module": module}
        raise HTTPException(status_code=404, detail=f"Module '{module}' not found")

    # === Lifecycle Control (Scheduler) ===

    @app.post("/modules/{module}/start")
    async def start_module(module: str):
        """Start a module (begin scheduled execution)."""
        from .scheduler import get_scheduler
        scheduler = get_scheduler()

        if module not in ["sentiment", "regime", "doctor", "anomaly", "self_improver"]:
            raise HTTPException(status_code=404, detail=f"Module '{module}' not found")

        success = await scheduler.start_module(module)
        if success:
            return {"status": "started", "module": module}
        raise HTTPException(status_code=400, detail=f"Failed to start module '{module}'")

    @app.post("/modules/{module}/stop")
    async def stop_module(module: str):
        """Stop a module (halt scheduled execution)."""
        from .scheduler import get_scheduler
        scheduler = get_scheduler()

        if module not in ["sentiment", "regime", "doctor", "anomaly", "self_improver"]:
            raise HTTPException(status_code=404, detail=f"Module '{module}' not found")

        success = await scheduler.stop_module(module)
        return {"status": "stopped" if success else "error", "module": module}

    @app.post("/modules/{module}/restart")
    async def restart_module(module: str):
        """Restart a module."""
        from .scheduler import get_scheduler
        scheduler = get_scheduler()

        if module not in ["sentiment", "regime", "doctor", "anomaly", "self_improver"]:
            raise HTTPException(status_code=404, detail=f"Module '{module}' not found")

        success = await scheduler.restart_module(module)
        return {"status": "restarted" if success else "error", "module": module}

    @app.post("/modules/{module}/run-now")
    async def run_module_now(module: str):
        """Execute module once immediately."""
        from .scheduler import get_scheduler
        scheduler = get_scheduler()

        if module not in ["sentiment", "regime", "doctor", "anomaly", "self_improver"]:
            raise HTTPException(status_code=404, detail=f"Module '{module}' not found")

        result = await scheduler.run_module_now(module)
        return {
            "status": "executed",
            "module": module,
            "artifact_produced": result is not None,
        }

    @app.get("/scheduler/info")
    async def get_scheduler_info():
        """Get scheduler and all modules info."""
        from .scheduler import get_scheduler
        scheduler = get_scheduler()
        return {
            "running_modules": scheduler.get_running_modules(),
            "modules": scheduler.get_all_modules_info(),
        }

    # === Diagnostics ===

    @app.get("/diagnostics")
    async def run_diagnostics():
        """Run full gateway diagnostics."""
        from .diagnostics import run_health_check
        return await run_health_check()

    @app.get("/diagnostics/telegram")
    async def get_diagnostics_telegram():
        """Get diagnostics formatted for Telegram."""
        from .diagnostics import GatewayDiagnostics, format_health_report_telegram
        diag = GatewayDiagnostics()
        report = await diag.run_all_checks()
        return {"block": format_health_report_telegram(report)}

    # === Price Feed Bridge (Real-time Outcome Tracking) ===

    @app.get("/price-feed/status")
    async def get_price_feed_status():
        """Get price feed bridge status and statistics."""
        from .feeds.price_bridge import get_price_bridge
        bridge = get_price_bridge()
        return {
            "status": "running" if bridge.is_running else "stopped",
            "connected": bridge.is_connected,
            "stats": bridge.get_stats(),
        }

    @app.get("/price-feed/prices")
    async def get_current_prices():
        """
        Get all cached prices from the feed.

        PriceFeed V1 Contract:
        - Returns ALL subscribed symbols, even if price not yet received
        - Missing price = {"price": null, "stale": true}
        - Includes staleness indicator (> 60s = stale)
        """
        import time
        from .feeds.price_bridge import get_price_bridge
        bridge = get_price_bridge()

        # Get subscribed symbols from underlying feed
        feed = bridge._get_feed() if bridge._feed else None
        subscribed = list(feed.symbols) if feed else []

        # Get prices with staleness
        raw_prices = bridge.get_all_prices()
        last_updates = bridge._last_update

        MAX_AGE_SEC = 60  # Price older than this is stale

        prices_with_contract = {}
        now = time.time()

        # Include ALL subscribed symbols
        all_symbols = set(subscribed) | set(raw_prices.keys())

        for symbol in all_symbols:
            price = raw_prices.get(symbol)
            last_update = last_updates.get(symbol, 0)
            age = now - last_update if last_update > 0 else float("inf")

            prices_with_contract[symbol] = {
                "price": price,  # null if missing
                "last_update": last_update if last_update > 0 else None,
                "age_sec": round(age) if age != float("inf") else None,
                "stale": price is None or age > MAX_AGE_SEC,
                "subscribed": symbol in subscribed,
            }

        return {
            "count": len(prices_with_contract),
            "subscribed_count": len(subscribed),
            "subscribed": subscribed,
            "prices": prices_with_contract,
        }

    @app.get("/price-feed/prices/{symbol}")
    async def get_symbol_price(symbol: str):
        """Get cached price for a specific symbol."""
        from .feeds.price_bridge import get_price_bridge
        bridge = get_price_bridge()
        price = bridge.get_price(symbol)
        if price is None:
            raise HTTPException(status_code=404, detail=f"No price for {symbol}")
        return {"symbol": symbol.upper(), "price": price}

    @app.post("/price-feed/start")
    async def start_price_feed():
        """Start the price feed bridge."""
        from .feeds.price_bridge import get_price_bridge
        bridge = get_price_bridge()
        if bridge.is_running:
            return {"status": "already_running"}
        success = await bridge.start()
        return {"status": "started" if success else "failed"}

    @app.post("/price-feed/stop")
    async def stop_price_feed():
        """Stop the price feed bridge."""
        from .feeds.price_bridge import get_price_bridge
        bridge = get_price_bridge()
        if not bridge.is_running:
            return {"status": "already_stopped"}
        await bridge.stop()
        return {"status": "stopped"}

    @app.post("/price-feed/subscribe")
    async def subscribe_symbols(symbols: List[str]):
        """Subscribe to additional symbols."""
        from .feeds.price_bridge import get_price_bridge
        bridge = get_price_bridge()
        feed = bridge._get_feed()
        await feed.subscribe(symbols)
        return {"status": "subscribed", "symbols": symbols}

    # === Outcome Tracking ===

    @app.get("/outcomes/stats")
    async def get_outcome_stats():
        """Get outcome tracking statistics."""
        from .feeds.price_bridge import get_price_bridge
        bridge = get_price_bridge()
        tracker = bridge._get_outcome_tracker()
        return {
            "stats": tracker.get_stats(),
            "active_symbols": list(tracker.active_symbols),
        }

    @app.get("/outcomes/pending")
    async def get_pending_outcomes():
        """Get list of signals pending outcome."""
        from .feeds.price_bridge import get_price_bridge
        bridge = get_price_bridge()
        tracker = bridge._get_outcome_tracker()
        return {
            "count": len(tracker._active),
            "signals": [
                {
                    "signal_id": s.signal_id,
                    "symbol": s.symbol,
                    "entry_price": s.entry_price,
                    "direction": s.direction,
                    "entry_time": s.entry_time.isoformat() + "Z",
                    "prices_collected": len(s.prices),
                    "mfe": round(s.mfe, 4),
                    "mae": round(s.mae, 4),
                }
                for s in list(tracker._active.values())[:50]  # Limit to 50
            ],
        }

    @app.get("/outcomes/completed")
    async def get_completed_outcomes(limit: int = 20):
        """Get recent completed outcomes."""
        from .feeds.price_bridge import get_price_bridge
        bridge = get_price_bridge()
        tracker = bridge._get_outcome_tracker()
        outcomes = tracker.get_completed_outcomes()
        return {
            "total": len(outcomes),
            "outcomes": outcomes[-limit:],  # Last N
        }

    @app.post("/outcomes/track")
    async def track_signal(signal: Dict[str, Any]):
        """
        Register a signal for outcome tracking.

        Required fields in signal:
        - symbol: Trading pair (e.g., "BTCUSDT")
        - price: Entry price
        - direction: "Long" or "Short" (default: "Long")

        Returns signal_id for tracking.
        """
        from .feeds.price_bridge import get_price_bridge
        bridge = get_price_bridge()
        tracker = bridge._get_outcome_tracker()
        feed = bridge._get_feed()

        # Validate required fields
        if "symbol" not in signal:
            raise HTTPException(status_code=400, detail="Missing 'symbol' field")
        if "price" not in signal:
            raise HTTPException(status_code=400, detail="Missing 'price' field")

        # Register signal
        signal_id = tracker.register_signal(signal)

        # Auto-subscribe to symbol for price updates
        symbol = signal.get("symbol", "").upper()
        if not symbol.endswith("USDT"):
            symbol = symbol + "USDT"
        await feed.subscribe([symbol])

        return {
            "status": "tracking",
            "signal_id": signal_id,
            "symbol": symbol,
            "entry_price": signal.get("price"),
        }

    # === Module Execution ===

    @app.post("/run/{module}")
    async def run_module(module: str, request: ModuleRunRequest, background_tasks: BackgroundTasks):
        """Run an AI module (async)."""
        sm = get_status_manager()

        if not sm.is_enabled(module):
            raise HTTPException(status_code=400, detail=f"Module '{module}' is disabled")

        if module == "sentiment":
            background_tasks.add_task(run_sentiment_task, request.symbol, request.params)
        elif module == "regime":
            background_tasks.add_task(run_regime_task, request.symbol, request.params)
        elif module == "doctor":
            strategy_id = request.params.get("strategy_id", "default")
            background_tasks.add_task(run_doctor_task, strategy_id, request.params)
        elif module == "anomaly":
            background_tasks.add_task(run_anomaly_task, request.params)
        else:
            raise HTTPException(status_code=404, detail=f"Module '{module}' not found")

        return {"status": "started", "module": module, "symbol": request.symbol}

    # === Artifact Access ===

    @app.get("/artifacts/{module}", response_model=ArtifactResponse)
    async def get_artifact(module: str):
        """Get latest valid artifact for module."""
        artifact = read_valid(module)

        if artifact is None:
            # Check if file exists but expired
            from .jsonl_writer import get_writer
            writer = get_writer()
            stats = writer.get_file_stats(module)

            if stats.get("exists"):
                return ArtifactResponse(found=True, expired=True, artifact=None)
            return ArtifactResponse(found=False, expired=False, artifact=None)

        return ArtifactResponse(found=True, expired=False, artifact=artifact)

    @app.get("/artifacts/{module}/history")
    async def get_artifact_history(module: str, count: int = 10):
        """Get recent artifacts for module."""
        from .jsonl_writer import read_latest
        artifacts = read_latest(module, count)
        return {"module": module, "count": len(artifacts), "artifacts": artifacts}

    # === Telegram Display ===

    @app.get("/telegram/status-block")
    async def get_telegram_status_block():
        """Get pre-formatted status block for Telegram."""
        sm = get_status_manager()
        return {"block": sm.format_status_block()}

    @app.get("/telegram/module-detail/{module}")
    async def get_telegram_module_detail(module: str):
        """Get pre-formatted module detail for Telegram."""
        sm = get_status_manager()
        return {"block": sm.format_detail_block(module)}

    # === Self-Improver Specific Endpoints ===

    @app.get("/self-improver/status")
    async def get_self_improver_status():
        """Get detailed self-improver status."""
        from .scheduler import get_scheduler
        scheduler = get_scheduler()

        module = scheduler._modules.get("self_improver")
        if module is None or not hasattr(module, 'get_loop'):
            return {
                "status": "not_initialized",
                "message": "Self-improver module not started. Enable with POST /modules/self_improver/enable"
            }

        loop = module.get_loop()
        if loop is None:
            return {"status": "not_initialized"}

        info = loop.get_info()
        return {
            "status": "running" if module.is_running else "stopped",
            "model_version": info.get("model_version"),
            "is_trained": info.get("is_trained"),
            "consecutive_losses": info.get("consecutive_losses"),
            "active_signals": info.get("active_signals"),
            "completed_signals": info.get("completed_signals"),
            "ab_test_active": info.get("ab_test_active"),
            "retrain_threshold": info.get("retrain_threshold"),
            "outcomes_until_retrain": info.get("outcomes_until_retrain"),
        }

    @app.post("/self-improver/predict")
    async def self_improver_predict(signal: Dict[str, Any]):
        """
        Get AI prediction for a signal.

        Required fields:
        - symbol: Trading pair (e.g., "BTCUSDT")
        - price: Current price
        - delta_pct: Price delta percentage
        - direction: "Long" or "Short"
        """
        from .scheduler import get_scheduler
        scheduler = get_scheduler()

        module = scheduler._modules.get("self_improver")
        if module is None or not hasattr(module, 'get_loop'):
            raise HTTPException(status_code=400, detail="Self-improver not initialized")

        loop = module.get_loop()
        if loop is None:
            raise HTTPException(status_code=400, detail="Self-improver loop not started")

        prediction = loop.predict(signal)
        return prediction

    @app.post("/self-improver/retrain")
    async def force_retrain():
        """Force model retraining (requires confirmation)."""
        from .scheduler import get_scheduler
        scheduler = get_scheduler()

        module = scheduler._modules.get("self_improver")
        if module is None or not hasattr(module, 'get_loop'):
            raise HTTPException(status_code=400, detail="Self-improver not initialized")

        loop = module.get_loop()
        if loop is None:
            raise HTTPException(status_code=400, detail="Self-improver loop not started")

        result = await loop._retrain()
        return {
            "status": "retrained" if result else "failed",
            "model_version": loop.model_registry.get_active_version(),
        }

    @app.post("/self-improver/rollback")
    async def rollback_model():
        """Rollback to previous model version."""
        from .scheduler import get_scheduler
        scheduler = get_scheduler()

        module = scheduler._modules.get("self_improver")
        if module is None or not hasattr(module, 'get_loop'):
            raise HTTPException(status_code=400, detail="Self-improver not initialized")

        loop = module.get_loop()
        if loop is None:
            raise HTTPException(status_code=400, detail="Self-improver loop not started")

        loop._trigger_rollback()
        return {
            "status": "rolled_back",
            "model_version": loop.model_registry.get_active_version(),
        }

    @app.get("/self-improver/model")
    async def get_model_info():
        """Get current model information."""
        from .scheduler import get_scheduler
        scheduler = get_scheduler()

        module = scheduler._modules.get("self_improver")
        if module is None or not hasattr(module, 'get_loop'):
            return {"status": "not_initialized"}

        loop = module.get_loop()
        if loop is None:
            return {"status": "not_initialized"}

        registry = loop.model_registry
        return {
            "active_version": registry.get_active_version(),
            "is_trained": loop.classifier.is_trained if loop.classifier else False,
            "registry_stats": registry.get_stats(),
        }

    # === Decision Engine Endpoints ===

    @app.post("/decision/evaluate")
    async def evaluate_signal(signal: Dict[str, Any]):
        """
        Evaluate a signal through DecisionEngine.

        Required fields:
        - symbol: Trading pair (e.g., "BTCUSDT")
        - price: Current price
        - direction: "Long" or "Short"
        - delta_pct: Price change percentage

        Returns decision: BUY or SKIP with reasoning.
        """
        import time as time_module
        from .core.decision_engine import get_decision_engine, SignalContext, Action
        from .contracts import MarketRegime

        engine = get_decision_engine()

        # Build signal context
        ctx = SignalContext(
            signal_id=signal.get("signal_id", f"api_{int(time_module.time()*1000)}"),
            symbol=signal.get("symbol", "UNKNOWN"),
            price=float(signal.get("price", 0)),
            direction=signal.get("direction", "Long"),
            delta_pct=float(signal.get("delta_pct", 0)),
            volume_24h=float(signal.get("volume_24h", 0)),
            raw_signal=signal,
        )

        # Get latest regime from artifact
        regime_artifact = read_valid("regime")
        if regime_artifact:
            try:
                regime_str = regime_artifact.get("current_regime")
                if regime_str:
                    ctx.regime = MarketRegime(regime_str)
            except (ValueError, KeyError):
                pass
            ctx.anomaly_score = regime_artifact.get("market_stress_level", 0)

        # Get latest anomaly from artifact
        anomaly_artifact = read_valid("anomaly")
        if anomaly_artifact:
            ctx.anomaly_score = anomaly_artifact.get("market_stress_level", 0)

        # Evaluate
        decision = engine.evaluate(ctx)

        # Build response
        checks_failed = [k for k, v in decision.checks_passed.items() if not v]
        skip_reasons = [r.value for r in decision.reasons] if decision.reasons else []

        return {
            "action": decision.action.value,
            "skip_reasons": skip_reasons,
            "confidence": decision.confidence,
            "checks_passed": decision.checks_passed,
            "checks_failed": checks_failed,
            "checks_values": decision.checks_values,
            "position_size_modifier": decision.position_size_modifier,
            "signal_id": ctx.signal_id,
            "timestamp": decision.timestamp,
        }

    @app.get("/decision/stats")
    async def get_decision_stats():
        """Get DecisionEngine statistics."""
        from .core.decision_engine import get_decision_engine

        engine = get_decision_engine()
        return engine.get_stats()

    @app.get("/decision/config")
    async def get_decision_config():
        """Get DecisionEngine policy configuration."""
        from .core.decision_engine import get_decision_engine

        engine = get_decision_engine()
        config = engine.config
        return {
            "prediction_min": config.prediction_min,
            "prediction_strong": config.prediction_strong,
            "anomaly_max": config.anomaly_max,
            "volume_min_24h": config.volume_min_24h,
            "max_positions": config.max_positions,
            "cooldown_seconds": config.cooldown_seconds,
            "allowed_regimes": [r.value for r in config.allowed_regimes],
        }

    return app


# === Server Runner ===

def run_server(host: str = "127.0.0.1", port: int = 8100):
    """Run the AI-Gateway server."""
    if not FASTAPI_AVAILABLE:
        logger.error("Cannot start server - FastAPI not installed")
        return

    # Single-instance check
    import sys
    sys.path.insert(0, str(Path(__file__).parent.parent))
    from core.lockfile import ProcessLock

    lock = ProcessLock("gateway")
    if not lock.acquire():
        owner_pid = lock.get_owner()
        logger.error(f"Another Gateway instance is running (PID {owner_pid})")
        return

    try:
        import uvicorn
        app = create_app()
        uvicorn.run(app, host=host, port=port)
    except ImportError:
        logger.error("uvicorn not installed - cannot start server")
    except Exception as e:
        logger.error(f"Server failed: {e}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run_server()
