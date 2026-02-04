# === AI SIGNATURE ===
# Module: hope_core/autotrader_adapter.py
# Created by: Claude (opus-4.5)
# Purpose: Adapter to make HOPE Core compatible with existing autotrader.py
# === END SIGNATURE ===
"""
Autotrader Adapter

Provides compatibility layer between HOPE Core v2.0 and existing autotrader.py.
Can be used as a drop-in replacement or as a wrapper.

Usage:
    # In autotrader.py, replace:
    from eye_of_god_v3 import EyeOfGodV3
    
    # With:
    from hope_core.autotrader_adapter import HopeCoreAdapter as EyeOfGodV3
"""

import asyncio
from typing import Dict, Any, Optional, Callable
from datetime import datetime, timezone
import threading


class HopeCoreAdapter:
    """
    Adapter that makes HOPE Core look like EyeOfGodV3 for autotrader.py.
    
    This allows gradual migration from autotrader.py to HOPE Core.
    """
    
    def __init__(
        self,
        mode: str = "DRY",
        min_confidence: float = 0.50,
        position_size_usd: float = 20.0,
        max_positions: int = 3,
    ):
        """
        Initialize adapter.
        
        Args:
            mode: Trading mode (DRY/TESTNET/LIVE)
            min_confidence: Minimum confidence threshold
            position_size_usd: Position size in USD
            max_positions: Maximum concurrent positions
        """
        self._mode = mode
        self._min_confidence = min_confidence
        self._position_size_usd = position_size_usd
        self._max_positions = max_positions
        
        self._core = None
        self._loop = None
        self._thread = None
        self._initialized = False
        
        # For compatibility with EyeOfGodV3 interface
        self.MIN_CONFIDENCE_TO_TRADE = min_confidence
        self.MIN_CONFIDENCE_AI_OVERRIDE = 0.35
        
        self._init_core()
    
    def _init_core(self):
        """Initialize HOPE Core in background thread."""
        try:
            from hope_core import HopeCore, HopeCoreConfig
            
            config = HopeCoreConfig(
                mode=self._mode,
                min_confidence=self._min_confidence,
                position_size_usd=self._position_size_usd,
                max_positions=self._max_positions,
            )
            
            self._core = HopeCore(config)
            self._core._running = True
            self._initialized = True
            
            print(f"[ADAPTER] HOPE Core initialized in {self._mode} mode")
            
        except ImportError as e:
            print(f"[ADAPTER] Failed to import HOPE Core: {e}")
            self._initialized = False
    
    def evaluate_signal(
        self,
        symbol: str,
        score: float,
        source: str = "SCANNER",
        market_data: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Evaluate signal (EyeOfGodV3 compatible interface).
        
        Args:
            symbol: Trading symbol
            score: Signal score (0-1)
            source: Signal source
            market_data: Optional market data
            
        Returns:
            Decision dict with action, confidence, reasons
        """
        if not self._initialized:
            return {
                "action": "HOLD",
                "confidence": 0,
                "reasons": ["HOPE Core not initialized"],
            }
        
        # Use asyncio to call core
        try:
            loop = asyncio.new_event_loop()
            result = loop.run_until_complete(
                self._core.submit_signal(symbol, score, source)
            )
            loop.close()
            
            if result.status.value == "SUCCESS" and result.data:
                auto_decide = result.data.get("auto_decide", {})
                decision = auto_decide.get("decision", "HOLD")
                confidence = auto_decide.get("confidence", score)
                
                return {
                    "action": decision,
                    "confidence": confidence,
                    "reasons": auto_decide.get("reasons", []),
                    "position_id": auto_decide.get("position_id"),
                    "signal_id": result.data.get("signal_id"),
                }
            else:
                return {
                    "action": "HOLD",
                    "confidence": score,
                    "reasons": [f"Signal status: {result.status.value}"],
                }
                
        except Exception as e:
            return {
                "action": "HOLD",
                "confidence": 0,
                "reasons": [f"Error: {e}"],
            }
    
    def decide(self, signal_data: Dict[str, Any]) -> Dict[str, Any]:
        """Alias for evaluate_signal (EyeOfGodV3 compatibility)."""
        return self.evaluate_signal(
            symbol=signal_data.get("symbol", "UNKNOWN"),
            score=signal_data.get("score", 0),
            source=signal_data.get("source", "SCANNER"),
            market_data=signal_data.get("market_data"),
        )
    
    def get_open_positions(self) -> list:
        """Get list of open positions."""
        if not self._initialized:
            return []
        return list(self._core._open_positions.values())
    
    def get_position_count(self) -> int:
        """Get count of open positions."""
        if not self._initialized:
            return 0
        return len(self._core._open_positions)
    
    def get_health(self) -> Dict[str, Any]:
        """Get health status."""
        if not self._initialized:
            return {"status": "unhealthy", "reason": "Not initialized"}
        
        try:
            loop = asyncio.new_event_loop()
            health = loop.run_until_complete(self._core.get_health())
            loop.close()
            return health
        except Exception as e:
            return {"status": "error", "reason": str(e)}
    
    def emergency_stop(self, reason: str = "Manual stop"):
        """Trigger emergency stop."""
        if self._initialized:
            self._core.stop()
            print(f"[ADAPTER] Emergency stop: {reason}")


class SignalForwarder:
    """
    Forwards signals from existing sources to HOPE Core API.
    
    Can be used with hunters_listener, tg_bot_simple, etc.
    """
    
    def __init__(
        self,
        api_url: str = "http://127.0.0.1:8200",
    ):
        """
        Initialize forwarder.
        
        Args:
            api_url: HOPE Core API URL
        """
        self._api_url = api_url.rstrip("/")
        self._session = None
    
    async def forward_signal(
        self,
        symbol: str,
        score: float,
        source: str,
    ) -> Dict[str, Any]:
        """
        Forward signal to HOPE Core API.
        
        Args:
            symbol: Trading symbol
            score: Signal score
            source: Signal source
            
        Returns:
            API response
        """
        try:
            import aiohttp
            
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{self._api_url}/signal/external",
                    json={"symbol": symbol, "score": score, "source": source},
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    if resp.status == 200:
                        return await resp.json()
                    else:
                        return {"success": False, "error": f"HTTP {resp.status}"}
                        
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    def forward_signal_sync(
        self,
        symbol: str,
        score: float,
        source: str,
    ) -> Dict[str, Any]:
        """Synchronous version of forward_signal."""
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(
                self.forward_signal(symbol, score, source)
            )
        finally:
            loop.close()


# =============================================================================
# HELPER FUNCTIONS
# =============================================================================

def create_adapter(
    mode: str = "DRY",
    min_confidence: float = 0.50,
) -> HopeCoreAdapter:
    """Create and return HopeCoreAdapter instance."""
    return HopeCoreAdapter(mode=mode, min_confidence=min_confidence)


def create_forwarder(
    api_url: str = "http://127.0.0.1:8200",
) -> SignalForwarder:
    """Create and return SignalForwarder instance."""
    return SignalForwarder(api_url=api_url)
