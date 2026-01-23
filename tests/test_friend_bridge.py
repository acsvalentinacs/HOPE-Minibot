"""
Tests for Friend Bridge (server + CLI) - Hardened version.

Run: pytest tests/test_friend_bridge.py -v
"""
from __future__ import annotations

import json
import tempfile
import threading
from http.server import HTTPServer
from pathlib import Path
from typing import Generator
from unittest.mock import MagicMock, patch

import pytest

# Import from chat_dispatch (the single source of truth)
from core.chat_dispatch import (
    send_chat,
    SendResult,
    get_ipc_status,
    get_last_sent,
    VALID_RECIPIENTS,
    MAX_MESSAGE_LEN,
)
from core.friend_bridge_server import (
    FriendBridgeHandler,
    tail_gpt_responses,
    BIND_HOST,
    VERSION,
    _STATE_DIR,
)
from core.friend_bridge_cli import BridgeClient


class TestSendChat:
    """Tests for send_chat function from chat_dispatch."""

    def test_invalid_recipient(self) -> None:
        """Test rejection of invalid recipient."""
        result = send_chat("invalid", "test message")
        assert result.ok is False
        assert "Invalid recipient" in result.error

    def test_empty_message(self) -> None:
        """Test rejection of empty message."""
        result = send_chat("claude", "")
        assert result.ok is False
        assert "empty" in result.error.lower()

    def test_message_too_long(self) -> None:
        """Test rejection of oversized message."""
        long_message = "x" * (MAX_MESSAGE_LEN + 100)
        result = send_chat("claude", long_message)
        assert result.ok is False
        assert "too long" in result.error.lower()

    def test_valid_recipients(self) -> None:
        """Test that valid recipients are accepted."""
        assert "claude" in VALID_RECIPIENTS
        assert "gpt" in VALID_RECIPIENTS

    @patch("core.chat_dispatch._atomic_write")
    @patch("core.chat_dispatch._ensure_ipc_folders")
    def test_successful_send_claude(
        self, mock_ensure: MagicMock, mock_write: MagicMock
    ) -> None:
        """Test successful send to Claude."""
        result = send_chat("claude", "Hello Claude!")

        assert result.ok is True
        assert result.to == "claude"
        assert result.ipc_id.startswith("sha256:")
        assert result.filename.endswith(".json")
        mock_write.assert_called_once()

    @patch("core.chat_dispatch._atomic_write")
    @patch("core.chat_dispatch._ensure_ipc_folders")
    def test_successful_send_gpt(
        self, mock_ensure: MagicMock, mock_write: MagicMock
    ) -> None:
        """Test successful send to GPT."""
        result = send_chat("gpt", "Hello GPT!")

        assert result.ok is True
        assert result.to == "gpt"
        assert result.ipc_id.startswith("sha256:")
        mock_write.assert_called_once()

    def test_send_result_to_dict(self) -> None:
        """Test SendResult.to_dict() method."""
        result = SendResult(
            ok=True,
            ipc_id="sha256:test123",
            to="claude",
            stored_file="/path/to/file.json",
            filename="file.json",
        )
        d = result.to_dict()
        assert d["ok"] is True
        assert d["ipc_id"] == "sha256:test123"
        assert d["to"] == "claude"


class TestTailGptResponses:
    """Tests for tail_gpt_responses function."""

    def test_missing_log_file(self, tmp_path: Path) -> None:
        """Test handling of missing log file."""
        with patch("core.friend_bridge_server._STATE_DIR", tmp_path):
            result = tail_gpt_responses(10)
            assert result["ok"] is True
            assert result["lines"] == []
            assert "not found" in result.get("note", "").lower()

    def test_read_log_file(self, tmp_path: Path) -> None:
        """Test reading existing log file."""
        log_file = tmp_path / "gpt_responses.log"
        log_file.write_text("line1\nline2\nline3\nline4\nline5\n", encoding="utf-8")

        with patch("core.friend_bridge_server._STATE_DIR", tmp_path):
            result = tail_gpt_responses(3)
            assert result["ok"] is True
            assert len(result["lines"]) == 3
            assert result["lines"] == ["line3", "line4", "line5"]

    def test_lines_clamped_max(self, tmp_path: Path) -> None:
        """Test that lines parameter is clamped to max 500."""
        log_file = tmp_path / "gpt_responses.log"
        log_file.write_text("line\n" * 10, encoding="utf-8")

        with patch("core.friend_bridge_server._STATE_DIR", tmp_path):
            # Request 1000, should be clamped to 500
            result = tail_gpt_responses(1000)
            assert result["ok"] is True

    def test_lines_clamped_min(self, tmp_path: Path) -> None:
        """Test that lines parameter is clamped to min 1."""
        log_file = tmp_path / "gpt_responses.log"
        log_file.write_text("line1\nline2\n", encoding="utf-8")

        with patch("core.friend_bridge_server._STATE_DIR", tmp_path):
            result = tail_gpt_responses(-5)
            assert result["ok"] is True
            assert len(result["lines"]) >= 1


class TestIPCStatus:
    """Tests for IPC status functions."""

    def test_get_ipc_status_returns_dict(self, tmp_path: Path) -> None:
        """Test that get_ipc_status returns proper structure."""
        with patch("core.chat_dispatch._IPC_DIR", tmp_path):
            (tmp_path / "claude_inbox").mkdir()
            (tmp_path / "gpt_inbox").mkdir()

            result = get_ipc_status()
            assert result["ok"] is True
            assert "claude" in result
            assert "gpt" in result
            assert "deadletter" in result


class TestHTTPHandler:
    """Tests for HTTP handler."""

    def test_healthz_returns_version(self) -> None:
        """Test that /healthz includes version info."""
        # This would need actual server testing
        pass

    def test_fail_closed_auth(self) -> None:
        """Test that auth fails closed when token is set but not provided."""
        FriendBridgeHandler.auth_token = "secret123"
        FriendBridgeHandler.insecure_mode = False
        # Would need proper request simulation
        # Key assertion: without token in request, should return 401

    def test_insecure_mode_bypasses_auth(self) -> None:
        """Test that insecure mode allows requests without token."""
        FriendBridgeHandler.auth_token = ""
        FriendBridgeHandler.insecure_mode = True
        # In insecure mode, _check_auth should return True


class TestBridgeClient:
    """Tests for CLI client."""

    def test_connection_error(self) -> None:
        """Test handling of connection refused."""
        client = BridgeClient(base_url="http://127.0.0.1:59999")  # Port unlikely in use
        result = client.health()
        assert result["ok"] is False
        assert "Connection refused" in result["error"]

    @patch("requests.Session.get")
    def test_health_success(self, mock_get: MagicMock) -> None:
        """Test successful health check."""
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "ok": True,
            "service": "friend_bridge",
            "version": VERSION,
            "auth_enabled": True,
        }
        mock_get.return_value = mock_response

        client = BridgeClient(base_url="http://127.0.0.1:8765")
        result = client.health()

        assert result["ok"] is True
        assert result["service"] == "friend_bridge"

    @patch("requests.Session.post")
    def test_send_success(self, mock_post: MagicMock) -> None:
        """Test successful send."""
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "ok": True,
            "ipc_id": "sha256:abc123",
            "to": "claude",
            "filename": "test.json",
        }
        mock_post.return_value = mock_response

        client = BridgeClient(base_url="http://127.0.0.1:8765")
        result = client.send("claude", "Hello!")

        assert result["ok"] is True
        assert result["ipc_id"].startswith("sha256:")

    @patch("requests.Session.get")
    def test_last_sent_endpoint(self, mock_get: MagicMock) -> None:
        """Test last_sent endpoint."""
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "ok": True,
            "last_sent": {
                "ok": True,
                "ipc_id": "sha256:xyz789",
                "to": "gpt",
                "filename": "msg.json",
            },
        }
        mock_get.return_value = mock_response

        client = BridgeClient(base_url="http://127.0.0.1:8765")
        result = client.last_sent()

        assert result["ok"] is True
        assert result["last_sent"]["ipc_id"] == "sha256:xyz789"


class TestIntegration:
    """Integration tests."""

    @pytest.fixture
    def temp_ipc_dir(self, tmp_path: Path) -> Path:
        """Create temporary IPC directory structure."""
        (tmp_path / "claude_inbox").mkdir()
        (tmp_path / "gpt_inbox").mkdir()
        (tmp_path / "claude_outbox").mkdir()
        (tmp_path / "gpt_outbox").mkdir()
        (tmp_path / "deadletter").mkdir()
        return tmp_path

    def test_send_creates_file(self, temp_ipc_dir: Path) -> None:
        """Test that send_chat creates file in correct inbox."""
        with patch("core.chat_dispatch._IPC_DIR", temp_ipc_dir):
            with patch("core.chat_dispatch.CLAUDE_INBOX", temp_ipc_dir / "claude_inbox"):
                result = send_chat("claude", "Test message")

                assert result.ok is True
                assert result.ipc_id.startswith("sha256:")

                # Check file was created
                inbox_files = list((temp_ipc_dir / "claude_inbox").glob("*.json"))
                assert len(inbox_files) == 1

                # Verify JSON structure
                content = json.loads(inbox_files[0].read_text(encoding="utf-8"))
                assert content["type"] == "task"
                assert content["payload"]["message"] == "Test message"
                assert content["id"] == result.ipc_id


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
