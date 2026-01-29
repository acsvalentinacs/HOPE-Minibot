# -*- coding: utf-8 -*-
# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-29 04:00:00 UTC
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
        logger.info("AI-Gateway server starting...")
        yield
        logger.info("AI-Gateway server shutting down...")

    app = FastAPI(
        title="HOPE AI-Gateway",
        description="Intelligence Layer for HOPE Trading Bot",
        version="1.0.0",
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
        for module in ["sentiment", "regime", "doctor", "anomaly"]:
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
            total_modules=4,
            modules=modules,
            updated_at=datetime.utcnow().isoformat() + "Z",
        )

    @app.get("/status/{module}", response_model=ModuleStatusResponse)
    async def get_module_status(module: str):
        """Get specific module status."""
        sm = get_status_manager()

        if module not in ["sentiment", "regime", "doctor", "anomaly"]:
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

    return app


# === Server Runner ===

def run_server(host: str = "127.0.0.1", port: int = 8100):
    """Run the AI-Gateway server."""
    if not FASTAPI_AVAILABLE:
        logger.error("Cannot start server - FastAPI not installed")
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
