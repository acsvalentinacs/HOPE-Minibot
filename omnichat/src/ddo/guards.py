# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-27T12:30:00Z
# Purpose: DDO Guards - Fail-closed quality and security checks
# === END SIGNATURE ===
"""
DDO Guards - Quality and Security Checks.

Implements fail-closed guards that run at each phase:
- Response quality validation
- Code syntax checking
- Conflict detection between agents
- Security pattern detection

Fail-Closed Principle:
    If any check is uncertain, it FAILS (not passes).
    Better to escalate than to let bad output through.
"""

from __future__ import annotations

import ast
import re
import logging
from typing import Optional

from .types import (
    AgentResponse,
    DiscussionPhase,
    DiscussionContext,
    ResponseFlag,
)

_log = logging.getLogger("ddo.guards")


# === RESPONSE QUALITY GUARDS ===

def check_response_quality(response: AgentResponse) -> tuple[bool, str]:
    """
    Validate response meets quality criteria.

    Checks:
    1. Minimum length
    2. Not an error response
    3. Contains expected markers for phase
    4. No obvious hallucinations

    Args:
        response: AgentResponse to validate

    Returns:
        (passed, reason) - If passed=False, reason explains why
    """
    content = response.content

    # Guard 1: Not empty or too short
    if not content:
        return False, "Empty response"

    stripped = content.strip()
    if len(stripped) < 50:
        return False, f"Response too short: {len(stripped)} chars"

    # Guard 2: Not an error response
    if content.startswith("❌"):
        return False, f"Error response: {content[:100]}"

    # Guard 3: Check for expected phase markers
    phase_markers = _get_phase_markers(response.phase)
    if phase_markers:
        found_count = sum(1 for m in phase_markers if m.lower() in content.lower())
        min_required = max(1, len(phase_markers) // 3)  # At least 1/3 of markers
        if found_count < min_required:
            return False, (
                f"Missing expected markers for {response.phase.value}. "
                f"Found {found_count}/{len(phase_markers)}, need {min_required}"
            )

    # Guard 4: Check for obvious hallucinations
    hallucination_patterns = [
        r"import\s+nonexistent",
        r"from\s+fake_module",
        r"pip\s+install\s+made_up_package",
        r"\[INSERT\s+.*\s+HERE\]",
        r"TODO:\s*implement",
        r"<placeholder>",
        r"\.\.\.\s*#\s*implementation",
    ]
    for pattern in hallucination_patterns:
        if re.search(pattern, content, re.IGNORECASE):
            return False, f"Possible hallucination detected: {pattern}"

    # Guard 5: Check confidence flag
    if response.confidence < 0.3:
        return False, f"Low confidence: {response.confidence}"

    return True, "OK"


def _get_phase_markers(phase: DiscussionPhase) -> list[str]:
    """Get expected content markers for each phase."""
    markers = {
        DiscussionPhase.ARCHITECT: [
            "Вариант", "вариант", "Option", "Решение",
            "Архитектур", "archit", "Рекоменд",
        ],
        DiscussionPhase.ANALYZE: [
            "Анализ", "анализ", "Analysis", "ТЗ",
            "Выбор", "Критери", "Требован",
        ],
        DiscussionPhase.IMPLEMENT: [
            "```python", "def ", "class ", "import ",
            "async def", "return ",
        ],
        DiscussionPhase.SECURITY_REVIEW: [
            "Security", "security", "Безопасност",
            "Audit", "Finding", "Verdict", "APPROVED", "REJECTED",
        ],
        DiscussionPhase.CODE_REVIEW: [
            "Review", "review", "Issue", "Quality",
            "APPROVED", "REJECTED", "Fix", "исправ",
        ],
        DiscussionPhase.REFINE: [
            "```python", "изменен", "исправлен",
            "Внесён", "Updated", "Fixed",
        ],
        DiscussionPhase.SYNTHESIZE: [
            "Итог", "Result", "Финальн", "Заключен",
        ],
    }
    return markers.get(phase, [])


# === CODE QUALITY GUARDS ===

def check_code_compiles(content: str) -> tuple[bool, str, list[str]]:
    """
    Check if Python code blocks in content compile.

    Args:
        content: Full response content

    Returns:
        (compiles, error_message, code_blocks)
    """
    # Extract Python code blocks
    code_blocks = re.findall(
        r'```python\n(.*?)```',
        content,
        re.DOTALL | re.IGNORECASE
    )

    if not code_blocks:
        return True, "No code blocks to check", []

    errors = []
    for i, block in enumerate(code_blocks, 1):
        # Clean up block
        block = block.strip()
        if not block:
            continue

        try:
            ast.parse(block)
        except SyntaxError as e:
            errors.append(f"Block {i} line {e.lineno}: {e.msg}")

    if errors:
        return False, f"Syntax errors: {'; '.join(errors)}", code_blocks

    return True, "OK", code_blocks


def check_code_quality(code: str) -> tuple[bool, str, list[str]]:
    """
    Check code quality beyond syntax.

    Checks:
    1. No bare except clauses
    2. No mutable default arguments
    3. Has docstrings for functions/classes
    4. No hardcoded secrets patterns

    Args:
        code: Python code string

    Returns:
        (passed, summary, issues_list)
    """
    issues = []

    try:
        tree = ast.parse(code)
    except SyntaxError:
        return False, "Cannot parse code", ["Syntax error"]

    # Check 1: Bare except
    for node in ast.walk(tree):
        if isinstance(node, ast.ExceptHandler):
            if node.type is None:
                issues.append(
                    f"Line {node.lineno}: Bare 'except:' clause - "
                    "should catch specific exceptions"
                )

    # Check 2: Mutable default arguments
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for default in node.args.defaults + node.args.kw_defaults:
                if default and isinstance(default, (ast.List, ast.Dict, ast.Set)):
                    issues.append(
                        f"Line {node.lineno}: Function '{node.name}' has mutable "
                        "default argument"
                    )

    # Check 3: Missing docstrings
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if not ast.get_docstring(node):
                # Only warn for public functions (not starting with _)
                if not node.name.startswith('_'):
                    issues.append(
                        f"Line {node.lineno}: '{node.name}' missing docstring"
                    )

    # Check 4: Hardcoded secrets patterns
    secret_patterns = [
        (r'["\']sk-[a-zA-Z0-9]{20,}["\']', "OpenAI API key"),
        (r'["\']AIza[a-zA-Z0-9_-]{30,}["\']', "Google API key"),
        (r'["\']ghp_[a-zA-Z0-9]{30,}["\']', "GitHub token"),
        (r'password\s*=\s*["\'][^"\']+["\']', "Hardcoded password"),
        (r'api_key\s*=\s*["\'][a-zA-Z0-9]{16,}["\']', "Hardcoded API key"),
    ]
    for pattern, description in secret_patterns:
        if re.search(pattern, code):
            issues.append(f"SECURITY: Possible {description} detected")

    if issues:
        critical = [i for i in issues if i.startswith("SECURITY")]
        if critical:
            return False, f"Security issues found: {len(critical)}", issues
        return True, f"Quality issues found: {len(issues)}", issues

    return True, "OK", []


# === CONFLICT DETECTION ===

def detect_conflict(responses: list[AgentResponse]) -> Optional[str]:
    """
    Detect conflicts between agent responses.

    Checks:
    1. Explicit rejection markers
    2. Contradicting verdicts
    3. Strong disagreement language

    Args:
        responses: List of recent responses to check

    Returns:
        Conflict description or None if no conflict
    """
    if len(responses) < 2:
        return None

    # Check 1: Explicit rejection after approval
    verdicts = []
    for resp in responses:
        content_lower = resp.content.lower()
        if "✅ approved" in content_lower or "одобрено" in content_lower:
            verdicts.append((resp.agent, "approved", resp.phase))
        elif "❌ rejected" in content_lower or "отклонено" in content_lower:
            verdicts.append((resp.agent, "rejected", resp.phase))

    if len(verdicts) >= 2:
        # Check for approval followed by rejection
        for i in range(len(verdicts) - 1):
            if verdicts[i][1] == "approved" and verdicts[i + 1][1] == "rejected":
                return (
                    f"Conflict: {verdicts[i][0]} approved at {verdicts[i][2].value}, "
                    f"but {verdicts[i + 1][0]} rejected at {verdicts[i + 1][2].value}"
                )

    # Check 2: Strong disagreement language
    disagreement_patterns = [
        r"категорически\s+не\s+согласен",
        r"это\s+неправильно",
        r"критическая\s+ошибка",
        r"fundamental(?:ly)?\s+(?:wrong|flawed)",
        r"cannot\s+accept",
        r"must\s+reject",
    ]

    for resp in responses:
        for pattern in disagreement_patterns:
            if re.search(pattern, resp.content, re.IGNORECASE):
                return f"{resp.agent} strongly disagrees: matches '{pattern}'"

    # Check 3: Security rejection
    for resp in responses:
        if resp.phase == DiscussionPhase.SECURITY_REVIEW:
            if "❌ rejected" in resp.content.lower():
                return f"Security review rejected by {resp.agent}"
            if "critical" in resp.content.lower() and "finding" in resp.content.lower():
                # Check if critical finding was addressed
                later_responses = [
                    r for r in responses
                    if r.timestamp > resp.timestamp
                ]
                addressed = any(
                    "исправлен" in r.content.lower() or "fixed" in r.content.lower()
                    for r in later_responses
                )
                if not addressed:
                    return "Critical security finding not addressed"

    return None


# === CONTEXT VALIDATION ===

def validate_context(context: DiscussionContext) -> tuple[bool, list[str]]:
    """
    Validate discussion context integrity.

    Checks:
    1. Required fields present
    2. Responses in valid order
    3. Cost tracking consistency

    Args:
        context: DiscussionContext to validate

    Returns:
        (valid, issues_list)
    """
    issues = []

    # Check 1: Required fields
    if not context.id:
        issues.append("Missing discussion ID")
    if not context.topic:
        issues.append("Missing topic")
    if not context.goal:
        issues.append("Missing goal")

    # Check 2: Cost consistency
    calculated_cost = sum(r.cost_cents for r in context.responses)
    if abs(calculated_cost - context.total_cost_cents) > 0.01:
        issues.append(
            f"Cost mismatch: tracked={context.total_cost_cents:.2f}, "
            f"calculated={calculated_cost:.2f}"
        )

    # Check 3: Timestamps in order
    for i in range(1, len(context.responses)):
        if context.responses[i].timestamp < context.responses[i - 1].timestamp:
            issues.append(
                f"Response {i} has earlier timestamp than response {i - 1}"
            )

    # Check 4: Terminal state consistency
    if context.is_terminal:
        if context.ended_at is None:
            issues.append("Terminal state but ended_at is None")
        if context.current_phase == DiscussionPhase.DONE and not context.consensus_reached:
            issues.append("DONE state but consensus_reached is False")

    return len(issues) == 0, issues


# === PROMPT SECURITY ===

def check_prompt_security(prompt: str) -> tuple[bool, str]:
    """
    Check prompt for security issues before sending.

    Checks:
    1. No injection attempts
    2. No secrets in prompt
    3. Reasonable length

    Args:
        prompt: Prompt text to check

    Returns:
        (safe, reason)
    """
    # Check 1: Prompt injection patterns
    injection_patterns = [
        r"ignore\s+(?:all\s+)?(?:previous|above)\s+instructions",
        r"disregard\s+(?:all\s+)?(?:previous|above)",
        r"you\s+are\s+now\s+(?:a\s+)?DAN",
        r"jailbreak",
        r"<\|(?:im_)?start\|>",
        r"<\|(?:im_)?end\|>",
    ]
    for pattern in injection_patterns:
        if re.search(pattern, prompt, re.IGNORECASE):
            return False, f"Prompt injection pattern detected: {pattern}"

    # Check 2: Secrets (using patterns from security.py)
    secret_patterns = [
        r"sk-[A-Za-z0-9]{16,}",
        r"AIza[0-9A-Za-z_\-]{20,}",
        r"sk-ant-[A-Za-z0-9_\-]{16,}",
        r"ghp_[A-Za-z0-9]{36,}",
        r"bot[0-9]{8,}:[A-Za-z0-9_\-]{30,}",
    ]
    for pattern in secret_patterns:
        if re.search(pattern, prompt):
            return False, "Secret detected in prompt"

    # Check 3: Length
    if len(prompt) > 50000:
        return False, f"Prompt too long: {len(prompt)} chars"

    return True, "OK"


# === COMBINED GUARD RUNNER ===

def run_all_guards(
    response: AgentResponse,
    context: DiscussionContext,
) -> tuple[bool, list[str]]:
    """
    Run all applicable guards for a response.

    Args:
        response: Response to check
        context: Current discussion context

    Returns:
        (all_passed, issues_list)
    """
    all_issues = []

    # Quality check
    quality_ok, quality_reason = check_response_quality(response)
    if not quality_ok:
        all_issues.append(f"Quality: {quality_reason}")

    # Code check (if response has code)
    if "```python" in response.content:
        code_ok, code_reason, _ = check_code_compiles(response.content)
        if not code_ok:
            all_issues.append(f"Code: {code_reason}")

    # Conflict check
    if len(context.responses) >= 1:
        recent = context.responses[-3:] + [response]
        conflict = detect_conflict(recent)
        if conflict:
            all_issues.append(f"Conflict: {conflict}")

    # Context validation
    ctx_ok, ctx_issues = validate_context(context)
    if not ctx_ok:
        all_issues.extend([f"Context: {i}" for i in ctx_issues])

    return len(all_issues) == 0, all_issues
