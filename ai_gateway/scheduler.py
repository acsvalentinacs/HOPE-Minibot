# -*- coding: utf-8 -*-
# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-29 16:05:00 UTC
# Purpose: Module scheduler for AI-Gateway lifecycle management
# === END SIGNATURE ===
"""
AI-Gateway Scheduler: Centralized lifecycle management for AI modules.

Responsibilities:
- Start/stop modules
- Track module states
- Provide control API for Telegram/HTTP
- Auto-restart on errors (optional)

INVARIANT: Single scheduler instance per gateway.
INVARIANT: All module operations go through scheduler.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from threading import Lock
from typing import Any, Callable, Dict, List, Optional, Type

from .base_module import BaseAIModule, ModuleConfig, ModuleState
from .status_manager import get_status_manager

logger = logging.getLogger(__name__)


# Module intervals (seconds)
MODULE_INTERVALS = {
    "sentiment": 900.0,   # 15 min - RSS + Claude API
    "regime": 300.0,      # 5 min - Binance klines
    "doctor": 3600.0,     # 1 hour - on-demand mainly
    "anomaly": 60.0,      # 1 min - fast scan
}


@dataclass
class SchedulerConfig:
    """Scheduler configuration."""
    auto_start_enabled: bool = False  # Start enabled modules on init
    auto_restart_on_error: bool = False
    restart_delay_seconds: float = 30.0
    max_restart_attempts: int = 3


class ModuleScheduler:
    """
    Centralized scheduler for AI modules.

    Usage:
        scheduler = ModuleScheduler.get_instance()
        await scheduler.start_module("sentiment")
        await scheduler.stop_module("sentiment")
        await scheduler.run_module_now("sentiment")
    """

    _instance: Optional["ModuleScheduler"] = None
    _lock = Lock()

    @classmethod
    def get_instance(cls) -> "ModuleScheduler":
        """Get or create singleton instance."""
        with cls._lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        """Reset singleton (for testing)."""
        with cls._lock:
            cls._instance = None

    def __init__(self, config: Optional[SchedulerConfig] = None):
        self.config = config or SchedulerConfig()
        self._modules: Dict[str, BaseAIModule] = {}
        self._module_factories: Dict[str, Callable[[], BaseAIModule]] = {}
        self._restart_counts: Dict[str, int] = {}
        self._status_manager = get_status_manager()
        self._started = False

        # Register default module factories
        self._register_default_factories()

    def _register_default_factories(self) -> None:
        """Register factory functions for each module."""
        # Lazy imports to avoid circular dependencies

        def create_sentiment():
            from .modules.sentiment import SentimentAnalyzer
            config = ModuleConfig(
                module_id="sentiment",
                interval_seconds=MODULE_INTERVALS["sentiment"],
                enabled=self._status_manager.is_enabled("sentiment"),
            )
            return SentimentModuleAdapter(config)

        def create_regime():
            from .modules.regime import RegimeDetector
            config = ModuleConfig(
                module_id="regime",
                interval_seconds=MODULE_INTERVALS["regime"],
                enabled=self._status_manager.is_enabled("regime"),
            )
            return RegimeModuleAdapter(config)

        def create_doctor():
            from .modules.doctor import StrategyDoctor
            config = ModuleConfig(
                module_id="doctor",
                interval_seconds=MODULE_INTERVALS["doctor"],
                enabled=self._status_manager.is_enabled("doctor"),
            )
            return DoctorModuleAdapter(config)

        def create_anomaly():
            from .modules.anomaly import AnomalyScanner
            config = ModuleConfig(
                module_id="anomaly",
                interval_seconds=MODULE_INTERVALS["anomaly"],
                enabled=self._status_manager.is_enabled("anomaly"),
            )
            return AnomalyModuleAdapter(config)

        self._module_factories = {
            "sentiment": create_sentiment,
            "regime": create_regime,
            "doctor": create_doctor,
            "anomaly": create_anomaly,
        }

    def _get_or_create_module(self, module_id: str) -> Optional[BaseAIModule]:
        """Get existing module or create new one."""
        if module_id in self._modules:
            return self._modules[module_id]

        factory = self._module_factories.get(module_id)
        if factory is None:
            logger.error(f"No factory for module: {module_id}")
            return None

        try:
            module = factory()
            self._modules[module_id] = module
            return module
        except Exception as e:
            logger.error(f"Failed to create module {module_id}: {e}")
            return None

    # === Module Control ===

    async def start_module(self, module_id: str) -> bool:
        """
        Start a specific module.

        Returns:
            True if started, False on error.
        """
        if not self._status_manager.is_enabled(module_id):
            logger.warning(f"Cannot start disabled module: {module_id}")
            return False

        module = self._get_or_create_module(module_id)
        if module is None:
            return False

        if module.is_running:
            logger.info(f"Module {module_id} already running")
            return True

        success = await module.start()
        if success:
            self._restart_counts[module_id] = 0
            logger.info(f"Module {module_id} started successfully")
        else:
            logger.error(f"Module {module_id} failed to start")

        return success

    async def stop_module(self, module_id: str, timeout: float = 10.0) -> bool:
        """
        Stop a specific module.

        Returns:
            True if stopped, False on error.
        """
        module = self._modules.get(module_id)
        if module is None:
            logger.warning(f"Module {module_id} not found")
            return True  # Not running = success

        success = await module.stop(timeout=timeout)
        if success:
            logger.info(f"Module {module_id} stopped")

        return success

    async def restart_module(self, module_id: str) -> bool:
        """Restart a module."""
        module = self._modules.get(module_id)
        if module is None:
            return await self.start_module(module_id)

        return await module.restart()

    async def run_module_now(self, module_id: str) -> Optional[Dict[str, Any]]:
        """
        Execute module immediately (one iteration).

        Returns:
            Artifact dict if produced, None otherwise.
        """
        module = self._modules.get(module_id)
        if module is None:
            module = self._get_or_create_module(module_id)
            if module is None:
                return None

        if not module.is_running:
            # Start temporarily for single run
            if not await module.start():
                return None

        artifact = await module.run_now()
        return artifact.dict() if artifact else None

    def enable_module(self, module_id: str) -> bool:
        """Enable a module (allows starting)."""
        return self._status_manager.enable_module(module_id)

    def disable_module(self, module_id: str) -> bool:
        """Disable a module (and stop if running)."""
        if not self._status_manager.disable_module(module_id):
            return False

        # Stop if running (fire-and-forget)
        module = self._modules.get(module_id)
        if module and module.is_running:
            asyncio.create_task(self.stop_module(module_id))

        return True

    # === Bulk Operations ===

    async def start_all_enabled(self) -> Dict[str, bool]:
        """Start all enabled modules."""
        results = {}
        for module_id in self._module_factories.keys():
            if self._status_manager.is_enabled(module_id):
                results[module_id] = await self.start_module(module_id)
            else:
                results[module_id] = False
        return results

    async def stop_all(self, timeout: float = 10.0) -> Dict[str, bool]:
        """Stop all running modules."""
        results = {}
        for module_id in list(self._modules.keys()):
            results[module_id] = await self.stop_module(module_id, timeout)
        return results

    # === Status ===

    def get_module_info(self, module_id: str) -> Optional[Dict[str, Any]]:
        """Get module info."""
        module = self._modules.get(module_id)
        if module is None:
            return {
                "module_id": module_id,
                "state": "not_initialized",
                "is_running": False,
                "enabled": self._status_manager.is_enabled(module_id),
            }
        return module.get_info()

    def get_all_modules_info(self) -> Dict[str, Dict[str, Any]]:
        """Get info for all modules."""
        result = {}
        for module_id in self._module_factories.keys():
            result[module_id] = self.get_module_info(module_id)
        return result

    def get_running_modules(self) -> List[str]:
        """Get list of running module IDs."""
        return [
            module_id for module_id, module in self._modules.items()
            if module.is_running
        ]


# === Module Adapters ===
# Wrap existing module classes to implement BaseAIModule interface

class SentimentModuleAdapter(BaseAIModule):
    """Adapter for SentimentAnalyzer."""

    def __init__(self, config: ModuleConfig):
        super().__init__(config)
        self._analyzer = None

    async def on_start(self) -> None:
        from .modules.sentiment import SentimentAnalyzer
        self._analyzer = SentimentAnalyzer()

    async def run_once(self):
        if self._analyzer is None:
            raise RuntimeError("Analyzer not initialized")

        # Fetch news and run analysis
        from .modules.sentiment.analyzer import RSSFetcher
        fetcher = RSSFetcher()
        news = await fetcher.fetch_all()

        if not news:
            logger.debug("No news fetched, skipping sentiment analysis")
            return None

        # Extract headlines
        headlines = [item["title"] for item in news[:10]]

        # Run analysis (default symbol)
        artifact = await self._analyzer.analyze(
            symbol="BTCUSDT",
            news_headlines=headlines,
        )

        return artifact


class RegimeModuleAdapter(BaseAIModule):
    """Adapter for RegimeDetector."""

    def __init__(self, config: ModuleConfig):
        super().__init__(config)
        self._detector = None

    async def on_start(self) -> None:
        from .modules.regime import RegimeDetector
        self._detector = RegimeDetector()

    async def run_once(self):
        if self._detector is None:
            raise RuntimeError("Detector not initialized")

        # Fetch candles from Binance
        import httpx
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(
                "https://api.binance.com/api/v3/klines",
                params={"symbol": "BTCUSDT", "interval": "4h", "limit": 100}
            )
            resp.raise_for_status()
            raw_klines = resp.json()

        from .modules.regime.detector import OHLCV
        candles = [
            OHLCV(
                timestamp=int(k[0]),
                open=float(k[1]),
                high=float(k[2]),
                low=float(k[3]),
                close=float(k[4]),
                volume=float(k[5]),
            )
            for k in raw_klines
        ]

        artifact = self._detector.detect("BTCUSDT", candles, "4h")
        return artifact


class DoctorModuleAdapter(BaseAIModule):
    """Adapter for StrategyDoctor."""

    def __init__(self, config: ModuleConfig):
        super().__init__(config)
        self._doctor = None

    async def on_start(self) -> None:
        from .modules.doctor import StrategyDoctor
        self._doctor = StrategyDoctor()

    async def run_once(self):
        if self._doctor is None:
            raise RuntimeError("Doctor not initialized")

        # Doctor is on-demand, just mark healthy
        # Real diagnosis requires trade data
        logger.debug("Doctor module tick - awaiting trades")
        return None


class AnomalyModuleAdapter(BaseAIModule):
    """Adapter for AnomalyScanner."""

    def __init__(self, config: ModuleConfig):
        super().__init__(config)
        self._scanner = None

    async def on_start(self) -> None:
        from .modules.anomaly import AnomalyScanner
        self._scanner = AnomalyScanner()

    async def run_once(self):
        if self._scanner is None:
            raise RuntimeError("Scanner not initialized")

        # Fetch tickers from Binance
        import httpx
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(
                "https://api.binance.com/api/v3/ticker/24hr"
            )
            resp.raise_for_status()
            tickers = resp.json()

        # Filter USDT pairs
        usdt_tickers = [t for t in tickers if t.get("symbol", "").endswith("USDT")][:50]

        artifact = self._scanner.scan(usdt_tickers)
        return artifact


# === Convenience ===

def get_scheduler() -> ModuleScheduler:
    """Get the global scheduler instance."""
    return ModuleScheduler.get_instance()
