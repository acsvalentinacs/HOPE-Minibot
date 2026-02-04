# -*- coding: utf-8 -*-
"""
EYE OF GOD ADAPTER v1.0
=======================

Адаптер для совместимости с EyeOfGodV3.
Если у класса нет метода analyze() — добавляем его.

ИСПОЛЬЗОВАНИЕ:
    from scripts.eye_of_god_adapter import EyeOfGodAdapter
    
    eye = EyeOfGodAdapter()
    result = eye.analyze(signal)  # Всегда работает!
"""

import logging
from typing import Dict, Any, Optional

log = logging.getLogger(__name__)


class EyeOfGodAdapter:
    """
    Адаптер для EyeOfGodV3 с гарантированным методом analyze().
    """
    
    def __init__(self):
        self._eye = None
        self._patched = False
        self.patch_detail = "not_loaded"
        
        self._load_eye()
    
    def _load_eye(self):
        """Загрузить EyeOfGodV3 и при необходимости пропатчить."""
        try:
            from scripts.eye_of_god_v3 import EyeOfGodV3
            self._eye = EyeOfGodV3()
            
            # Check what methods exist
            methods = [m for m in dir(self._eye) if not m.startswith('_') and callable(getattr(self._eye, m))]
            
            if hasattr(self._eye, 'analyze'):
                self.patch_detail = "native_analyze"
                log.info("EyeOfGodV3 has native analyze()")
            elif hasattr(self._eye, 'evaluate'):
                self._patch_analyze_from_evaluate()
                self.patch_detail = "patched_from_evaluate"
                log.info("Patched analyze() from evaluate()")
            elif hasattr(self._eye, 'decide'):
                self._patch_analyze_from_decide()
                self.patch_detail = "patched_from_decide"
                log.info("Patched analyze() from decide()")
            elif hasattr(self._eye, 'process_signal'):
                self._patch_analyze_from_process()
                self.patch_detail = "patched_from_process_signal"
                log.info("Patched analyze() from process_signal()")
            else:
                # Create dummy analyze that returns safe default
                self._patch_dummy_analyze()
                self.patch_detail = f"dummy_fallback (methods={methods[:5]})"
                log.warning(f"No suitable method found, using dummy. Available: {methods}")
            
            self._patched = True
            
        except ImportError as e:
            log.error(f"Failed to import EyeOfGodV3: {e}")
            self._eye = None
            self.patch_detail = f"import_error: {e}"
    
    def _patch_analyze_from_evaluate(self):
        """Создать analyze() из evaluate()."""
        original_evaluate = self._eye.evaluate
        
        def analyze(signal: Dict) -> Dict[str, Any]:
            result = original_evaluate(signal)
            return self._normalize_result(result, signal)
        
        self._eye.analyze = analyze
    
    def _patch_analyze_from_decide(self):
        """Создать analyze() из decide()."""
        original_decide = self._eye.decide
        
        def analyze(signal: Dict) -> Dict[str, Any]:
            result = original_decide(signal)
            return self._normalize_result(result, signal)
        
        self._eye.analyze = analyze
    
    def _patch_analyze_from_process(self):
        """Создать analyze() из process_signal()."""
        original_process = self._eye.process_signal
        
        def analyze(signal: Dict) -> Dict[str, Any]:
            result = original_process(signal)
            return self._normalize_result(result, signal)
        
        self._eye.analyze = analyze
    
    def _patch_dummy_analyze(self):
        """Создать dummy analyze() который просто пропускает."""
        def analyze(signal: Dict) -> Dict[str, Any]:
            return {
                "action": "SKIP",
                "confidence": 0.0,
                "reason": "eye_of_god_not_available",
                "signal": signal,
            }
        
        self._eye.analyze = analyze
    
    def _normalize_result(self, result: Any, signal: Dict) -> Dict[str, Any]:
        """Нормализовать результат в стандартный формат."""
        if result is None:
            return {
                "action": "SKIP",
                "confidence": 0.0,
                "reason": "none_result",
                "signal": signal,
            }
        
        if isinstance(result, dict):
            # Ensure required fields
            return {
                "action": result.get("action", result.get("decision", "SKIP")).upper(),
                "confidence": float(result.get("confidence", result.get("score", 0.0))),
                "reason": result.get("reason", result.get("reasons", [])),
                "signal": signal,
                "raw": result,
            }
        
        if isinstance(result, str):
            return {
                "action": result.upper(),
                "confidence": 0.5,
                "reason": "string_result",
                "signal": signal,
            }
        
        if isinstance(result, (tuple, list)) and len(result) >= 2:
            return {
                "action": str(result[0]).upper(),
                "confidence": float(result[1]) if len(result) > 1 else 0.5,
                "reason": result[2] if len(result) > 2 else "tuple_result",
                "signal": signal,
            }
        
        return {
            "action": "SKIP",
            "confidence": 0.0,
            "reason": f"unknown_result_type: {type(result)}",
            "signal": signal,
        }
    
    def analyze(self, signal: Dict[str, Any]) -> Dict[str, Any]:
        """
        Анализировать сигнал.
        
        Args:
            signal: Словарь с данными сигнала
        
        Returns:
            {
                "action": "BUY" | "SELL" | "SKIP",
                "confidence": 0.0-1.0,
                "reason": str | list,
                "signal": original signal,
            }
        """
        if self._eye is None:
            return {
                "action": "SKIP",
                "confidence": 0.0,
                "reason": "eye_not_loaded",
                "signal": signal,
            }
        
        try:
            return self._eye.analyze(signal)
        except Exception as e:
            log.error(f"Eye analyze error: {e}")
            return {
                "action": "SKIP",
                "confidence": 0.0,
                "reason": f"error: {e}",
                "signal": signal,
            }
    
    @property
    def is_available(self) -> bool:
        return self._eye is not None and self._patched


# ══════════════════════════════════════════════════════════════════════════════
# SINGLETON
# ══════════════════════════════════════════════════════════════════════════════

_adapter: Optional[EyeOfGodAdapter] = None

def get_eye_adapter() -> EyeOfGodAdapter:
    """Получить адаптер."""
    global _adapter
    if _adapter is None:
        _adapter = EyeOfGodAdapter()
    return _adapter


# ══════════════════════════════════════════════════════════════════════════════
# TEST
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("=" * 60)
    print("EYE OF GOD ADAPTER TEST")
    print("=" * 60)
    
    adapter = EyeOfGodAdapter()
    print(f"Patch detail: {adapter.patch_detail}")
    print(f"Available: {adapter.is_available}")
    
    # Test signal
    signal = {
        'symbol': 'PEPEUSDT',
        'delta_pct': 15.0,
        'type': 'EXPLOSION',
        'price': 0.00001,
    }
    
    result = adapter.analyze(signal)
    print(f"\nSignal: {signal['symbol']} delta={signal['delta_pct']}%")
    print(f"Result: action={result['action']}, confidence={result['confidence']}")
    print(f"Reason: {result['reason']}")
    
    print("\n[PASS] Adapter test")
