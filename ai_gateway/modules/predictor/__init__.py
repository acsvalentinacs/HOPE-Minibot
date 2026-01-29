# -*- coding: utf-8 -*-
# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-29 08:05:00 UTC
# Purpose: Signal predictor module for AI-Gateway
# === END SIGNATURE ===
"""
Signal Predictor Module — AI-powered signal classification.

Components:
- SignalClassifier: XGBoost model for WIN/LOSS prediction
- Feature extraction utilities
- Model training and evaluation

Usage:
    from ai_gateway.modules.predictor import SignalClassifier

    classifier = SignalClassifier()
    result = classifier.predict(signal)
"""

from .signal_classifier import (
    SignalClassifier,
    extract_features,
    extract_label,
    FEATURE_NAMES,
)

__all__ = [
    "SignalClassifier",
    "extract_features",
    "extract_label",
    "FEATURE_NAMES",
]
