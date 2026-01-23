"""
Unit tests for IPC Agent contract.

Tests:
1. Canonical JSON -> stable sha256
2. Fail-closed: bad JSON / missing fields / wrong recipient / id mismatch -> deadletter
3. Backpressure: file >64KB -> deadletter
4. ACK flow: pending_acks decreases after receiving ack
5. Resend: after ACK_TIMEOUT message is re-delivered to peer_inbox

Run: pytest tests/test_ipc_agent.py -v
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

import pytest

# Patch paths before importing ipc_agent
TEST_DIR = Path(tempfile.mkdtemp(prefix="ipc_test_"))


@pytest.fixture(scope="function", autouse=True)
def setup_test_dirs():
    """Setup and cleanup test directories for each test."""
    # Create fresh test dirs
    test_base = Path(tempfile.mkdtemp(prefix="ipc_test_"))

    with patch.multiple(
        "core.ipc_agent",
        BASE_DIR=test_base,
        IPC_DIR=test_base / "ipc",
        CLAUDE_INBOX=test_base / "ipc" / "claude_inbox",
        CLAUDE_OUTBOX=test_base / "ipc" / "claude_outbox",
        GPT_INBOX=test_base / "ipc" / "gpt_inbox",
        GPT_OUTBOX=test_base / "ipc" / "gpt_outbox",
        DEADLETTER=test_base / "ipc" / "deadletter",
        LOGS_DIR=test_base / "logs",
        IPC_LOG_FILE=test_base / "logs" / "ipc.log",
    ):
        # Import after patching
        from core import ipc_agent

        # Create folders
        for d in [
            test_base / "ipc" / "claude_inbox",
            test_base / "ipc" / "claude_outbox",
            test_base / "ipc" / "gpt_inbox",
            test_base / "ipc" / "gpt_outbox",
            test_base / "ipc" / "deadletter",
            test_base / "logs",
            test_base / "state",
        ]:
            d.mkdir(parents=True, exist_ok=True)

        yield {
            "base": test_base,
            "module": ipc_agent,
        }

    # Cleanup
    shutil.rmtree(test_base, ignore_errors=True)


class TestCanonicalJSON:
    """Test 1: Canonical JSON -> stable sha256."""

    def test_canonical_json_sorted_keys(self, setup_test_dirs):
        """Canonical JSON must have sorted keys."""
        m = setup_test_dirs["module"]

        obj1 = {"z": 1, "a": 2, "m": 3}
        obj2 = {"a": 2, "m": 3, "z": 1}

        assert m._json_canonical(obj1) == m._json_canonical(obj2)

    def test_canonical_json_no_spaces(self, setup_test_dirs):
        """Canonical JSON has no spaces."""
        m = setup_test_dirs["module"]

        obj = {"key": "value", "num": 123}
        result = m._json_canonical(obj)

        assert " " not in result
        assert '{"key":"value","num":123}' == result

    def test_stable_sha256(self, setup_test_dirs):
        """Same input -> same sha256 ID."""
        m = setup_test_dirs["module"]

        fields = {
            "from": "gpt-5.2",
            "to": "claude",
            "timestamp": 1737042000.123,
            "type": "task",
            "payload": {"task_type": "math", "expression": "2+2"},
        }

        id1 = m._generate_id_from_message_fields(fields)
        id2 = m._generate_id_from_message_fields(fields)

        assert id1 == id2
        assert id1.startswith("sha256:")
        assert len(id1) == 7 + 64  # "sha256:" + 64 hex chars

    def test_different_fields_different_id(self, setup_test_dirs):
        """Different fields -> different sha256 ID."""
        m = setup_test_dirs["module"]

        fields1 = {"a": 1, "b": 2}
        fields2 = {"a": 1, "b": 3}

        id1 = m._generate_id_from_message_fields(fields1)
        id2 = m._generate_id_from_message_fields(fields2)

        assert id1 != id2


class TestFailClosed:
    """Test 2: Fail-closed - bad input -> deadletter."""

    def test_bad_json_to_deadletter(self, setup_test_dirs):
        """Invalid JSON -> deadletter."""
        base = setup_test_dirs["base"]
        m = setup_test_dirs["module"]

        # Patch module paths for this test
        m.CLAUDE_INBOX = base / "ipc" / "claude_inbox"
        m.DEADLETTER = base / "ipc" / "deadletter"

        # Write bad JSON to inbox
        bad_file = m.CLAUDE_INBOX / "bad_json.json"
        bad_file.write_text("{invalid json", encoding="utf-8")

        # Create agent and parse
        agent = m.ClaudeAgent()
        agent._inbox = m.CLAUDE_INBOX
        result = agent._parse_message(bad_file)

        assert result is None
        # File should be moved to deadletter
        assert len(list(m.DEADLETTER.glob("*bad_json.json"))) == 1

    def test_missing_fields_to_deadletter(self, setup_test_dirs):
        """Missing required fields -> deadletter."""
        base = setup_test_dirs["base"]
        m = setup_test_dirs["module"]

        m.CLAUDE_INBOX = base / "ipc" / "claude_inbox"
        m.DEADLETTER = base / "ipc" / "deadletter"

        # Message without 'type' field
        incomplete = {
            "id": "sha256:abc",
            "from": "gpt-5.2",
            "to": "claude",
            "timestamp": 123.0,
            "payload": {},
        }

        bad_file = m.CLAUDE_INBOX / "missing_type.json"
        bad_file.write_text(json.dumps(incomplete), encoding="utf-8")

        agent = m.ClaudeAgent()
        agent._inbox = m.CLAUDE_INBOX
        result = agent._parse_message(bad_file)

        assert result is None
        assert len(list(m.DEADLETTER.glob("*missing_type.json"))) == 1

    def test_wrong_recipient_to_deadletter(self, setup_test_dirs):
        """Wrong recipient -> deadletter."""
        base = setup_test_dirs["base"]
        m = setup_test_dirs["module"]

        m.CLAUDE_INBOX = base / "ipc" / "claude_inbox"
        m.DEADLETTER = base / "ipc" / "deadletter"

        # Build valid message but to wrong recipient
        fields = {
            "from": "gpt-5.2",
            "to": "someone_else",  # Wrong recipient!
            "timestamp": 123.0,
            "type": "task",
            "payload": {},
        }
        msg_id = m._generate_id_from_message_fields(fields)
        msg = dict(fields, id=msg_id)

        bad_file = m.CLAUDE_INBOX / "wrong_recipient.json"
        bad_file.write_text(json.dumps(msg), encoding="utf-8")

        agent = m.ClaudeAgent()
        agent._inbox = m.CLAUDE_INBOX
        result = agent._parse_message(bad_file)

        assert result is None
        assert len(list(m.DEADLETTER.glob("*wrong_recipient.json"))) == 1

    def test_id_mismatch_to_deadletter(self, setup_test_dirs):
        """ID mismatch -> deadletter."""
        base = setup_test_dirs["base"]
        m = setup_test_dirs["module"]

        m.CLAUDE_INBOX = base / "ipc" / "claude_inbox"
        m.DEADLETTER = base / "ipc" / "deadletter"

        # Message with tampered ID
        fields = {
            "from": "gpt-5.2",
            "to": "claude",
            "timestamp": 123.0,
            "type": "task",
            "payload": {},
        }
        msg = dict(fields, id="sha256:0000000000000000000000000000000000000000000000000000000000000000")

        bad_file = m.CLAUDE_INBOX / "id_mismatch.json"
        bad_file.write_text(json.dumps(msg), encoding="utf-8")

        agent = m.ClaudeAgent()
        agent._inbox = m.CLAUDE_INBOX
        result = agent._parse_message(bad_file)

        assert result is None
        assert len(list(m.DEADLETTER.glob("*id_mismatch.json"))) == 1


class TestBackpressure:
    """Test 3: Backpressure - oversized -> deadletter."""

    def test_oversized_file_to_deadletter(self, setup_test_dirs):
        """File >64KB -> deadletter."""
        base = setup_test_dirs["base"]
        m = setup_test_dirs["module"]

        m.CLAUDE_INBOX = base / "ipc" / "claude_inbox"
        m.DEADLETTER = base / "ipc" / "deadletter"

        # Create file > 64KB
        big_payload = "x" * (65 * 1024)
        fields = {
            "from": "gpt-5.2",
            "to": "claude",
            "timestamp": 123.0,
            "type": "task",
            "payload": {"data": big_payload},
        }
        msg_id = m._generate_id_from_message_fields(fields)
        msg = dict(fields, id=msg_id)

        big_file = m.CLAUDE_INBOX / "too_big.json"
        big_file.write_text(json.dumps(msg), encoding="utf-8")

        assert big_file.stat().st_size > 64 * 1024

        agent = m.ClaudeAgent()
        agent._inbox = m.CLAUDE_INBOX
        result = agent._parse_message(big_file)

        assert result is None
        assert len(list(m.DEADLETTER.glob("*too_big.json"))) == 1


class TestACKFlow:
    """Test 4: ACK flow - pending_acks decreases after receiving ack."""

    def test_pending_acks_decreases_on_ack(self, setup_test_dirs):
        """pending_acks count decreases when ACK is received."""
        base = setup_test_dirs["base"]
        m = setup_test_dirs["module"]

        # Patch module paths
        m.BASE_DIR = base
        m.CLAUDE_INBOX = base / "ipc" / "claude_inbox"
        m.CLAUDE_OUTBOX = base / "ipc" / "claude_outbox"
        m.GPT_INBOX = base / "ipc" / "gpt_inbox"
        m.DEADLETTER = base / "ipc" / "deadletter"

        agent = m.ClaudeAgent()
        agent._inbox = m.CLAUDE_INBOX
        agent._outbox = m.CLAUDE_OUTBOX
        agent._peer_inbox = m.GPT_INBOX

        # Agent sends a response (which creates pending_ack)
        sent_id = agent.send_response("sha256:test_task_id", {"result": 42})

        assert sent_id in agent._pending_acks
        initial_pending = len(agent._pending_acks)

        # Now simulate receiving ACK for that message
        ack_fields = {
            "from": "gpt-5.2",
            "to": "claude",
            "timestamp": time.time(),
            "type": "ack",
            "payload": {"acked": sent_id},
            "reply_to": sent_id,
        }
        ack_id = m._generate_id_from_message_fields(ack_fields)
        ack_msg = dict(ack_fields, id=ack_id)

        ack_file = m.CLAUDE_INBOX / "ack_message.json"
        ack_file.write_text(json.dumps(ack_msg), encoding="utf-8")

        # Process cycle
        agent.process_cycle()

        # pending_acks should have decreased
        assert sent_id not in agent._pending_acks
        assert len(agent._pending_acks) < initial_pending


class TestResend:
    """Test 5: Resend - after ACK_TIMEOUT message is re-delivered."""

    def test_resend_after_timeout(self, setup_test_dirs):
        """Message is resent after ACK_TIMEOUT."""
        base = setup_test_dirs["base"]
        m = setup_test_dirs["module"]

        # Use very short timeout for testing
        original_timeout = m.ACK_TIMEOUT_SEC
        m.ACK_TIMEOUT_SEC = 0.1  # 100ms
        m.RESEND_MIN_INTERVAL_SEC = 0.05

        try:
            m.BASE_DIR = base
            m.CLAUDE_INBOX = base / "ipc" / "claude_inbox"
            m.CLAUDE_OUTBOX = base / "ipc" / "claude_outbox"
            m.GPT_INBOX = base / "ipc" / "gpt_inbox"
            m.DEADLETTER = base / "ipc" / "deadletter"

            agent = m.ClaudeAgent()
            agent._inbox = m.CLAUDE_INBOX
            agent._outbox = m.CLAUDE_OUTBOX
            agent._peer_inbox = m.GPT_INBOX

            # Send a response
            sent_id = agent.send_response("sha256:original_task", {"result": 100})

            # Initial file count in peer inbox
            initial_files = list(m.GPT_INBOX.glob("*.json"))

            # Wait for timeout
            time.sleep(0.2)

            # Resend should happen
            resent = agent._resend_unacked(time.time())

            assert resent >= 1
            # Message should still be in pending_acks (no ACK received)
            assert sent_id in agent._pending_acks

        finally:
            m.ACK_TIMEOUT_SEC = original_timeout
            m.RESEND_MIN_INTERVAL_SEC = 5.0


class TestSafeEval:
    """Test safe math expression evaluator."""

    def test_basic_operations(self, setup_test_dirs):
        """Basic math operations work."""
        m = setup_test_dirs["module"]

        assert m._safe_eval_expr("2+2") == 4
        assert m._safe_eval_expr("10-3") == 7
        assert m._safe_eval_expr("4*5") == 20
        assert m._safe_eval_expr("15/3") == 5
        assert m._safe_eval_expr("2**3") == 8

    def test_parentheses(self, setup_test_dirs):
        """Parentheses work correctly."""
        m = setup_test_dirs["module"]

        assert m._safe_eval_expr("(2+3)*4") == 20
        assert m._safe_eval_expr("2*(3+4)") == 14

    def test_unary_minus(self, setup_test_dirs):
        """Unary minus works."""
        m = setup_test_dirs["module"]

        assert m._safe_eval_expr("-5+10") == 5
        assert m._safe_eval_expr("10+-5") == 5

    def test_rejects_dangerous_code(self, setup_test_dirs):
        """Dangerous expressions are rejected."""
        m = setup_test_dirs["module"]

        with pytest.raises(ValueError):
            m._safe_eval_expr("__import__('os')")

        with pytest.raises(ValueError):
            m._safe_eval_expr("open('file.txt')")

        with pytest.raises(ValueError):
            m._safe_eval_expr("eval('1+1')")

    def test_division_by_zero(self, setup_test_dirs):
        """Division by zero raises error."""
        m = setup_test_dirs["module"]

        with pytest.raises(ValueError, match="division by zero"):
            m._safe_eval_expr("1/0")

    def test_too_long_expression(self, setup_test_dirs):
        """Expression too long raises error."""
        m = setup_test_dirs["module"]

        long_expr = "1+" * 100 + "1"
        with pytest.raises(ValueError, match="too long"):
            m._safe_eval_expr(long_expr)


class TestAtomicWrite:
    """Test atomic write functionality."""

    def test_atomic_write_creates_file(self, setup_test_dirs):
        """Atomic write creates file with correct content."""
        base = setup_test_dirs["base"]
        m = setup_test_dirs["module"]

        test_file = base / "test_atomic.json"
        content = '{"test": "data"}'

        m._atomic_write(test_file, content)

        assert test_file.exists()
        assert test_file.read_text(encoding="utf-8") == content

    def test_atomic_write_no_temp_leftover(self, setup_test_dirs):
        """Atomic write doesn't leave temp files."""
        base = setup_test_dirs["base"]
        m = setup_test_dirs["module"]

        test_file = base / "test_atomic2.json"
        m._atomic_write(test_file, "content")

        tmp_files = list(base.glob("*.tmp"))
        assert len(tmp_files) == 0


class TestMathHandler:
    """Test math task handler."""

    def test_math_handler_success(self, setup_test_dirs):
        """Math handler returns correct result."""
        base = setup_test_dirs["base"]
        m = setup_test_dirs["module"]

        m.BASE_DIR = base
        m.CLAUDE_INBOX = base / "ipc" / "claude_inbox"
        m.CLAUDE_OUTBOX = base / "ipc" / "claude_outbox"
        m.GPT_INBOX = base / "ipc" / "gpt_inbox"

        agent = m.ClaudeAgent()

        result = agent._handle_math({"expression": "10+5*2"})

        assert result["result"] == 20
        assert result["expression"] == "10+5*2"

    def test_math_handler_error(self, setup_test_dirs):
        """Math handler returns error for invalid expression."""
        base = setup_test_dirs["base"]
        m = setup_test_dirs["module"]

        m.BASE_DIR = base

        agent = m.ClaudeAgent()

        result = agent._handle_math({"expression": "invalid"})

        assert "error" in result


class TestFullCycle:
    """Test complete message processing cycle."""

    def test_task_to_response_cycle(self, setup_test_dirs):
        """Complete cycle: task in -> response out."""
        base = setup_test_dirs["base"]
        m = setup_test_dirs["module"]

        m.BASE_DIR = base
        m.CLAUDE_INBOX = base / "ipc" / "claude_inbox"
        m.CLAUDE_OUTBOX = base / "ipc" / "claude_outbox"
        m.GPT_INBOX = base / "ipc" / "gpt_inbox"
        m.DEADLETTER = base / "ipc" / "deadletter"

        # Create valid task message
        fields = {
            "from": "gpt-5.2",
            "to": "claude",
            "timestamp": time.time(),
            "type": "task",
            "payload": {"task_type": "math", "expression": "7*6"},
        }
        msg_id = m._generate_id_from_message_fields(fields)
        msg = dict(fields, id=msg_id)

        task_file = m.CLAUDE_INBOX / "task.json"
        task_file.write_text(json.dumps(msg), encoding="utf-8")

        # Create agent and process
        agent = m.ClaudeAgent()
        agent._inbox = m.CLAUDE_INBOX
        agent._outbox = m.CLAUDE_OUTBOX
        agent._peer_inbox = m.GPT_INBOX

        cycle_result = agent.process_cycle()

        # Task should be processed
        assert cycle_result["processed"] == 1

        # Response should be in outbox
        outbox_files = list(m.CLAUDE_OUTBOX.glob("*.json"))
        assert len(outbox_files) >= 1

        # Response should be delivered to peer inbox
        peer_files = list(m.GPT_INBOX.glob("*.json"))
        assert len(peer_files) >= 1

        # Check response content
        for f in peer_files:
            data = json.loads(f.read_text(encoding="utf-8"))
            if data.get("type") == "response":
                assert data["payload"]["result"] == 42
                assert data["reply_to"] == msg_id


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
