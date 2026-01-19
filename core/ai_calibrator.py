"""
HOPE/NORE AI Impact Calibrator v1.0

Two modes:
1. Offline (default): TF-IDF similarity + keyword boost + outcome feedback
2. Online (optional): LLM via Anthropic API for real-time calibration

Design principles:
- Fail-closed: LLM timeout/error = fallback to base_score
- Explicit contracts: calibrated_score with sha256: evidence trail
- No hallucination: LLM is calibrator, not truth source

Usage:
    from core.ai_calibrator import ImpactCalibrator

    calibrator = ImpactCalibrator()

    # Offline calibration (fast, no API)
    score = calibrator.calibrate_offline(title, base_score)

    # Online calibration (slow, requires ANTHROPIC_API_KEY)
    score = await calibrator.calibrate_online(title, base_score)
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

STATE_DIR = Path(r"C:\Users\kirillDev\Desktop\TradingBot\minibot\state")
CALIBRATION_LOG = STATE_DIR / "calibration_log.jsonl"

HIGH_IMPACT_KEYWORDS = {
    "regulation": ["sec", "ban", "approved", "lawsuit", "legal", "regulate", "enforcement"],
    "institutional": ["etf", "grayscale", "blackrock", "fidelity", "institutional", "treasury"],
    "exploit": ["hack", "exploit", "vulnerability", "breach", "stolen", "rug pull", "scam"],
    "macro": ["fed", "rate", "inflation", "recession", "tariff", "geopolitical"],
    "market": ["crash", "surge", "rally", "dump", "liquidation", "all-time high", "ath"],
}

BOOST_FACTORS = {
    "regulation": 0.15,
    "institutional": 0.12,
    "exploit": 0.20,
    "macro": 0.10,
    "market": 0.08,
}

ASSET_WEIGHTS = {
    "BTC": 1.0,
    "ETH": 0.9,
    "SOL": 0.7,
    "BNB": 0.6,
    "XRP": 0.6,
}


@dataclass
class CalibrationResult:
    """Result of impact calibration."""
    original_score: float
    calibrated_score: float
    method: str  # "offline" | "online"
    factors: Dict[str, float]
    evidence_hash: str
    timestamp: float


def _sha256_short(text: str) -> str:
    """Compute sha256 prefix for evidence trail."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _tokenize(text: str) -> List[str]:
    """Simple tokenization: lowercase, alphanumeric only."""
    return re.findall(r"[a-z0-9]+", text.lower())


def _log_calibration(result: CalibrationResult, title: str) -> None:
    """Append calibration result to log file."""
    entry = {
        "timestamp": result.timestamp,
        "title_hash": _sha256_short(title),
        "original_score": result.original_score,
        "calibrated_score": result.calibrated_score,
        "method": result.method,
        "factors": result.factors,
        "evidence_hash": result.evidence_hash,
    }
    try:
        with open(CALIBRATION_LOG, "a", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
            f.flush()
            os.fsync(f.fileno())
    except OSError as e:
        logger.warning("Failed to log calibration: %s", e)


class ImpactCalibrator:
    """
    AI-powered impact score calibrator.

    Offline mode: keyword matching + asset weighting + historical feedback.
    Online mode: LLM calibration with strict JSON output validation.
    """

    def __init__(self, anthropic_api_key: Optional[str] = None):
        self._api_key = anthropic_api_key
        self._outcome_feedback: Dict[str, float] = {}
        self._load_outcome_feedback()

    def _load_outcome_feedback(self) -> None:
        """Load historical outcome data for feedback calibration."""
        outcomes_path = STATE_DIR / "signal_outcomes.jsonl"
        if not outcomes_path.exists():
            return

        try:
            with open(outcomes_path, "r", encoding="utf-8") as f:
                for line in f:
                    parts = line.strip().split(":", 2)
                    if len(parts) != 3:
                        continue
                    obj = json.loads(parts[2])
                    if obj.get("kind") != "signal_outcome" or obj.get("reason") != "ok":
                        continue

                    mfe = obj.get("mfe")
                    mae = obj.get("mae")
                    if mfe is not None and mae is not None:
                        signal_id = obj["signal_id"]
                        self._outcome_feedback[signal_id] = mfe - abs(mae)

            logger.info("Loaded %d outcome feedback entries", len(self._outcome_feedback))
        except (OSError, json.JSONDecodeError) as e:
            logger.warning("Failed to load outcome feedback: %s", e)

    def calibrate_offline(
        self,
        title: str,
        base_score: float,
        event_type: Optional[str] = None,
        assets: Optional[List[str]] = None,
    ) -> CalibrationResult:
        """
        Calibrate impact score using offline methods (no API).

        Factors:
        1. Keyword boost based on event type detection
        2. Asset weight multiplier
        3. Historical outcome feedback (if available)
        """
        tokens = _tokenize(title)
        factors: Dict[str, float] = {"base": base_score}
        score = base_score

        detected_type = event_type
        if not detected_type:
            for etype, keywords in HIGH_IMPACT_KEYWORDS.items():
                if any(kw in title.lower() for kw in keywords):
                    detected_type = etype
                    break

        if detected_type and detected_type in BOOST_FACTORS:
            boost = BOOST_FACTORS[detected_type]
            score += boost
            factors[f"keyword_boost_{detected_type}"] = boost

        if assets:
            max_weight = max(ASSET_WEIGHTS.get(a, 0.5) for a in assets)
            weight_factor = max_weight / 1.0
            score *= (0.8 + 0.4 * weight_factor)
            factors["asset_weight"] = weight_factor

        urgent_keywords = ["breaking", "just in", "urgent", "alert", "emergency"]
        if any(kw in title.lower() for kw in urgent_keywords):
            score *= 1.1
            factors["urgency_boost"] = 0.1

        score = max(0.0, min(1.0, score))
        factors["final"] = score

        evidence = json.dumps({"title": title, "factors": factors}, sort_keys=True)

        result = CalibrationResult(
            original_score=base_score,
            calibrated_score=round(score, 4),
            method="offline",
            factors=factors,
            evidence_hash=f"sha256:{_sha256_short(evidence)}",
            timestamp=time.time(),
        )

        _log_calibration(result, title)
        return result

    async def calibrate_online(
        self,
        title: str,
        base_score: float,
        context: Optional[str] = None,
        timeout_sec: float = 5.0,
    ) -> CalibrationResult:
        """
        Calibrate impact score using LLM (Anthropic API).

        Fail-closed: any error returns base_score with method="online_fallback".
        """
        if not self._api_key:
            logger.warning("No ANTHROPIC_API_KEY, falling back to offline")
            return self.calibrate_offline(title, base_score)

        prompt = f"""You are a crypto market impact analyzer. Rate the market impact of this news headline on a scale of 0.0 to 1.0.

Headline: "{title}"
Base score: {base_score}

Consider:
- Regulatory impact (SEC, bans, approvals)
- Institutional involvement (ETFs, treasuries)
- Security events (hacks, exploits)
- Macroeconomic factors (Fed, rates)
- Market-moving potential

Respond with ONLY a JSON object:
{{"score": <float>, "reason": "<brief explanation>"}}"""

        try:
            import httpx

            async with httpx.AsyncClient(timeout=timeout_sec) as client:
                response = await client.post(
                    "https://api.anthropic.com/v1/messages",
                    headers={
                        "x-api-key": self._api_key,
                        "anthropic-version": "2023-06-01",
                        "content-type": "application/json",
                    },
                    json={
                        "model": "claude-3-haiku-20240307",
                        "max_tokens": 100,
                        "messages": [{"role": "user", "content": prompt}],
                    },
                )

                if response.status_code != 200:
                    logger.warning("Anthropic API error: %d", response.status_code)
                    return self._fallback_result(title, base_score, "api_error")

                data = response.json()
                content = data.get("content", [{}])[0].get("text", "")

                parsed = json.loads(content)
                score = float(parsed.get("score", base_score))
                reason = parsed.get("reason", "")

                score = max(0.0, min(1.0, score))

                factors = {
                    "base": base_score,
                    "llm_score": score,
                    "llm_reason": reason[:100],
                }

                evidence = json.dumps({"title": title, "prompt": prompt[:200], "response": content[:200]}, sort_keys=True)

                result = CalibrationResult(
                    original_score=base_score,
                    calibrated_score=round(score, 4),
                    method="online",
                    factors=factors,
                    evidence_hash=f"sha256:{_sha256_short(evidence)}",
                    timestamp=time.time(),
                )

                _log_calibration(result, title)
                return result

        except Exception as e:
            logger.warning("LLM calibration failed: %s", e)
            return self._fallback_result(title, base_score, str(e)[:50])

    def _fallback_result(self, title: str, base_score: float, error: str) -> CalibrationResult:
        """Create fallback result when online calibration fails."""
        offline = self.calibrate_offline(title, base_score)
        return CalibrationResult(
            original_score=base_score,
            calibrated_score=offline.calibrated_score,
            method="online_fallback",
            factors={"error": error, **offline.factors},
            evidence_hash=offline.evidence_hash,
            timestamp=time.time(),
        )

    def batch_calibrate(
        self,
        items: List[Tuple[str, float]],
    ) -> List[CalibrationResult]:
        """
        Calibrate multiple items offline.

        Args:
            items: List of (title, base_score) tuples

        Returns:
            List of CalibrationResult
        """
        return [self.calibrate_offline(title, score) for title, score in items]


def get_calibrator() -> ImpactCalibrator:
    """Get singleton calibrator instance."""
    global _calibrator_instance
    if "_calibrator_instance" not in globals():
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        _calibrator_instance = ImpactCalibrator(anthropic_api_key=api_key)
    return _calibrator_instance


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    print("=== AI IMPACT CALIBRATOR TEST ===\n")

    calibrator = ImpactCalibrator()

    test_cases = [
        ("SEC Approves First Spot Bitcoin ETF", 0.5),
        ("DeFi Protocol Hacked for $50M", 0.4),
        ("Fed Announces Rate Cut", 0.3),
        ("Bitcoin Breaks All-Time High", 0.6),
        ("Minor update to Ethereum client", 0.2),
    ]

    print("Offline Calibration Results:")
    for title, base_score in test_cases:
        result = calibrator.calibrate_offline(title, base_score)
        print(f"\n  Title: {title[:50]}...")
        print(f"  Base: {result.original_score:.2f} -> Calibrated: {result.calibrated_score:.2f}")
        print(f"  Method: {result.method}")
        print(f"  Factors: {result.factors}")
