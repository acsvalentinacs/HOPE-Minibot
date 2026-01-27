# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-27T21:00:00Z
# Purpose: Windows clipboard helper using clip.exe (fail-closed)
# Security: No external dependencies, subprocess only
# === END SIGNATURE ===
"""
Windows Clipboard Helper.

Provides cross-platform clipboard access with Windows-native fallback.
Uses clip.exe for writing (always available on Windows).

Usage:
    from omnichat.src.clipboard import copy_to_clipboard, ClipboardResult

    result = copy_to_clipboard("Hello, World!")
    if result.success:
        print("Copied!")
    else:
        print(f"Failed: {result.error}")
"""
from __future__ import annotations

import logging
import subprocess
import sys
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class ClipboardResult:
    """Result of clipboard operation."""
    success: bool
    error: Optional[str] = None
    method: Optional[str] = None  # "clip.exe" | "pyperclip" | "tkinter"


def copy_to_clipboard(text: str) -> ClipboardResult:
    """
    Copy text to system clipboard.

    Tries multiple methods in order:
    1. clip.exe (Windows native, most reliable)
    2. pyperclip (if installed)
    3. tkinter (fallback)

    Args:
        text: Text to copy to clipboard

    Returns:
        ClipboardResult with success status and method used
    """
    if not isinstance(text, str):
        return ClipboardResult(success=False, error="Input must be string")

    if not text:
        return ClipboardResult(success=False, error="Empty text")

    # Method 1: clip.exe (Windows native)
    if sys.platform == "win32":
        try:
            process = subprocess.Popen(
                ["clip.exe"],
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
            )
            _, stderr = process.communicate(input=text.encode("utf-16-le"))

            if process.returncode == 0:
                logger.debug("Copied %d chars via clip.exe", len(text))
                return ClipboardResult(success=True, method="clip.exe")
            else:
                error = stderr.decode("utf-8", errors="replace") if stderr else "Unknown error"
                logger.warning("clip.exe failed: %s", error)
        except FileNotFoundError:
            logger.warning("clip.exe not found")
        except Exception as e:
            logger.warning("clip.exe error: %s", e)

    # Method 2: pyperclip (cross-platform, optional dependency)
    try:
        import pyperclip
        pyperclip.copy(text)
        logger.debug("Copied %d chars via pyperclip", len(text))
        return ClipboardResult(success=True, method="pyperclip")
    except ImportError:
        logger.debug("pyperclip not installed")
    except Exception as e:
        logger.warning("pyperclip error: %s", e)

    # Method 3: tkinter (stdlib fallback)
    try:
        import tkinter as tk
        root = tk.Tk()
        root.withdraw()
        root.clipboard_clear()
        root.clipboard_append(text)
        root.update()  # Required for clipboard to persist
        root.destroy()
        logger.debug("Copied %d chars via tkinter", len(text))
        return ClipboardResult(success=True, method="tkinter")
    except ImportError:
        logger.warning("tkinter not available")
    except Exception as e:
        logger.warning("tkinter error: %s", e)

    # All methods failed
    return ClipboardResult(
        success=False,
        error="All clipboard methods failed (clip.exe, pyperclip, tkinter)"
    )


def get_clipboard_text() -> Optional[str]:
    """
    Get text from system clipboard.

    Returns:
        Clipboard text or None if failed
    """
    # Method 1: pyperclip
    try:
        import pyperclip
        return pyperclip.paste()
    except ImportError:
        pass
    except Exception as e:
        logger.warning("pyperclip paste error: %s", e)

    # Method 2: tkinter
    try:
        import tkinter as tk
        root = tk.Tk()
        root.withdraw()
        try:
            text = root.clipboard_get()
            return text
        except tk.TclError:
            return None  # Clipboard empty or non-text
        finally:
            root.destroy()
    except ImportError:
        pass
    except Exception as e:
        logger.warning("tkinter paste error: %s", e)

    # Method 3: PowerShell (Windows)
    if sys.platform == "win32":
        try:
            result = subprocess.run(
                ["powershell.exe", "-Command", "Get-Clipboard"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0:
                return result.stdout.rstrip("\r\n")
        except Exception as e:
            logger.warning("PowerShell Get-Clipboard error: %s", e)

    return None


if __name__ == "__main__":
    # Quick test
    test_text = "HOPE Clipboard Test 🚀"
    result = copy_to_clipboard(test_text)
    print(f"Copy result: {result}")

    retrieved = get_clipboard_text()
    print(f"Retrieved: {retrieved}")
