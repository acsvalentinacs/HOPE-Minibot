# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-29T00:15:00Z
# Purpose: Systemd sd_notify integration for watchdog and status reporting
# Security: Fail-open on non-systemd systems, proper socket handling
# === END SIGNATURE ===
"""
Systemd Notification Module.

Provides sd_notify functionality for:
- READY=1: Service is ready
- WATCHDOG=1: Watchdog keepalive
- STATUS=...: Human-readable status
- STOPPING=1: Service is shutting down

Works transparently on non-systemd systems (no-op).

Usage:
    from core.runtime.systemd_notify import SystemdNotifier

    notifier = SystemdNotifier()
    notifier.ready()  # Tell systemd we're ready

    # In main loop:
    while running:
        notifier.watchdog()  # Send keepalive
        notifier.status("Processing order #123")
        ...

    notifier.stopping()  # Tell systemd we're shutting down
"""
from __future__ import annotations

import logging
import os
import socket
from typing import Optional

logger = logging.getLogger("runtime.systemd")


class SystemdNotifier:
    """
    Systemd notification handler.

    Sends notifications to systemd via NOTIFY_SOCKET.
    Fail-open: does nothing if not running under systemd.
    """

    def __init__(self) -> None:
        """Initialize notifier, detecting systemd environment."""
        self._socket_path: Optional[str] = os.environ.get("NOTIFY_SOCKET")
        self._socket: Optional[socket.socket] = None
        self._enabled: bool = False

        if self._socket_path:
            try:
                self._socket = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
                # Handle abstract sockets (start with @)
                if self._socket_path.startswith("@"):
                    self._socket_path = "\0" + self._socket_path[1:]
                self._enabled = True
                logger.info("Systemd notifier enabled: %s", os.environ.get("NOTIFY_SOCKET"))
            except Exception as e:
                logger.warning("Failed to create systemd socket: %s", e)
                self._socket = None
                self._enabled = False
        else:
            logger.debug("NOTIFY_SOCKET not set - systemd notifications disabled")

    @property
    def enabled(self) -> bool:
        """Check if systemd notifications are enabled."""
        return self._enabled

    def _send(self, message: str) -> bool:
        """
        Send message to systemd.

        Args:
            message: Notification message (e.g., "READY=1")

        Returns:
            True if sent successfully, False otherwise.
        """
        if not self._enabled or not self._socket or not self._socket_path:
            return False

        try:
            self._socket.sendto(message.encode("utf-8"), self._socket_path)
            logger.debug("sd_notify: %s", message.replace("\n", " | "))
            return True
        except Exception as e:
            logger.warning("sd_notify failed: %s", e)
            return False

    def ready(self) -> bool:
        """
        Notify systemd that service is ready.

        Call this after initialization is complete.
        """
        return self._send("READY=1")

    def watchdog(self) -> bool:
        """
        Send watchdog keepalive.

        Must be called at intervals less than WatchdogSec/2.
        Recommended: WatchdogSec=30 -> call every 10-15 seconds.
        """
        return self._send("WATCHDOG=1")

    def status(self, status: str) -> bool:
        """
        Update human-readable status.

        Visible in `systemctl status <service>`.

        Args:
            status: Status string (max ~200 chars recommended)
        """
        # Sanitize status (no newlines, reasonable length)
        clean_status = status.replace("\n", " ").strip()[:200]
        return self._send(f"STATUS={clean_status}")

    def stopping(self) -> bool:
        """
        Notify systemd that service is stopping.

        Call this at the beginning of shutdown sequence.
        """
        return self._send("STOPPING=1")

    def reloading(self) -> bool:
        """
        Notify systemd that service is reloading config.

        Call ready() again when reload is complete.
        """
        return self._send("RELOADING=1")

    def notify_watchdog_with_status(self, status: str) -> bool:
        """
        Combined watchdog + status update.

        More efficient than separate calls.
        """
        clean_status = status.replace("\n", " ").strip()[:200]
        return self._send(f"WATCHDOG=1\nSTATUS={clean_status}")

    def close(self) -> None:
        """Close the socket."""
        if self._socket:
            try:
                self._socket.close()
            except Exception:
                pass
            self._socket = None
            self._enabled = False


# Singleton instance for convenience
_notifier: Optional[SystemdNotifier] = None


def get_notifier() -> SystemdNotifier:
    """Get singleton SystemdNotifier instance."""
    global _notifier
    if _notifier is None:
        _notifier = SystemdNotifier()
    return _notifier


def sd_ready() -> bool:
    """Convenience: notify ready."""
    return get_notifier().ready()


def sd_watchdog() -> bool:
    """Convenience: send watchdog keepalive."""
    return get_notifier().watchdog()


def sd_status(status: str) -> bool:
    """Convenience: update status."""
    return get_notifier().status(status)


def sd_stopping() -> bool:
    """Convenience: notify stopping."""
    return get_notifier().stopping()
