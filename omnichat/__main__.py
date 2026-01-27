# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-27T15:30:00Z
# Purpose: OMNI-CHAT entry point for python -m omnichat
# === END SIGNATURE ===
"""
HOPE OMNI-CHAT Entry Point.

Usage:
    python -m omnichat
    python -m omnichat --mock
"""
from __future__ import annotations

import sys
from pathlib import Path

# Ensure project root is in path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def main() -> int:
    """Run OMNI-CHAT application."""
    from omnichat.app import OmniChatApp

    app = OmniChatApp()
    app.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
