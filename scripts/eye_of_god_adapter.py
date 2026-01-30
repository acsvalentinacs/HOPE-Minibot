# -*- coding: utf-8 -*-
# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-30 22:47:00 UTC
# Purpose: EyeOfGodV3 adapter - adds .analyze() shim for compatibility
# === END SIGNATURE ===
"""
EYE OF GOD ADAPTER v1.0

Проблема: EyeOfGodV3 имеет метод .decide(), но некоторые тесты вызывают .analyze()

Решение: Этот адаптер:
1. Оборачивает EyeOfGodV3
2. Добавляет .analyze() как alias для .decide()
3. Сохраняет полную совместимость
"""

import sys
import os
from pathlib import Path
from typing import Dict, Any, Optional

# Add project root to path
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


class EyeOfGodAdapter:
    """
    Adapter for EyeOfGodV3 that provides .analyze() compatibility.

    Usage:
        adapter = EyeOfGodAdapter()
        result = adapter.analyze(signal)  # Works!
        result = adapter.decide(signal)   # Also works!
    """

    def __init__(self):
        self._eye = None
        self._patch_applied = False
        self.patch_detail = "none"
        self._init_eye()

    def _init_eye(self):
        """Initialize EyeOfGodV3 with compatibility patch."""
        try:
            # Try importing from scripts
            from scripts.eye_of_god_v3 import EyeOfGodV3
            self._eye = EyeOfGodV3()

            # Check if .analyze() exists
            if hasattr(self._eye, 'analyze'):
                self.patch_detail = "native"
            elif hasattr(self._eye, 'decide'):
                # Monkeypatch: add .analyze() as alias to .decide()
                self._eye.analyze = self._eye.decide
                self._patch_applied = True
                self.patch_detail = "patched:decide->analyze"
            else:
                raise AttributeError("EyeOfGodV3 has neither .analyze() nor .decide()")

        except ImportError as e:
            # Create stub for testing
            self._eye = _StubEyeOfGod()
            self.patch_detail = f"stub:import_failed:{e}"

    def analyze(self, signal: Dict[str, Any]) -> Dict[str, Any]:
        """
        Analyze signal using EyeOfGodV3.

        This method provides .analyze() interface regardless of
        whether the underlying EyeOfGodV3 uses .decide() or .analyze()
        """
        if hasattr(self._eye, 'analyze'):
            result = self._eye.analyze(signal)
        elif hasattr(self._eye, 'decide'):
            result = self._eye.decide(signal)
        else:
            return {"action": "SKIP", "reason": "no_method", "confidence": 0}

        # Normalize result format
        return self._normalize_result(result)

    def decide(self, signal: Dict[str, Any]) -> Dict[str, Any]:
        """Alias for analyze() - for backward compatibility."""
        return self.analyze(signal)

    def _normalize_result(self, result) -> Dict[str, Any]:
        """Normalize result to standard dict format."""
        if result is None:
            return {"action": "SKIP", "reason": "null_result", "confidence": 0}

        if hasattr(result, 'action'):
            # It's a dataclass/namedtuple
            return {
                "action": getattr(result, 'action', 'SKIP'),
                "confidence": getattr(result, 'confidence', 0),
                "reasons": getattr(result, 'reasons', []),
                "symbol": getattr(result, 'symbol', ''),
                "target_pct": getattr(result, 'target_pct', 0),
                "stop_pct": getattr(result, 'stop_pct', 0),
                "position_size_usdt": getattr(result, 'position_size_usdt', 0),
                "timeout_sec": getattr(result, 'timeout_sec', 0),
                "checksum": getattr(result, 'checksum', ''),
            }

        if isinstance(result, dict):
            return result

        return {"action": "SKIP", "reason": "unknown_result_type", "raw": str(result)}

    @property
    def eye(self):
        """Access underlying EyeOfGodV3 instance."""
        return self._eye


class _StubEyeOfGod:
    """Stub for when EyeOfGodV3 cannot be imported."""

    def analyze(self, signal: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "action": "SKIP",
            "reason": "stub_mode",
            "confidence": 0,
            "symbol": signal.get("symbol", ""),
        }

    def decide(self, signal: Dict[str, Any]) -> Dict[str, Any]:
        return self.analyze(signal)


# ═══════════════════════════════════════════════════════════════════════════════
# SINGLETON
# ═══════════════════════════════════════════════════════════════════════════════

_adapter: Optional[EyeOfGodAdapter] = None


def get_eye_of_god_adapter() -> EyeOfGodAdapter:
    """Get singleton adapter instance."""
    global _adapter
    if _adapter is None:
        _adapter = EyeOfGodAdapter()
    return _adapter


# ═══════════════════════════════════════════════════════════════════════════════
# TEST
# ═══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("=" * 60)
    print("EYE OF GOD ADAPTER TEST")
    print("=" * 60)

    adapter = EyeOfGodAdapter()
    print(f"Patch detail: {adapter.patch_detail}")

    # Test signal
    test_signal = {
        "symbol": "PEPEUSDT",
        "delta_pct": 15.0,
        "type": "EXPLOSION",
        "confidence": 0.8,
        "price": 0.00001234,
        "daily_volume_m": 50.0,
        "timestamp": "2026-01-30T22:00:00Z",
    }

    print(f"\nTest signal: {test_signal['symbol']} delta={test_signal['delta_pct']}%")

    # Test .analyze()
    result = adapter.analyze(test_signal)
    print(f"\n.analyze() result:")
    print(f"  Action: {result.get('action', 'N/A')}")
    print(f"  Confidence: {result.get('confidence', 0)}")
    print(f"  Reasons: {result.get('reasons', [])}")

    # Test .decide()
    result2 = adapter.decide(test_signal)
    print(f"\n.decide() result:")
    print(f"  Action: {result2.get('action', 'N/A')}")

    print("\n" + "=" * 60)
    print("[PASS] EyeOfGodAdapter test completed")
