# -*- coding: utf-8 -*-
# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-29 03:40:00 UTC
# Modified by: Claude (opus-4)
# Modified at: 2026-01-29 10:15:00 UTC
# Purpose: AI-Gateway modules package
# === END SIGNATURE ===
"""
AI-Gateway Modules: Intelligence components.

Each module runs independently and writes artifacts for Core consumption.
"""

from typing import Dict, Type

# Module registry (lazy import to avoid circular deps)
MODULE_REGISTRY: Dict[str, str] = {
    "sentiment": "ai_gateway.modules.sentiment.analyzer.SentimentAnalyzer",
    "regime": "ai_gateway.modules.regime.detector.RegimeDetector",
    "doctor": "ai_gateway.modules.doctor.diagnostics.StrategyDoctor",
    "anomaly": "ai_gateway.modules.anomaly.scanner.AnomalyScanner",
    "self_improver": "ai_gateway.modules.self_improver.loop.SelfImprovingLoop",
}


def get_module_class(module_name: str):
    """Dynamically import and return module class."""
    if module_name not in MODULE_REGISTRY:
        raise ValueError(f"Unknown module: {module_name}")

    module_path = MODULE_REGISTRY[module_name]
    parts = module_path.rsplit(".", 1)
    module_import = parts[0]
    class_name = parts[1]

    import importlib
    mod = importlib.import_module(module_import)
    return getattr(mod, class_name)
