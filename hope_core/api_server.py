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
        score: float = Field(..., ge=0, le=1)
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
