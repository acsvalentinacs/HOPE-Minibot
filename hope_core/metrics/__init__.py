# === AI SIGNATURE ===
# Module: hope_core/metrics/__init__.py
# Created by: Claude (opus-4.5)
# === END SIGNATURE ===
"""HOPE Core Metrics Module."""

from .collector import (
    MetricsCollector,
    Counter,
    Gauge,
    Histogram,
    get_metrics,
)

__all__ = [
    "MetricsCollector",
    "Counter",
    "Gauge", 
    "Histogram",
    "get_metrics",
]
