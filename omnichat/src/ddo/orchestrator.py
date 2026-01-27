# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-27T12:50:00Z
# Purpose: DDO Orchestrator - Main discussion coordination engine
# === END SIGNATURE ===
"""
DDO Orchestrator - Dynamic Discussion Orchestrator.

Main coordination engine that runs multi-agent discussions.
Controls the flow between Gemini, GPT, and Claude through defined phases.

Key features:
- Async streaming of discussion progress
- Fail-closed guards at every phase
- Cost and time tracking
- Automatic retry with backoff
- Full audit trail

Usage:
    from omnichat.src.ddo import DDOOrchestrator, DiscussionMode

    orchestrator = DDOOrchestrator(agents)
    async for event in orchestrator.run_discussion(
        topic="Create a caching layer",
        mode=DiscussionMode.ARCHITECTURE
    ):
        print(event)
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime
from typing import AsyncIterator, Optional
from dataclasses import dataclass, field

from .types import (
    DiscussionPhase,
    DiscussionMode,
    DiscussionContext,
    DiscussionTemplate,
    AgentResponse,
    ResponseFlag,
    PhaseConfig,
)
from .fsm import DiscussionFSM, TransitionError, GuardError
from .guards import (
    check_response_quality,
    check_code_compiles,
    detect_conflict,
    run_all_guards,
    check_prompt_security,
)
from .roles import build_prompt, get_agent_for_phase
from .templates import get_template, TEMPLATES

_log = logging.getLogger("ddo.orchestrator")


# === EVENT TYPES ===

@dataclass
class DDOEvent:
    """Base class for DDO events streamed to caller."""
    event_type: str
    timestamp: datetime = field(default_factory=datetime.utcnow)

    def to_dict(self) -> dict:
        return {
            "event_type": self.event_type,
            "timestamp": self.timestamp.isoformat(),
        }


@dataclass
class PhaseStartEvent(DDOEvent):
    """Emitted when a new phase begins."""
    event_type: str = "phase_start"
    phase: DiscussionPhase = DiscussionPhase.INIT
    agent: str = ""

    def to_dict(self) -> dict:
        d = super().to_dict()
        d["phase"] = self.phase.value
        d["phase_display"] = self.phase.display_name
        d["agent"] = self.agent
        return d


@dataclass
class ResponseEvent(DDOEvent):
    """Emitted when an agent responds."""
    event_type: str = "response"
    response: Optional[AgentResponse] = None

    def to_dict(self) -> dict:
        d = super().to_dict()
        if self.response:
            d["response"] = self.response.to_dict()
        return d


@dataclass
class GuardFailEvent(DDOEvent):
    """Emitted when a guard check fails."""
    event_type: str = "guard_fail"
    guard_name: str = ""
    reason: str = ""
    recoverable: bool = True

    def to_dict(self) -> dict:
        d = super().to_dict()
        d["guard_name"] = self.guard_name
        d["reason"] = self.reason
        d["recoverable"] = self.recoverable
        return d


@dataclass
class ProgressEvent(DDOEvent):
    """Emitted to show progress."""
    event_type: str = "progress"
    current_phase: int = 0
    total_phases: int = 0
    message: str = ""
    cost_cents: float = 0.0
    elapsed_seconds: float = 0.0

    def to_dict(self) -> dict:
        d = super().to_dict()
        d["current_phase"] = self.current_phase
        d["total_phases"] = self.total_phases
        d["message"] = self.message
        d["cost_cents"] = self.cost_cents
        d["elapsed_seconds"] = self.elapsed_seconds
        return d


@dataclass
class CompletedEvent(DDOEvent):
    """Emitted when discussion completes."""
    event_type: str = "completed"
    context: Optional[DiscussionContext] = None
    success: bool = False

    def to_dict(self) -> dict:
        d = super().to_dict()
        d["success"] = self.success
        if self.context:
            d["discussion_id"] = self.context.id
            d["final_phase"] = self.context.current_phase.value
            d["cost_cents"] = self.context.total_cost_cents
            d["elapsed_str"] = self.context.elapsed_str
            d["consensus_reached"] = self.context.consensus_reached
        return d


@dataclass
class ErrorEvent(DDOEvent):
    """Emitted on fatal errors."""
    event_type: str = "error"
    error: str = ""
    phase: Optional[DiscussionPhase] = None

    def to_dict(self) -> dict:
        d = super().to_dict()
        d["error"] = self.error
        if self.phase:
            d["phase"] = self.phase.value
        return d


# === MAIN ORCHESTRATOR ===

class DDOOrchestrator:
    """
    Main DDO Orchestrator class.

    Coordinates multi-agent discussions through predefined phases.
    Uses FSM for state management and guards for quality control.

    Attributes:
        agents: Dict of agent name -> agent instance
        default_cost_limit: Default cost limit in cents
        default_time_limit: Default time limit in seconds
    """

    def __init__(
        self,
        agents: dict,
        default_cost_limit: float = 100.0,  # $1.00
        default_time_limit: int = 600,       # 10 minutes
        max_responses: int = 30,
    ):
        """
        Initialize orchestrator.

        Args:
            agents: Dict mapping agent keys (gemini, gpt, claude) to agent instances
            default_cost_limit: Default budget in cents
            default_time_limit: Default timeout in seconds
            max_responses: Maximum responses before force-stop
        """
        self.agents = agents
        self.default_cost_limit = default_cost_limit
        self.default_time_limit = default_time_limit
        self.max_responses = max_responses

        _log.info(
            f"DDOOrchestrator initialized with agents: {list(agents.keys())}, "
            f"cost_limit=${default_cost_limit/100:.2f}, time_limit={default_time_limit}s"
        )

    async def run_discussion(
        self,
        topic: str,
        mode: DiscussionMode = DiscussionMode.ARCHITECTURE,
        goal: str = "",
        constraints: Optional[list[str]] = None,
        cost_limit: Optional[float] = None,
        time_limit: Optional[int] = None,
        user_id: str = "default",
    ) -> AsyncIterator[DDOEvent]:
        """
        Run a complete discussion and yield events.

        This is the main entry point. It runs the discussion through all phases
        defined by the template, applying guards and handling failures.

        Args:
            topic: Main topic/question for discussion
            mode: Discussion mode (determines template)
            goal: Specific goal (defaults to solving the topic)
            constraints: List of constraints/limitations
            cost_limit: Budget in cents (overrides default)
            time_limit: Timeout in seconds (overrides default)
            user_id: User ID for audit trail

        Yields:
            DDOEvent subclasses as discussion progresses
        """
        # Generate discussion ID
        discussion_id = f"ddo-{uuid.uuid4().hex[:8]}"

        # Get template for mode
        try:
            template = get_template(mode)
        except ValueError as e:
            yield ErrorEvent(error=str(e))
            return

        # Create context
        context = DiscussionContext(
            id=discussion_id,
            mode=mode,
            topic=topic,
            goal=goal or f"Решить задачу: {topic}",
            constraints=constraints or [],
            cost_limit_cents=cost_limit or self.default_cost_limit,
            time_limit_seconds=time_limit or self.default_time_limit,
            max_responses=self.max_responses,
            user_id=user_id,
        )

        # Create FSM
        fsm = DiscussionFSM(context, template)

        _log.info(
            f"Starting discussion {discussion_id}: mode={mode.value}, "
            f"topic='{topic[:50]}...', phases={template.phase_count}"
        )

        # Yield initial progress
        yield ProgressEvent(
            current_phase=0,
            total_phases=template.phase_count + 1,  # +1 for synthesize
            message=f"Начинаем дискуссию: {template.name}",
            cost_cents=0.0,
            elapsed_seconds=0.0,
        )

        # Run through phases
        phase_index = 0
        for phase_config in template.phases:
            phase_index += 1

            # Check guards before starting phase
            guards_ok, guard_reason = fsm.check_guards()
            if not guards_ok:
                yield GuardFailEvent(
                    guard_name="pre_phase",
                    reason=guard_reason,
                    recoverable=False,
                )
                fsm.force_fail(guard_reason)
                yield CompletedEvent(context=context, success=False)
                return

            # Transition to phase
            if not fsm.can_transition(phase_config.phase):
                # Check if phase can be skipped
                if not phase_config.required and template.allow_phase_skip:
                    _log.info(f"Skipping optional phase: {phase_config.phase.value}")
                    continue

                yield ErrorEvent(
                    error=f"Cannot transition to {phase_config.phase.value}",
                    phase=context.current_phase,
                )
                fsm.force_fail(f"Invalid transition to {phase_config.phase.value}")
                yield CompletedEvent(context=context, success=False)
                return

            fsm.transition(phase_config.phase, f"Phase {phase_index} of {template.phase_count}")

            # Yield phase start event
            yield PhaseStartEvent(
                phase=phase_config.phase,
                agent=phase_config.agent,
            )

            # Execute phase
            async for event in self._execute_phase(
                phase_config, context, fsm, phase_index, template.phase_count
            ):
                yield event

                # Check if we got a fatal event
                if isinstance(event, ErrorEvent):
                    yield CompletedEvent(context=context, success=False)
                    return

            # Yield progress after phase
            yield ProgressEvent(
                current_phase=phase_index,
                total_phases=template.phase_count + 1,
                message=f"Завершена фаза: {phase_config.phase.display_name}",
                cost_cents=context.total_cost_cents,
                elapsed_seconds=context.elapsed_seconds,
            )

        # Run synthesis phase
        yield PhaseStartEvent(
            phase=DiscussionPhase.SYNTHESIZE,
            agent=template.synthesizer_agent,
        )

        async for event in self._execute_synthesis(context, template, fsm):
            yield event

        # Check if we need consensus
        if template.require_consensus:
            if not await self._check_consensus(context):
                # No consensus - might need another round
                context.consensus_reached = False
                fsm.transition(DiscussionPhase.ESCALATED, "No consensus reached")
                yield CompletedEvent(context=context, success=False)
                return

        # Success!
        fsm.transition(DiscussionPhase.DONE, "Discussion completed successfully")
        context.consensus_reached = True

        yield CompletedEvent(context=context, success=True)

    async def _execute_phase(
        self,
        config: PhaseConfig,
        context: DiscussionContext,
        fsm: DiscussionFSM,
        phase_index: int,
        total_phases: int,
    ) -> AsyncIterator[DDOEvent]:
        """
        Execute a single discussion phase.

        Args:
            config: Phase configuration
            context: Discussion context
            fsm: Finite state machine
            phase_index: Current phase index (1-based)
            total_phases: Total number of phases

        Yields:
            DDOEvent as phase executes
        """
        agent_key = config.agent
        agent = self.agents.get(agent_key)

        if not agent:
            yield ErrorEvent(
                error=f"Agent not available: {agent_key}",
                phase=config.phase,
            )
            fsm.force_fail(f"Agent {agent_key} not available")
            return

        if not agent.is_connected:
            yield ErrorEvent(
                error=f"Agent not connected: {agent_key} - {agent.error_message}",
                phase=config.phase,
            )
            fsm.force_fail(f"Agent {agent_key} not connected")
            return

        # Build prompt
        try:
            prompt = build_prompt(config.phase, context)
        except Exception as e:
            yield ErrorEvent(error=f"Failed to build prompt: {e}", phase=config.phase)
            fsm.force_fail(f"Prompt build failed: {e}")
            return

        # Check prompt security
        prompt_safe, prompt_reason = check_prompt_security(prompt)
        if not prompt_safe:
            yield GuardFailEvent(
                guard_name="prompt_security",
                reason=prompt_reason,
                recoverable=False,
            )
            fsm.force_fail(prompt_reason)
            return

        # Execute with retry
        response_content = ""
        last_error = None

        for attempt in range(config.retry_count + 1):
            try:
                # Send with timeout
                response_content = await asyncio.wait_for(
                    agent.ask_async(prompt),
                    timeout=config.timeout_seconds
                )

                # Check if response is an error
                if response_content.startswith("❌"):
                    if attempt < config.retry_count:
                        _log.warning(
                            f"Phase {config.phase.value} attempt {attempt + 1} failed, retrying..."
                        )
                        await asyncio.sleep(2 ** attempt)  # Exponential backoff
                        continue
                    last_error = response_content
                    break

                # Success
                break

            except asyncio.TimeoutError:
                last_error = f"Timeout after {config.timeout_seconds}s"
                if attempt < config.retry_count:
                    _log.warning(
                        f"Phase {config.phase.value} timeout, retry {attempt + 1}/{config.retry_count}"
                    )
                    continue

        # Check if we got a valid response
        if not response_content or response_content.startswith("❌"):
            yield ErrorEvent(
                error=last_error or response_content or "No response",
                phase=config.phase,
            )
            fsm.force_fail(f"Phase failed: {last_error or 'no response'}")
            return

        # Get token usage for cost tracking
        usage = agent.get_usage()
        tokens_used = usage.input_tokens + usage.output_tokens
        cost_cents = usage.total_cost_cents

        # Create response object
        response = AgentResponse(
            agent=agent_key,
            phase=config.phase,
            content=response_content,
            tokens_used=tokens_used,
            cost_cents=cost_cents - sum(r.cost_cents for r in context.responses),  # Delta
            confidence=self._extract_confidence(response_content),
            flags=self._extract_flags(response_content, config.phase),
        )

        # Add to context
        context.add_response(response)

        # Run guards on response
        guards_passed, issues = run_all_guards(response, context)
        if not guards_passed:
            yield GuardFailEvent(
                guard_name="response_quality",
                reason="; ".join(issues),
                recoverable=True,  # Might be fixable in REFINE phase
            )
            _log.warning(f"Guard issues for {config.phase.value}: {issues}")

        # Yield response event
        yield ResponseEvent(response=response)

    async def _execute_synthesis(
        self,
        context: DiscussionContext,
        template: DiscussionTemplate,
        fsm: DiscussionFSM,
    ) -> AsyncIterator[DDOEvent]:
        """
        Execute the synthesis phase.

        Args:
            context: Discussion context with all responses
            template: Discussion template
            fsm: Finite state machine

        Yields:
            DDOEvent as synthesis executes
        """
        # Transition to synthesize
        if not fsm.transition(DiscussionPhase.SYNTHESIZE, "Starting synthesis"):
            yield ErrorEvent(
                error="Cannot transition to SYNTHESIZE phase",
                phase=context.current_phase,
            )
            return

        # Get synthesizer agent
        agent_key = template.synthesizer_agent
        agent = self.agents.get(agent_key)

        if not agent or not agent.is_connected:
            yield ErrorEvent(
                error=f"Synthesizer agent {agent_key} not available",
                phase=DiscussionPhase.SYNTHESIZE,
            )
            fsm.force_fail(f"Synthesizer {agent_key} not available")
            return

        # Build synthesis prompt
        try:
            prompt = build_prompt(DiscussionPhase.SYNTHESIZE, context)
        except Exception as e:
            yield ErrorEvent(error=f"Failed to build synthesis prompt: {e}")
            fsm.force_fail(f"Synthesis prompt failed: {e}")
            return

        # Execute
        try:
            response_content = await asyncio.wait_for(
                agent.ask_async(prompt),
                timeout=180  # 3 minutes for synthesis
            )
        except asyncio.TimeoutError:
            yield ErrorEvent(
                error="Synthesis timeout",
                phase=DiscussionPhase.SYNTHESIZE,
            )
            fsm.force_fail("Synthesis timeout")
            return

        # Check for error response
        if response_content.startswith("❌"):
            yield ErrorEvent(
                error=response_content,
                phase=DiscussionPhase.SYNTHESIZE,
            )
            fsm.force_fail(f"Synthesis failed: {response_content}")
            return

        # Create response
        usage = agent.get_usage()
        response = AgentResponse(
            agent=agent_key,
            phase=DiscussionPhase.SYNTHESIZE,
            content=response_content,
            tokens_used=usage.input_tokens + usage.output_tokens,
            cost_cents=usage.total_cost_cents - sum(r.cost_cents for r in context.responses),
        )

        context.add_response(response)
        context.final_result = response_content

        yield ResponseEvent(response=response)

        yield ProgressEvent(
            current_phase=len(template.phases) + 1,
            total_phases=len(template.phases) + 1,
            message="Синтез завершён",
            cost_cents=context.total_cost_cents,
            elapsed_seconds=context.elapsed_seconds,
        )

    async def _check_consensus(self, context: DiscussionContext) -> bool:
        """
        Check if agents reached consensus.

        Returns True if all review phases approved.
        """
        # Check security review
        security_responses = context.get_responses_by_phase(DiscussionPhase.SECURITY_REVIEW)
        for resp in security_responses:
            if resp.is_rejected:
                return False
            if ResponseFlag.REJECTED in resp.flags:
                return False

        # Check code review
        code_review_responses = context.get_responses_by_phase(DiscussionPhase.CODE_REVIEW)
        for resp in code_review_responses:
            if resp.is_rejected:
                return False
            if ResponseFlag.REJECTED in resp.flags:
                return False

        # Check for conflicts
        conflict = detect_conflict(context.responses)
        if conflict:
            _log.warning(f"Consensus check failed: {conflict}")
            return False

        return True

    def _extract_confidence(self, content: str) -> float:
        """Extract confidence value from response content."""
        import re

        # Look for patterns like "Уверенность: 85%" or "confidence: 0.85"
        patterns = [
            r'[Уу]веренность[:\s]+(\d{1,3})%',
            r'[Cc]onfidence[:\s]+(\d{1,3})%',
            r'[Уу]веренность[:\s]+0?\.(\d{1,2})',
            r'[Cc]onfidence[:\s]+0?\.(\d{1,2})',
        ]

        for pattern in patterns:
            match = re.search(pattern, content)
            if match:
                value = match.group(1)
                if '.' in pattern:
                    return float(f"0.{value}")
                return float(value) / 100.0

        return 0.8  # Default confidence

    def _extract_flags(
        self, content: str, phase: DiscussionPhase
    ) -> list[ResponseFlag]:
        """Extract response flags from content."""
        flags = []
        content_lower = content.lower()

        # Approval/rejection
        if "✅ approved" in content_lower or "одобрено" in content_lower:
            flags.append(ResponseFlag.APPROVED)
        elif "❌ rejected" in content_lower or "отклонено" in content_lower:
            flags.append(ResponseFlag.REJECTED)

        # Other flags
        if "требуется уточнение" in content_lower or "needs clarification" in content_lower:
            flags.append(ResponseFlag.NEEDS_CLARIFICATION)

        if "риск" in content_lower and "critical" in content_lower:
            flags.append(ResponseFlag.RISK_DETECTED)

        # Low confidence
        confidence = self._extract_confidence(content)
        if confidence < 0.5:
            flags.append(ResponseFlag.LOW_CONFIDENCE)

        return flags


# === CONVENIENCE FUNCTION ===

async def run_discussion_stream(
    agents: dict,
    topic: str,
    mode: DiscussionMode = DiscussionMode.ARCHITECTURE,
    **kwargs,
) -> AsyncIterator[DDOEvent]:
    """
    Convenience function to run a discussion.

    Creates orchestrator and runs discussion in one call.

    Args:
        agents: Dict of agent instances
        topic: Discussion topic
        mode: Discussion mode
        **kwargs: Additional arguments for run_discussion

    Yields:
        DDOEvent as discussion progresses
    """
    orchestrator = DDOOrchestrator(agents)
    async for event in orchestrator.run_discussion(topic, mode, **kwargs):
        yield event


# === SYNC WRAPPER ===

def run_discussion_sync(
    agents: dict,
    topic: str,
    mode: DiscussionMode = DiscussionMode.ARCHITECTURE,
    callback=None,
    **kwargs,
) -> DiscussionContext:
    """
    Synchronous wrapper for run_discussion.

    Runs the async discussion in an event loop.
    Optionally calls callback for each event.

    Args:
        agents: Dict of agent instances
        topic: Discussion topic
        mode: Discussion mode
        callback: Optional callback(event) for each event
        **kwargs: Additional arguments

    Returns:
        Final DiscussionContext
    """
    async def _run():
        orchestrator = DDOOrchestrator(agents)
        final_context = None

        async for event in orchestrator.run_discussion(topic, mode, **kwargs):
            if callback:
                callback(event)
            if isinstance(event, CompletedEvent):
                final_context = event.context

        return final_context

    return asyncio.run(_run())
