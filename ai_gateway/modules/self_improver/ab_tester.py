# -*- coding: utf-8 -*-
# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-29 10:00:00 UTC
# Purpose: A/B testing for model versions
# === END SIGNATURE ===
"""
A/B Tester - Compare model versions in production.

Features:
- Run two models in parallel
- Track performance metrics for each
- Automatic winner selection
- Statistical significance testing

Usage:
    tester = ABTester()
    tester.start_test(model_a_version=2, model_b_version=3)

    # Record predictions and outcomes
    tester.record_prediction("A", signal_id, prediction)
    tester.record_outcome(signal_id, actual_outcome)

    # Check results
    if tester.has_winner():
        winner = tester.get_winner()
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Minimum samples for statistical significance
MIN_SAMPLES_PER_ARM = 30


@dataclass
class TestArm:
    """One arm of the A/B test."""
    name: str  # "A" or "B"
    model_version: int
    predictions: Dict[str, Dict[str, Any]] = field(default_factory=dict)  # signal_id -> prediction
    outcomes: Dict[str, bool] = field(default_factory=dict)  # signal_id -> win/loss

    @property
    def total_predictions(self) -> int:
        return len(self.predictions)

    @property
    def total_outcomes(self) -> int:
        return len(self.outcomes)

    @property
    def wins(self) -> int:
        return sum(1 for o in self.outcomes.values() if o)

    @property
    def losses(self) -> int:
        return sum(1 for o in self.outcomes.values() if not o)

    @property
    def win_rate(self) -> float:
        if self.total_outcomes == 0:
            return 0.0
        return self.wins / self.total_outcomes


@dataclass
class ABTest:
    """A/B test session."""
    test_id: str
    arm_a: TestArm
    arm_b: TestArm
    started_at: datetime
    ended_at: Optional[datetime] = None
    winner: Optional[str] = None
    status: str = "running"  # running, completed, cancelled


class ABTester:
    """
    A/B testing for model comparison.

    Usage:
        tester = ABTester(state_dir=Path("state/ai/ab_tests"))

        # Start test
        test_id = tester.start_test(model_a_version=2, model_b_version=3)

        # Route signals (50/50 split)
        arm = tester.route_signal(signal_id)  # Returns "A" or "B"

        # Record prediction
        tester.record_prediction(arm, signal_id, {"win_prob": 0.75})

        # Record actual outcome
        tester.record_outcome(signal_id, is_win=True)

        # Check for winner
        if tester.has_winner():
            winner_version = tester.get_winner()
            tester.end_test()
    """

    def __init__(self, state_dir: Path = Path("state/ai/ab_tests")):
        self.state_dir = state_dir
        self.tests_file = state_dir / "tests.json"

        # Current active test
        self._active_test: Optional[ABTest] = None

        # Routing state (deterministic based on signal_id)
        self._signal_routing: Dict[str, str] = {}  # signal_id -> arm

        # Create directory
        self.state_dir.mkdir(parents=True, exist_ok=True)

        # Load state
        self._load_state()

        logger.info(f"ABTester initialized, active test: {self._active_test.test_id if self._active_test else None}")

    def _load_state(self) -> None:
        """Load state from disk."""
        if not self.tests_file.exists():
            return

        try:
            with open(self.tests_file, "r", encoding="utf-8") as f:
                data = json.load(f)

            # Find active test
            for test_data in data.get("tests", []):
                if test_data.get("status") == "running":
                    self._active_test = self._deserialize_test(test_data)
                    break

        except Exception as e:
            logger.error(f"Failed to load A/B tests: {e}")

    def _deserialize_test(self, data: Dict[str, Any]) -> ABTest:
        """Deserialize test from JSON."""
        arm_a_data = data["arm_a"]
        arm_b_data = data["arm_b"]

        arm_a = TestArm(
            name=arm_a_data["name"],
            model_version=arm_a_data["model_version"],
            predictions=arm_a_data.get("predictions", {}),
            outcomes=arm_a_data.get("outcomes", {}),
        )

        arm_b = TestArm(
            name=arm_b_data["name"],
            model_version=arm_b_data["model_version"],
            predictions=arm_b_data.get("predictions", {}),
            outcomes=arm_b_data.get("outcomes", {}),
        )

        return ABTest(
            test_id=data["test_id"],
            arm_a=arm_a,
            arm_b=arm_b,
            started_at=datetime.fromisoformat(data["started_at"]),
            ended_at=datetime.fromisoformat(data["ended_at"]) if data.get("ended_at") else None,
            winner=data.get("winner"),
            status=data.get("status", "running"),
        )

    def _save_state(self) -> None:
        """Save state to disk (atomic write)."""
        tests_data = []

        # Add active test
        if self._active_test:
            tests_data.append(self._serialize_test(self._active_test))

        data = {
            "updated_at": datetime.utcnow().isoformat() + "Z",
            "tests": tests_data,
        }

        tmp_path = self.tests_file.with_suffix(".tmp")
        try:
            with open(tmp_path, "w", encoding="utf-8", newline="\n") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, self.tests_file)
        except Exception as e:
            logger.error(f"Failed to save A/B tests: {e}")
            if tmp_path.exists():
                tmp_path.unlink()

    def _serialize_test(self, test: ABTest) -> Dict[str, Any]:
        """Serialize test to JSON."""
        return {
            "test_id": test.test_id,
            "arm_a": {
                "name": test.arm_a.name,
                "model_version": test.arm_a.model_version,
                "predictions": test.arm_a.predictions,
                "outcomes": {k: v for k, v in test.arm_a.outcomes.items()},
            },
            "arm_b": {
                "name": test.arm_b.name,
                "model_version": test.arm_b.model_version,
                "predictions": test.arm_b.predictions,
                "outcomes": {k: v for k, v in test.arm_b.outcomes.items()},
            },
            "started_at": test.started_at.isoformat() + "Z",
            "ended_at": test.ended_at.isoformat() + "Z" if test.ended_at else None,
            "winner": test.winner,
            "status": test.status,
        }

    def start_test(
        self,
        model_a_version: int,
        model_b_version: int,
    ) -> str:
        """
        Start a new A/B test.

        Args:
            model_a_version: Version number for arm A
            model_b_version: Version number for arm B

        Returns:
            Test ID
        """
        if self._active_test is not None:
            raise RuntimeError(f"Test already running: {self._active_test.test_id}")

        test_id = f"ab_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}"

        self._active_test = ABTest(
            test_id=test_id,
            arm_a=TestArm(name="A", model_version=model_a_version),
            arm_b=TestArm(name="B", model_version=model_b_version),
            started_at=datetime.utcnow(),
        )

        self._signal_routing = {}
        self._save_state()

        logger.info(f"Started A/B test {test_id}: v{model_a_version} vs v{model_b_version}")
        return test_id

    def route_signal(self, signal_id: str) -> str:
        """
        Route signal to an arm (deterministic 50/50 split).

        Args:
            signal_id: Signal identifier

        Returns:
            "A" or "B"
        """
        if self._active_test is None:
            return "A"  # Default to A if no test

        # Check if already routed
        if signal_id in self._signal_routing:
            return self._signal_routing[signal_id]

        # Deterministic routing based on hash
        arm = "A" if hash(signal_id) % 2 == 0 else "B"
        self._signal_routing[signal_id] = arm

        return arm

    def record_prediction(
        self,
        arm: str,
        signal_id: str,
        prediction: Dict[str, Any],
    ) -> None:
        """
        Record a prediction from an arm.

        Args:
            arm: "A" or "B"
            signal_id: Signal identifier
            prediction: Prediction dict
        """
        if self._active_test is None:
            return

        target_arm = self._active_test.arm_a if arm == "A" else self._active_test.arm_b
        target_arm.predictions[signal_id] = {
            "prediction": prediction,
            "timestamp": datetime.utcnow().isoformat() + "Z",
        }

        self._save_state()

    def record_outcome(self, signal_id: str, is_win: bool) -> None:
        """
        Record actual outcome for a signal.

        Args:
            signal_id: Signal identifier
            is_win: Whether the signal resulted in a win
        """
        if self._active_test is None:
            return

        # Find which arm this signal belongs to
        arm = self._signal_routing.get(signal_id)
        if arm is None:
            return

        target_arm = self._active_test.arm_a if arm == "A" else self._active_test.arm_b
        target_arm.outcomes[signal_id] = is_win

        self._save_state()

        logger.debug(f"Recorded outcome for {signal_id}: {'WIN' if is_win else 'LOSS'} (arm {arm})")

    def has_winner(self, min_samples: int = MIN_SAMPLES_PER_ARM) -> bool:
        """
        Check if there's a statistically significant winner.

        Args:
            min_samples: Minimum samples per arm

        Returns:
            True if there's a winner
        """
        if self._active_test is None:
            return False

        arm_a = self._active_test.arm_a
        arm_b = self._active_test.arm_b

        # Check minimum samples
        if arm_a.total_outcomes < min_samples or arm_b.total_outcomes < min_samples:
            return False

        # Simple significance test (>5% difference with enough samples)
        win_rate_diff = abs(arm_a.win_rate - arm_b.win_rate)
        return win_rate_diff >= 0.05  # 5% difference

    def get_winner(self) -> Optional[int]:
        """
        Get winning model version.

        Returns:
            Model version number or None
        """
        if not self.has_winner():
            return None

        arm_a = self._active_test.arm_a
        arm_b = self._active_test.arm_b

        if arm_a.win_rate > arm_b.win_rate:
            return arm_a.model_version
        else:
            return arm_b.model_version

    def get_results(self) -> Dict[str, Any]:
        """Get current test results."""
        if self._active_test is None:
            return {"status": "no_active_test"}

        arm_a = self._active_test.arm_a
        arm_b = self._active_test.arm_b

        return {
            "test_id": self._active_test.test_id,
            "status": self._active_test.status,
            "started_at": self._active_test.started_at.isoformat() + "Z",
            "arm_a": {
                "model_version": arm_a.model_version,
                "predictions": arm_a.total_predictions,
                "outcomes": arm_a.total_outcomes,
                "wins": arm_a.wins,
                "losses": arm_a.losses,
                "win_rate": round(arm_a.win_rate, 4),
            },
            "arm_b": {
                "model_version": arm_b.model_version,
                "predictions": arm_b.total_predictions,
                "outcomes": arm_b.total_outcomes,
                "wins": arm_b.wins,
                "losses": arm_b.losses,
                "win_rate": round(arm_b.win_rate, 4),
            },
            "has_winner": self.has_winner(),
            "winner_version": self.get_winner(),
        }

    def end_test(self, winner: Optional[str] = None) -> Dict[str, Any]:
        """
        End the current test.

        Args:
            winner: Override winner ("A" or "B"), auto-detect if None

        Returns:
            Final results
        """
        if self._active_test is None:
            return {"error": "No active test"}

        results = self.get_results()

        # Determine winner
        if winner is None:
            arm_a = self._active_test.arm_a
            arm_b = self._active_test.arm_b
            if arm_a.win_rate > arm_b.win_rate:
                winner = "A"
            elif arm_b.win_rate > arm_a.win_rate:
                winner = "B"
            else:
                winner = "TIE"

        self._active_test.winner = winner
        self._active_test.ended_at = datetime.utcnow()
        self._active_test.status = "completed"

        self._save_state()

        logger.info(f"Ended A/B test {self._active_test.test_id}, winner: {winner}")

        # Clear active test
        self._active_test = None
        self._signal_routing = {}

        return results

    def cancel_test(self) -> None:
        """Cancel current test without selecting winner."""
        if self._active_test is None:
            return

        logger.info(f"Cancelled A/B test {self._active_test.test_id}")
        self._active_test = None
        self._signal_routing = {}
        self._save_state()

    @property
    def is_testing(self) -> bool:
        """Check if a test is currently active."""
        return self._active_test is not None

    def get_arm_models(self) -> Optional[Tuple[int, int]]:
        """
        Get model versions for both arms.

        Returns:
            (arm_a_version, arm_b_version) or None
        """
        if self._active_test is None:
            return None
        return (
            self._active_test.arm_a.model_version,
            self._active_test.arm_b.model_version,
        )
