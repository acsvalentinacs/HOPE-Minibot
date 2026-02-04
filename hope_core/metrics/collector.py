# === AI SIGNATURE ===
# Module: hope_core/metrics/collector.py
# Created by: Claude (opus-4.5)
# Created at: 2026-02-04 11:40:00 UTC
# Purpose: Metrics collection for HOPE Core
# === END SIGNATURE ===
"""
Metrics Collector

Collects and exposes metrics in Prometheus format.
Also provides real-time metrics for dashboard.
"""

from typing import Dict, Any, List, Optional
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from collections import deque
import threading
import time


@dataclass
class MetricPoint:
    """Single metric data point."""
    timestamp: datetime
    value: float
    labels: Dict[str, str] = field(default_factory=dict)


class Counter:
    """Monotonically increasing counter."""
    
    def __init__(self, name: str, description: str, labels: List[str] = None):
        self.name = name
        self.description = description
        self.labels = labels or []
        self._values: Dict[tuple, float] = {}
        self._lock = threading.Lock()
    
    def inc(self, value: float = 1, **label_values):
        """Increment counter."""
        key = tuple(label_values.get(l, "") for l in self.labels)
        with self._lock:
            self._values[key] = self._values.get(key, 0) + value
    
    def get(self, **label_values) -> float:
        """Get counter value."""
        key = tuple(label_values.get(l, "") for l in self.labels)
        return self._values.get(key, 0)
    
    def to_prometheus(self) -> str:
        """Export to Prometheus format."""
        lines = [
            f"# HELP {self.name} {self.description}",
            f"# TYPE {self.name} counter",
        ]
        for key, value in self._values.items():
            if self.labels:
                label_str = ",".join(f'{l}="{v}"' for l, v in zip(self.labels, key))
                lines.append(f"{self.name}{{{label_str}}} {value}")
            else:
                lines.append(f"{self.name} {value}")
        return "\n".join(lines)


class Gauge:
    """Value that can go up and down."""
    
    def __init__(self, name: str, description: str, labels: List[str] = None):
        self.name = name
        self.description = description
        self.labels = labels or []
        self._values: Dict[tuple, float] = {}
        self._lock = threading.Lock()
    
    def set(self, value: float, **label_values):
        """Set gauge value."""
        key = tuple(label_values.get(l, "") for l in self.labels)
        with self._lock:
            self._values[key] = value
    
    def inc(self, value: float = 1, **label_values):
        """Increment gauge."""
        key = tuple(label_values.get(l, "") for l in self.labels)
        with self._lock:
            self._values[key] = self._values.get(key, 0) + value
    
    def dec(self, value: float = 1, **label_values):
        """Decrement gauge."""
        self.inc(-value, **label_values)
    
    def get(self, **label_values) -> float:
        """Get gauge value."""
        key = tuple(label_values.get(l, "") for l in self.labels)
        return self._values.get(key, 0)
    
    def to_prometheus(self) -> str:
        """Export to Prometheus format."""
        lines = [
            f"# HELP {self.name} {self.description}",
            f"# TYPE {self.name} gauge",
        ]
        for key, value in self._values.items():
            if self.labels:
                label_str = ",".join(f'{l}="{v}"' for l, v in zip(self.labels, key))
                lines.append(f"{self.name}{{{label_str}}} {value}")
            else:
                lines.append(f"{self.name} {value}")
        return "\n".join(lines)


class Histogram:
    """Distribution of values."""
    
    BUCKETS = [0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10]
    
    def __init__(self, name: str, description: str, buckets: List[float] = None):
        self.name = name
        self.description = description
        self.buckets = buckets or self.BUCKETS
        self._counts: Dict[float, int] = {b: 0 for b in self.buckets}
        self._counts[float('inf')] = 0
        self._sum = 0
        self._count = 0
        self._lock = threading.Lock()
    
    def observe(self, value: float):
        """Observe a value."""
        with self._lock:
            self._sum += value
            self._count += 1
            for bucket in self.buckets:
                if value <= bucket:
                    self._counts[bucket] += 1
            self._counts[float('inf')] += 1
    
    def to_prometheus(self) -> str:
        """Export to Prometheus format."""
        lines = [
            f"# HELP {self.name} {self.description}",
            f"# TYPE {self.name} histogram",
        ]
        for bucket, count in self._counts.items():
            le = "+Inf" if bucket == float('inf') else str(bucket)
            lines.append(f'{self.name}_bucket{{le="{le}"}} {count}')
        lines.append(f"{self.name}_sum {self._sum}")
        lines.append(f"{self.name}_count {self._count}")
        return "\n".join(lines)


class MetricsCollector:
    """
    Central metrics collector for HOPE Core.
    
    Collects:
    - Trading metrics (signals, trades, PnL)
    - System metrics (uptime, memory, latency)
    - Command bus metrics (commands, errors)
    """
    
    def __init__(self):
        """Initialize metrics collector."""
        self._lock = threading.Lock()
        self._start_time = datetime.now(timezone.utc)
        
        # Trading metrics
        self.signals_received = Counter(
            "hope_signals_total",
            "Total signals received",
            ["source"]
        )
        self.trades_executed = Counter(
            "hope_trades_total",
            "Total trades executed",
            ["symbol", "side", "result"]
        )
        self.positions_open = Gauge(
            "hope_positions_open",
            "Number of open positions"
        )
        self.daily_pnl = Gauge(
            "hope_daily_pnl_usd",
            "Daily PnL in USD"
        )
        self.total_pnl = Gauge(
            "hope_total_pnl_usd",
            "Total PnL in USD"
        )
        
        # System metrics
        self.uptime_seconds = Gauge(
            "hope_uptime_seconds",
            "System uptime in seconds"
        )
        self.memory_bytes = Gauge(
            "hope_memory_bytes",
            "Memory usage in bytes"
        )
        self.cpu_percent = Gauge(
            "hope_cpu_percent",
            "CPU usage percentage"
        )
        
        # Command bus metrics
        self.commands_total = Counter(
            "hope_commands_total",
            "Total commands processed",
            ["type", "status"]
        )
        self.command_duration = Histogram(
            "hope_command_duration_seconds",
            "Command execution duration"
        )
        
        # Circuit breaker
        self.circuit_breaker_state = Gauge(
            "hope_circuit_breaker_state",
            "Circuit breaker state (0=closed, 1=open)"
        )
        
        # Time series for dashboard
        self._pnl_history: deque = deque(maxlen=1440)  # 24h of minute data
        self._signal_history: deque = deque(maxlen=100)
    
    def record_signal(self, source: str):
        """Record signal received."""
        self.signals_received.inc(source=source)
        self._signal_history.append({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "source": source,
        })
    
    def record_trade(self, symbol: str, side: str, result: str, pnl: float = 0):
        """Record trade execution."""
        self.trades_executed.inc(symbol=symbol, side=side, result=result)
        if result == "SUCCESS":
            self.daily_pnl.inc(pnl)
            self.total_pnl.inc(pnl)
    
    def record_command(self, cmd_type: str, status: str, duration_sec: float):
        """Record command execution."""
        self.commands_total.inc(type=cmd_type, status=status)
        self.command_duration.observe(duration_sec)
    
    def update_pnl_history(self, pnl: float):
        """Update PnL history for chart."""
        self._pnl_history.append({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "pnl": pnl,
        })
    
    def update_system_metrics(self):
        """Update system metrics."""
        import psutil
        
        process = psutil.Process()
        self.memory_bytes.set(process.memory_info().rss)
        self.cpu_percent.set(process.cpu_percent())
        self.uptime_seconds.set(
            (datetime.now(timezone.utc) - self._start_time).total_seconds()
        )
    
    def get_prometheus_metrics(self) -> str:
        """Get all metrics in Prometheus format."""
        self.update_system_metrics()
        
        metrics = [
            self.signals_received.to_prometheus(),
            self.trades_executed.to_prometheus(),
            self.positions_open.to_prometheus(),
            self.daily_pnl.to_prometheus(),
            self.total_pnl.to_prometheus(),
            self.uptime_seconds.to_prometheus(),
            self.memory_bytes.to_prometheus(),
            self.cpu_percent.to_prometheus(),
            self.commands_total.to_prometheus(),
            self.command_duration.to_prometheus(),
            self.circuit_breaker_state.to_prometheus(),
        ]
        
        return "\n\n".join(metrics)
    
    def get_dashboard_data(self) -> Dict[str, Any]:
        """Get data for dashboard."""
        self.update_system_metrics()
        
        return {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "uptime_seconds": self.uptime_seconds.get(),
            "memory_mb": self.memory_bytes.get() / (1024 * 1024),
            "cpu_percent": self.cpu_percent.get(),
            "signals_total": sum(self.signals_received._values.values()),
            "trades_total": sum(self.trades_executed._values.values()),
            "positions_open": self.positions_open.get(),
            "daily_pnl": self.daily_pnl.get(),
            "total_pnl": self.total_pnl.get(),
            "pnl_history": list(self._pnl_history),
            "recent_signals": list(self._signal_history)[-10:],
            "circuit_breaker": "OPEN" if self.circuit_breaker_state.get() else "CLOSED",
        }


# =============================================================================
# SINGLETON
# =============================================================================

_metrics: Optional[MetricsCollector] = None


def get_metrics() -> MetricsCollector:
    """Get singleton metrics collector."""
    global _metrics
    if _metrics is None:
        _metrics = MetricsCollector()
    return _metrics


# =============================================================================
# TESTING
# =============================================================================

if __name__ == "__main__":
    print("=== Metrics Collector Tests ===\n")
    
    metrics = MetricsCollector()
    
    # Test counter
    metrics.signals_received.inc(source="SCANNER")
    metrics.signals_received.inc(source="SCANNER")
    metrics.signals_received.inc(source="EXTERNAL")
    print(f"Signals from SCANNER: {metrics.signals_received.get(source='SCANNER')}")
    print(f"Signals from EXTERNAL: {metrics.signals_received.get(source='EXTERNAL')}")
    
    # Test gauge
    metrics.daily_pnl.set(100.50)
    metrics.daily_pnl.inc(25.25)
    print(f"Daily PnL: ${metrics.daily_pnl.get():.2f}")
    
    # Test histogram
    for duration in [0.01, 0.05, 0.1, 0.5, 1.0]:
        metrics.command_duration.observe(duration)
    
    # Test Prometheus output
    print("\n=== Prometheus Metrics ===")
    print(metrics.get_prometheus_metrics()[:500] + "...")
    
    # Test dashboard data
    print("\n=== Dashboard Data ===")
    data = metrics.get_dashboard_data()
    for key, value in data.items():
        if not isinstance(value, list):
            print(f"  {key}: {value}")
    
    print("\n✅ Tests Completed")
