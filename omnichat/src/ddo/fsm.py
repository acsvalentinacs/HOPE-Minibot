# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-27T12:25:00Z
# Purpose: DDO Finite State Machine - Discussion phase management
# === END SIGNATURE ===
"""
DDO Finite State Machine.

Manages transitions between discussion phases with fail-closed guards.
Ensures discussions follow valid paths and cannot enter invalid states.

Design principles:
- Explicit valid transitions only
- Fail-closed on any guard violation
- Full audit trail of all transitions
- Thread-safe state management
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime
from typing import Optional

from .types import (
    DiscussionPhase,
    DiscussionContext,
    DiscussionTemplate,
    PhaseConfig,
    PhaseChangeCallback,
)

_log = logging.getLogger("ddo.fsm")


class TransitionError(Exception):
    """Raised when an invalid state transition is attempted."""
    pass


class GuardError(Exception):
    """Raised when a guard check fails."""
    pass


class DiscussionFSM:
    """
    Finite State Machine for managing discussion phases.

    Controls the flow of a discussion, ensuring only valid transitions
    occur and all guards pass before allowing state changes.

    Thread Safety:
        All state mutations are protected by a lock.
        Multiple threads can safely call transition methods.

    Usage:
        fsm = DiscussionFSM(context, template)
        fsm.set_phase_change_callback(on_change)

        if fsm.can_transition(DiscussionPhase.ARCHITECT):
            fsm.transition(DiscussionPhase.ARCHITECT, "Starting")

        # Check guards before any operation
        passed, reason = fsm.check_guards()
        if not passed:
            fsm.transition(DiscussionPhase.FAILED, reason)
    """

    # Valid transitions map: from_phase -> [allowed_to_phases]
    # Any transition not in this map is INVALID
    VALID_TRANSITIONS: dict[DiscussionPhase, list[DiscussionPhase]] = {
        DiscussionPhase.INIT: [
            DiscussionPhase.ARCHITECT,
            DiscussionPhase.ANALYZE,  # For CODE_REVIEW mode
            DiscussionPhase.SECURITY_REVIEW,  # For CODE_REVIEW mode
            DiscussionPhase.FAILED,
        ],
        DiscussionPhase.ARCHITECT: [
            DiscussionPhase.ANALYZE,
            DiscussionPhase.IMPLEMENT,  # For QUICK mode (skip ANALYZE)
            DiscussionPhase.FAILED,
            DiscussionPhase.ESCALATED,
        ],
        DiscussionPhase.ANALYZE: [
            DiscussionPhase.IMPLEMENT,
            DiscussionPhase.ARCHITECT,  # Loop back if needs revision
            DiscussionPhase.SYNTHESIZE,  # For BRAINSTORM mode
            DiscussionPhase.FAILED,
            DiscussionPhase.ESCALATED,
        ],
        DiscussionPhase.IMPLEMENT: [
            DiscussionPhase.SECURITY_REVIEW,
            DiscussionPhase.SYNTHESIZE,  # For QUICK mode
            DiscussionPhase.FAILED,
            DiscussionPhase.ESCALATED,
        ],
        DiscussionPhase.SECURITY_REVIEW: [
            DiscussionPhase.CODE_REVIEW,
            DiscussionPhase.IMPLEMENT,  # Loop back if critical issues
            DiscussionPhase.SYNTHESIZE,  # For CODE_REVIEW mode
            DiscussionPhase.FAILED,
            DiscussionPhase.ESCALATED,
        ],
        DiscussionPhase.CODE_REVIEW: [
            DiscussionPhase.REFINE,
            DiscussionPhase.IMPLEMENT,  # Loop back if needs changes
            DiscussionPhase.SYNTHESIZE,  # For CODE_REVIEW mode
            DiscussionPhase.FAILED,
            DiscussionPhase.ESCALATED,
        ],
        DiscussionPhase.REFINE: [
            DiscussionPhase.SYNTHESIZE,
            DiscussionPhase.SECURITY_REVIEW,  # Re-review if major changes
            DiscussionPhase.FAILED,
        ],
        DiscussionPhase.SYNTHESIZE: [
            DiscussionPhase.CONSENSUS,
            DiscussionPhase.DONE,  # Skip consensus if not required
            DiscussionPhase.FAILED,
        ],
        DiscussionPhase.CONSENSUS: [
            DiscussionPhase.DONE,
            DiscussionPhase.REFINE,  # Loop back if no consensus
            DiscussionPhase.ESCALATED,
        ],
        # Terminal states have no valid transitions
        DiscussionPhase.DONE: [],
        DiscussionPhase.FAILED: [],
        DiscussionPhase.ESCALATED: [],
    }

    def __init__(
        self,
        context: DiscussionContext,
        template: DiscussionTemplate,
    ):
        """
        Initialize FSM with context and template.

        Args:
            context: Discussion context to manage
            template: Template defining the discussion flow
        """
        self.context = context
        self.template = template
        self._lock = threading.RLock()
        self._transition_history: list[tuple[datetime, DiscussionPhase, DiscussionPhase, str]] = []
        self._on_phase_change: Optional[PhaseChangeCallback] = None
        self._loop_count: dict[DiscussionPhase, int] = {}  # Track loops to prevent infinite

    def set_phase_change_callback(self, callback: PhaseChangeCallback) -> None:
        """
        Set callback for phase transitions.

        Callback receives (old_phase, new_phase) arguments.
        """
        self._on_phase_change = callback

    def can_transition(self, to_phase: DiscussionPhase) -> bool:
        """
        Check if transition to target phase is valid.

        Args:
            to_phase: Phase to transition to

        Returns:
            True if transition is allowed, False otherwise
        """
        with self._lock:
            current = self.context.current_phase
            valid_targets = self.VALID_TRANSITIONS.get(current, [])
            return to_phase in valid_targets

    def transition(self, to_phase: DiscussionPhase, reason: str = "") -> bool:
        """
        Attempt to transition to a new phase.

        Args:
            to_phase: Target phase
            reason: Reason for transition (for audit)

        Returns:
            True if transition successful

        Raises:
            TransitionError: If transition is invalid (in strict mode)
        """
        with self._lock:
            old_phase = self.context.current_phase

            # Validate transition
            if not self.can_transition(to_phase):
                _log.error(
                    f"Invalid transition: {old_phase.value} → {to_phase.value}"
                )
                return False

            # Check loop prevention
            if to_phase in self._loop_count:
                self._loop_count[to_phase] += 1
                if self._loop_count[to_phase] > 3:
                    _log.error(f"Too many loops to phase {to_phase.value}")
                    self.context.current_phase = DiscussionPhase.FAILED
                    self.context.escalation_reason = f"Infinite loop detected at {to_phase.value}"
                    return False
            else:
                self._loop_count[to_phase] = 1

            # Execute transition
            self.context.current_phase = to_phase

            # Record in history
            self._transition_history.append((
                datetime.utcnow(),
                old_phase,
                to_phase,
                reason,
            ))

            _log.info(
                f"Phase transition: {old_phase.value} → {to_phase.value} "
                f"(reason: {reason or 'none'})"
            )

            # Handle terminal states
            if to_phase.is_terminal:
                self.context.ended_at = datetime.utcnow()
                if to_phase == DiscussionPhase.ESCALATED:
                    self.context.escalation_reason = reason
                elif to_phase == DiscussionPhase.DONE:
                    self.context.consensus_reached = True

            # Notify callback
            if self._on_phase_change:
                try:
                    self._on_phase_change(old_phase, to_phase)
                except Exception as e:
                    _log.error(f"Phase change callback error: {e}")

            return True

    def check_guards(self) -> tuple[bool, str]:
        """
        Check all fail-closed guards.

        Guards checked:
        1. Cost limit
        2. Time limit
        3. Response count limit
        4. Consecutive failures

        Returns:
            (passed, reason) - If passed=False, reason explains why
        """
        with self._lock:
            ctx = self.context

            # Guard 1: Cost limit
            if ctx.is_over_budget:
                return False, (
                    f"Cost limit exceeded: ${ctx.cost_usd:.4f} >= "
                    f"${ctx.cost_limit_cents / 100:.4f}"
                )

            # Guard 2: Time limit
            if ctx.is_over_time:
                return False, (
                    f"Time limit exceeded: {ctx.elapsed_seconds:.0f}s >= "
                    f"{ctx.time_limit_seconds}s"
                )

            # Guard 3: Response count limit
            if ctx.response_count >= ctx.max_responses:
                return False, (
                    f"Response limit exceeded: {ctx.response_count} >= "
                    f"{ctx.max_responses}"
                )

            # Guard 4: Consecutive error responses
            recent_errors = 0
            for resp in reversed(ctx.responses[-5:]):
                if resp.is_error:
                    recent_errors += 1
                else:
                    break
            if recent_errors >= 3:
                return False, f"Too many consecutive errors: {recent_errors}"

            # Guard 5: Already terminal
            if ctx.is_terminal:
                return False, f"Already in terminal state: {ctx.current_phase.value}"

            return True, "OK"

    def get_current_phase_config(self) -> Optional[PhaseConfig]:
        """Get configuration for current phase from template."""
        return self.template.get_phase_config(self.context.current_phase)

    def get_next_phase(self) -> Optional[DiscussionPhase]:
        """
        Determine next phase based on template.

        Returns:
            Next phase or None if at end of template
        """
        current = self.context.current_phase

        # If synthesize/consensus, check if we're done
        if current == DiscussionPhase.SYNTHESIZE:
            if self.template.require_consensus:
                return DiscussionPhase.CONSENSUS
            return DiscussionPhase.DONE

        if current == DiscussionPhase.CONSENSUS:
            return DiscussionPhase.DONE

        # Otherwise follow template
        return self.template.get_next_phase(current)

    def is_terminal(self) -> bool:
        """Check if discussion is in terminal state."""
        return self.context.is_terminal

    def get_transition_history(self) -> list[dict]:
        """
        Get full transition history for audit.

        Returns:
            List of transition records with timestamps
        """
        return [
            {
                "timestamp": ts.isoformat(),
                "from_phase": old.value,
                "to_phase": new.value,
                "reason": reason,
            }
            for ts, old, new, reason in self._transition_history
        ]

    def force_fail(self, reason: str) -> None:
        """
        Force transition to FAILED state.

        Use for unrecoverable errors.
        """
        with self._lock:
            self.context.current_phase = DiscussionPhase.FAILED
            self.context.ended_at = datetime.utcnow()
            self.context.escalation_reason = reason
            _log.error(f"Forced FAILED state: {reason}")

    def force_escalate(self, reason: str) -> None:
        """
        Force transition to ESCALATED state.

        Use when human intervention is required.
        """
        with self._lock:
            self.context.current_phase = DiscussionPhase.ESCALATED
            self.context.ended_at = datetime.utcnow()
            self.context.escalation_reason = reason
            _log.warning(f"Forced ESCALATED state: {reason}")

    def reset_loop_count(self, phase: DiscussionPhase) -> None:
        """Reset loop counter for a phase (use after successful resolution)."""
        with self._lock:
            if phase in self._loop_count:
                del self._loop_count[phase]

    def __repr__(self) -> str:
        return (
            f"DiscussionFSM(id={self.context.id}, "
            f"phase={self.context.current_phase.value}, "
            f"transitions={len(self._transition_history)})"
        )
