# === AI SIGNATURE ===
# Module: hope_core/api_server.py
# Created by: Claude (opus-4.5)
# Created at: 2026-02-04 10:45:00 UTC
# Purpose: HTTP API Server for HOPE Core with FastAPI
# === END SIGNATURE ===
"""
HOPE Core HTTP API Server

Provides HTTP endpoints for:
- Health checks
- Signal submission
- Status monitoring
- Emergency controls
- Guardian integration

Compatible with existing autotrader.py API.
"""

from typing import Dict, Any, Optional
from datetime import datetime, timezone
from pathlib import Path
import asyncio
import json
import sys

# Add paths
sys.path.insert(0, str(Path(__file__).parent))

try:
    from fastapi import FastAPI, HTTPException, BackgroundTasks, Request
    from fastapi.responses import JSONResponse
    from pydantic import BaseModel, Field
    import uvicorn
    HAS_FASTAPI = True
except ImportError:
    HAS_FASTAPI = False
    print("WARNING: FastAPI not installed. Run: pip install fastapi uvicorn")

from hope_core import HopeCore, HopeCoreConfig


# =============================================================================
# PYDANTIC MODELS
# =============================================================================

if HAS_FASTAPI:
    
    class SignalRequest(BaseModel):
        """Signal submission request."""
        symbol: str = Field(..., pattern=r"^[A-Z]+USDT$")
        score: float = Field(..., ge=0, le=100)  # Accept 0-100, normalize in handler
        source: str = Field(default="API")
        confidence: Optional[float] = Field(default=None, ge=0, le=1)
        strategy: Optional[str] = None
        metadata: Optional[Dict[str, Any]] = None
    
    class EmergencyStopRequest(BaseModel):
        """Emergency stop request."""
        reason: str
        close_positions: bool = True
    
    class CircuitBreakerReset(BaseModel):
        """Circuit breaker reset request."""
        confirm: bool = True


# =============================================================================
# API SERVER
# =============================================================================

class HopeCoreAPIServer:
    """
    HTTP API Server for HOPE Core.
    
    Provides REST endpoints compatible with existing autotrader.py.
    """
    
    def __init__(self, core: HopeCore, host: str = "127.0.0.1", port: int = 8200):
        """
        Initialize API server.
        
        Args:
            core: HopeCore instance
            host: Bind host
            port: Bind port
        """
        self.core = core
        self.host = host
        self.port = port
        
        if not HAS_FASTAPI:
            raise RuntimeError("FastAPI not installed")
        
        self.app = FastAPI(
            title="HOPE Core API",
            description="HOPE AI Trading System Core v2.0",
            version="2.0.0",
        )
        
        self._setup_routes()
        self._setup_middleware()
    
    def _setup_middleware(self):
        """Setup middleware."""
        
        @self.app.middleware("http")
        async def add_timing_header(request: Request, call_next):
            start = datetime.now(timezone.utc)
            response = await call_next(request)
            duration = (datetime.now(timezone.utc) - start).total_seconds()
            response.headers["X-Process-Time"] = f"{duration:.4f}"
            return response
    
    def _setup_routes(self):
        """Setup API routes."""
        
        # =====================================================================
        # HEALTH ENDPOINTS (Compatible with existing API)
        # =====================================================================
        
        @self.app.get("/")
        async def root():
            """Root endpoint."""
            return {
                "service": "HOPE Core",
                "version": "2.0.0",
                "mode": self.core.config.mode,
                "status": "running" if self.core._running else "stopped",
            }
        
        @self.app.get("/status")
        async def get_status():
            """Get trading status (compatible with autotrader.py)."""
            health = await self.core.get_health()
            
            return {
                "mode": self.core.config.mode,
                "running": self.core._running,
                "state": self.core.state.value,
                "uptime_seconds": self.core.uptime,
                "stats": self.core._stats,
                "open_positions": len(self.core._open_positions),
                "positions": list(self.core._open_positions.values()),
                "circuit_breaker": {
                    "state": self.core.command_bus.circuit_state.value,
                    "failures": self.core.command_bus._circuit_breaker._failure_count,
                },
                "eye_of_god": self.core.eye_of_god is not None,
                "executor": self.core.order_executor is not None,
            }
        
        @self.app.get("/api/secret-sauce")
        async def get_secret_sauce_status():
            """Get Secret Sauce advanced features status."""
            if hasattr(self.core, 'secret_sauce') and self.core.secret_sauce:
                return self.core.secret_sauce.get_status()
            return {"error": "Secret Sauce not available"}
        
        
        @self.app.post("/api/secret-sauce/reset/panic")
        async def reset_sauce_panic():
            """Reset Secret Sauce panic mode."""
            if hasattr(self.core, 'secret_sauce') and self.core.secret_sauce:
                self.core.secret_sauce.panic.reset_panic()
                self.core.secret_sauce.panic.reset_daily()
                return {"success": True, "message": "Panic mode reset"}
            return {"success": False, "error": "Secret Sauce not available"}
        
        @self.app.post("/api/secret-sauce/reset/threshold")
        async def reset_sauce_threshold():
            """Reset adaptive threshold to default (35%)."""
            if hasattr(self.core, 'secret_sauce') and self.core.secret_sauce:
                self.core.secret_sauce.adaptive.threshold = 0.35
                self.core.secret_sauce.adaptive._save_state()
                return {"success": True, "message": "Threshold reset to 35%"}
            return {"success": False, "error": "Secret Sauce not available"}
        
        @self.app.post("/api/secret-sauce/reset/learning")
        async def reset_sauce_learning():
            """Reset symbol learning data (clears blacklist)."""
            if hasattr(self.core, 'secret_sauce') and self.core.secret_sauce:
                self.core.secret_sauce.learner.symbols = {}
                self.core.secret_sauce.learner._save_state()
                return {"success": True, "message": "Learning data cleared"}
            return {"success": False, "error": "Secret Sauce not available"}
        
        # Position Guardian endpoints
        @self.app.get("/api/guardian/status")
        async def get_guardian_status():
            """Get Position Guardian status."""
            if hasattr(self.core, 'position_guardian') and self.core.position_guardian:
                return self.core.position_guardian.get_status()
            return {"error": "Position Guardian not available"}
        
        @self.app.get("/api/guardian/positions")
        async def get_guardian_positions():
            """Get tracked positions."""
            if hasattr(self.core, 'position_guardian') and self.core.position_guardian:
                return self.core.position_guardian.get_status().get("positions_detail", [])
            return []
        
        @self.app.post("/api/guardian/sync")
        async def sync_guardian_positions():
            """Sync positions from Binance."""
            if hasattr(self.core, 'position_guardian') and self.core.position_guardian:
                count = await self.core.position_guardian.sync_positions_from_binance()
                return {"synced": count}
            return {"error": "Position Guardian not available"}
        
        @self.app.post("/api/guardian/close/{symbol}")
        async def close_guardian_position(symbol: str):
            """Manually close a position."""
            if hasattr(self.core, 'position_guardian') and self.core.position_guardian:
                from hope_core.guardian.position_guardian import ExitReason
                success = await self.core.position_guardian.close_position(
                    symbol.upper(),
                    ExitReason.MANUAL,
                    "Manual close via API"
                )
                return {"success": success}
            return {"error": "Position Guardian not available"}

        @self.app.post("/api/guardian/start")
        async def start_guardian():
            """Start the Position Guardian monitoring loop."""
            if hasattr(self.core, 'position_guardian') and self.core.position_guardian:
                guardian = self.core.position_guardian
                if guardian._running:
                    return {"status": "already_running", "positions": len(guardian.positions)}

                # Start in background task
                import asyncio
                asyncio.create_task(guardian.start())
                return {"status": "started", "positions": len(guardian.positions)}
            return {"error": "Position Guardian not available"}

        @self.app.post("/api/guardian/stop")
        async def stop_guardian():
            """Stop the Position Guardian monitoring loop."""
            if hasattr(self.core, 'position_guardian') and self.core.position_guardian:
                guardian = self.core.position_guardian
                guardian.stop()
                return {"status": "stopped", "positions": len(guardian.positions)}
            return {"error": "Position Guardian not available"}

        @self.app.post("/api/guardian/run-once")
        async def run_guardian_once():
            """Run one guardian monitoring cycle (for testing)."""
            if hasattr(self.core, 'position_guardian') and self.core.position_guardian:
                guardian = self.core.position_guardian
                results = await guardian.run_once()
                return {
                    "checked": results["checked"],
                    "closed": results["closed"],
                    "positions": [
                        {
                            "symbol": p["symbol"],
                            "pnl_pct": round(p["pnl_pct"], 2),
                            "decision": p["decision"],
                            "reason": p.get("reason"),
                        }
                        for p in results["positions"]
                    ],
                }
            return {"error": "Position Guardian not available"}

        @self.app.get("/api/health")
        async def get_health():
            """Health check endpoint (P0)."""
            health = await self.core.get_health()
            
            checks = {
                "eye_of_god_loaded": self.core.eye_of_god is not None,
                "executor_loaded": self.core.order_executor is not None,
                "state_machine_ok": self.core.state_manager is not None,
                "command_bus_ok": self.core.command_bus is not None,
                "journal_ok": self.core.journal is not None,
                "circuit_breaker_ok": self.core.command_bus.circuit_state.value == "CLOSED",
            }
            
            all_ok = all(checks.values())
            
            return {
                "status": "healthy" if all_ok else "degraded",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "checks": checks,
                "stats": self.core._stats,
                "open_positions": len(self.core._open_positions),
                "mode": self.core.config.mode,
                "uptime_seconds": self.core.uptime,
            }
        
        # =====================================================================
        # DASHBOARD & METRICS ENDPOINTS
        # =====================================================================
        
        @self.app.get("/dashboard")
        async def get_dashboard():
            """Serve dashboard HTML."""
            from fastapi.responses import HTMLResponse
            dashboard_path = Path(__file__).parent / "static" / "dashboard.html"
            if dashboard_path.exists():
                return HTMLResponse(content=dashboard_path.read_text())
            return HTMLResponse(content="<h1>Dashboard not found</h1>", status_code=404)
        
        @self.app.get("/metrics")
        async def get_prometheus_metrics():
            """Prometheus metrics endpoint."""
            from fastapi.responses import PlainTextResponse
            try:
                from metrics.collector import get_metrics
                metrics = get_metrics()
                
                # Update metrics from core
                metrics.positions_open.set(len(self.core._open_positions))
                metrics.daily_pnl.set(self.core._stats.get("daily_pnl", 0))
                metrics.total_pnl.set(self.core._stats.get("total_pnl", 0))
                metrics.circuit_breaker_state.set(
                    1 if self.core.command_bus.circuit_state.value == "OPEN" else 0
                )
                
                return PlainTextResponse(
                    content=metrics.get_prometheus_metrics(),
                    media_type="text/plain"
                )
            except ImportError:
                return PlainTextResponse(
                    content="# Metrics module not available",
                    media_type="text/plain"
                )
        
        @self.app.get("/api/dashboard")
        async def get_dashboard_data():
            """JSON data for dashboard."""
            try:
                from metrics.collector import get_metrics
                metrics = get_metrics()
                
                # Update and return dashboard data
                metrics.positions_open.set(len(self.core._open_positions))
                metrics.daily_pnl.set(self.core._stats.get("daily_pnl", 0))
                metrics.total_pnl.set(self.core._stats.get("total_pnl", 0))
                
                data = metrics.get_dashboard_data()
                data["mode"] = self.core.config.mode
                data["state"] = self.core.state.value
                data["positions"] = list(self.core._open_positions.values())
                
                return data
            except ImportError:
                return {
                    "error": "Metrics module not available",
                    "mode": self.core.config.mode,
                    "state": self.core.state.value,
                }
        
        # =====================================================================
        # SIGNAL ENDPOINTS
        # =====================================================================
        
        @self.app.post("/signal")
        async def submit_signal(request: SignalRequest):
            """Submit trading signal."""
            result = await self.core.submit_signal(
                symbol=request.symbol,
                score=request.confidence or request.score,
                source=request.source,
            )
            
            if result.status.value == "SUCCESS":
                return {
                    "status": "accepted",
                    "signal_id": result.data.get("signal_id"),
                    "message": f"Signal for {request.symbol} accepted",
                }
            else:
                return JSONResponse(
                    status_code=400,
                    content={
                        "status": "rejected",
                        "error": result.error or "Unknown error",
                    }
                )
        
        @self.app.post("/api/signal")
        async def submit_signal_v2(request: SignalRequest):
            """Submit trading signal (v2 endpoint)."""
            return await submit_signal(request)
        
        # =====================================================================
        # POSITION ENDPOINTS
        # =====================================================================
        
        @self.app.get("/positions")
        async def get_positions():
            """Get open positions."""
            return {
                "count": len(self.core._open_positions),
                "positions": list(self.core._open_positions.values()),
                "total_exposure": sum(
                    p.get("size_usd", 0) 
                    for p in self.core._open_positions.values()
                ),
            }
        
        @self.app.post("/positions/{position_id}/close")
        async def close_position(position_id: str, reason: str = "API"):
            """Close specific position."""
            if position_id not in self.core._open_positions:
                raise HTTPException(status_code=404, detail="Position not found")
            
            result = await self.core.command_bus.dispatch_simple(
                "CLOSE",
                {"position_id": position_id, "reason": reason},
                source="api",
            )
            
            return {"status": "closing", "position_id": position_id}
        
        # =====================================================================
        # CONTROL ENDPOINTS
        # =====================================================================
        
        @self.app.post("/emergency-stop")
        async def emergency_stop(request: EmergencyStopRequest):
            """Trigger emergency stop."""
            result = await self.core.emergency_stop(request.reason)
            return {
                "status": "stopped",
                "reason": request.reason,
                "positions_closed": request.close_positions,
            }
        
        @self.app.post("/circuit-breaker/reset")
        async def reset_circuit_breaker(request: CircuitBreakerReset = None):
            """Reset circuit breaker."""
            if request and not request.confirm:
                raise HTTPException(status_code=400, detail="Confirm required")
            
            self.core.command_bus._circuit_breaker.reset()
            
            return {
                "status": "reset",
                "new_state": self.core.command_bus.circuit_state.value,
            }
        
        @self.app.get("/circuit-breaker")
        async def get_circuit_breaker():
            """Get circuit breaker status."""
            cb = self.core.command_bus._circuit_breaker
            return {
                "state": cb.state.value,
                "failures": cb._failure_count,
                "threshold": cb._failure_threshold,
                "last_failure": cb._last_failure_time.isoformat() if cb._last_failure_time else None,
            }
        
        # =====================================================================
        # STATE MACHINE ENDPOINTS
        # =====================================================================
        
        @self.app.get("/state")
        async def get_state():
            """Get current state machine status."""
            sm = self.core.state_manager.global_machine
            return {
                "current": sm.state.value,
                "history": [
                    {"state": s, "timestamp": t.isoformat()}
                    for s, t in sm._state_history[-10:]
                ],
                "valid_transitions": sm.get_valid_transitions(),
            }
        
        # =====================================================================
        # JOURNAL ENDPOINTS
        # =====================================================================
        
        @self.app.get("/journal/recent")
        async def get_recent_events(limit: int = 50):
            """Get recent journal events."""
            events = self.core.journal.get_recent(limit)
            return {
                "count": len(events),
                "events": events,
            }
        
        @self.app.get("/journal/stats")
        async def get_journal_stats():
            """Get journal statistics."""
            return self.core.journal.get_stats()
        
        # =====================================================================
        # DASHBOARD ENDPOINT
        # =====================================================================
        
        @self.app.get("/dashboard")
        async def get_dashboard():
            """
            Full dashboard with all metrics.
            
            Returns comprehensive view for monitoring.
            """
            health = await self.core.get_health()
            
            # Calculate additional metrics
            open_positions = list(self.core._open_positions.values())
            total_exposure = sum(p.get("size_usd", 0) for p in open_positions)
            
            # Win rate (if we have closed trades)
            win_count = self.core._stats.get("win_count", 0)
            loss_count = self.core._stats.get("loss_count", 0)
            total_trades = win_count + loss_count
            win_rate = win_count / max(1, total_trades)
            
            return {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "mode": self.core.config.mode,
                "uptime_seconds": self.core.uptime,
                
                "health": {
                    "status": health.get("status"),
                    "eye_of_god": self.core.eye_of_god is not None,
                    "executor": self.core.order_executor is not None,
                    "circuit_breaker": self.core.command_bus.circuit_state.value,
                },
                
                "trading": {
                    "state": self.core.state.value,
                    "signals_received": self.core._stats.get("signals_received", 0),
                    "signals_traded": self.core._stats.get("signals_traded", 0),
                    "positions_opened": self.core._stats.get("positions_opened", 0),
                    "positions_closed": self.core._stats.get("positions_closed", 0),
                },
                
                "positions": {
                    "count": len(open_positions),
                    "total_exposure_usd": total_exposure,
                    "max_positions": self.core.config.max_positions,
                    "details": open_positions,
                },
                
                "performance": {
                    "daily_pnl": self.core._stats.get("daily_pnl", 0),
                    "total_pnl": self.core._stats.get("total_pnl", 0),
                    "win_count": win_count,
                    "loss_count": loss_count,
                    "win_rate": win_rate,
                },
                
                "command_bus": self.core.command_bus.get_stats(),
                
                "journal": {
                    "events": self.core.journal.event_count,
                },
                
                "config": {
                    "min_confidence": self.core.config.min_confidence,
                    "position_size_usd": self.core.config.position_size_usd,
                    "max_positions": self.core.config.max_positions,
                    "daily_loss_limit_percent": self.core.config.daily_loss_limit_percent,
                },
            }
        
        @self.app.get("/alerts/history")
        async def get_alert_history(limit: int = 50):
            """Get alert history."""
            if self.core.alert_manager:
                return {
                    "alerts": self.core.alert_manager.get_history(limit),
                    "stats": self.core.alert_manager.get_stats(),
                }
            return {"alerts": [], "stats": {}}
        
        # =====================================================================
        # INTEGRATION ENDPOINTS (for autotrader.py compatibility)
        # =====================================================================
        
        @self.app.post("/signal/external")
        async def receive_external_signal(request: SignalRequest):
            """
            Receive signal from external sources (MoonBot, Hunters, etc).
            Compatible with existing autotrader.py signal format.
            """
            # Normalize score to 0-1 if needed
            normalized_score = request.score / 100.0 if request.score > 1 else request.score
            result = await self.core.submit_signal(
                symbol=request.symbol,
                score=normalized_score,
                source=request.source,
            )
            
            # Return in autotrader.py compatible format
            return {
                "success": result.status.value == "SUCCESS",
                "status": result.status.value,
                "signal_id": result.data.get("signal_id") if result.data else None,
                "decision": result.data.get("auto_decide", {}).get("decision") if result.data else None,
                "position_id": result.data.get("auto_decide", {}).get("position_id") if result.data else None,
            }
        
        @self.app.get("/positions/open")
        async def get_open_positions_list():
            """Get list of open positions (autotrader.py compatible)."""
            return {
                "count": len(self.core._open_positions),
                "positions": list(self.core._open_positions.values()),
                "total_exposure_usd": sum(
                    p.get("size_usd", 0) for p in self.core._open_positions.values()
                ),
            }
        
        @self.app.post("/positions/{position_id}/close")
        async def close_position_by_id(position_id: str, reason: str = "API"):
            """Close specific position."""
            from bus.command_bus import Command, CommandType
            from datetime import datetime, timezone
            
            if position_id not in self.core._open_positions:
                return {"success": False, "error": "Position not found"}
            
            result = await self.core.command_bus.dispatch(
                Command(
                    id=f"close_{position_id}",
                    type=CommandType.CLOSE,
                    payload={"position_id": position_id, "reason": reason},
                    timestamp=datetime.now(timezone.utc),
                    source="API",
                )
            )
            
            return {
                "success": result.status.value == "SUCCESS",
                "status": result.status.value,
                "data": result.data,
            }
        
        @self.app.get("/stats/trading")
        async def get_trading_stats():
            """Get trading statistics."""
            return {
                "signals_received": self.core._stats.get("signals_received", 0),
                "signals_traded": self.core._stats.get("signals_traded", 0),
                "positions_opened": self.core._stats.get("positions_opened", 0),
                "positions_closed": self.core._stats.get("positions_closed", 0),
                "daily_pnl": self.core._stats.get("daily_pnl", 0),
                "total_pnl": self.core._stats.get("total_pnl", 0),
                "win_count": self.core._stats.get("win_count", 0),
                "loss_count": self.core._stats.get("loss_count", 0),
            }
        
        @self.app.get("/mode")
        async def get_trading_mode():
            """Get current trading mode."""
            return {
                "mode": self.core.config.mode,
                "eye_of_god_enabled": self.core.config.eye_of_god_enabled,
                "binance_enabled": self.core.config.binance_enabled,
            }
        
        # =====================================================================
        # GUARDIAN ENDPOINTS (for Guardian process)
        # =====================================================================
        
        @self.app.get("/guardian/heartbeat")
        async def guardian_heartbeat():
            """Heartbeat endpoint for Guardian."""
            import psutil
            process = psutil.Process()
            
            return {
                "status": "alive",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "state": self.core.state.value,
                "running": self.core._running,
                "memory_mb": process.memory_info().rss / (1024 * 1024),
                "cpu_percent": process.cpu_percent(),
                "uptime_seconds": self.core.uptime,
            }
        
        @self.app.post("/guardian/restart-signal")
        async def guardian_restart_signal():
            """Signal from Guardian that restart is imminent."""
            # Graceful shutdown preparation
            self.core._running = False
            return {"status": "acknowledged", "action": "preparing_shutdown"}
    
    async def run(self):
        """Run the API server."""
        config = uvicorn.Config(
            self.app,
            host=self.host,
            port=self.port,
            log_level="info",
            access_log=True,
        )
        server = uvicorn.Server(config)
        await server.serve()


# =============================================================================
# MAIN
# =============================================================================

async def main():
    """Main entry point."""
    import argparse
    
    parser = argparse.ArgumentParser(description="HOPE Core API Server")
    parser.add_argument("--mode", choices=["DRY", "TESTNET", "LIVE"], default="DRY")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8200)
    parser.add_argument("--config", type=str, help="Config file path")
    args = parser.parse_args()
    
    # Create config
    config = HopeCoreConfig(
        mode=args.mode,
        api_host=args.host,
        api_port=args.port,
    )
    
    if args.config:
        config = HopeCoreConfig.from_file(Path(args.config))
        config.mode = args.mode
    
    # Create core
    core = HopeCore(config)
    
    # Create and run server
    server = HopeCoreAPIServer(core, args.host, args.port)
    
    # Start core in background
    async def run_core():
        await core.start()
    
    # Run both
    await asyncio.gather(
        server.run(),
        run_core(),
    )


if __name__ == "__main__":
    asyncio.run(main())
