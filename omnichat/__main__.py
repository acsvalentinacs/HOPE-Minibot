# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-27T15:30:00Z
# Modified by: Claude (opus-4)
# Modified at: 2026-01-27T15:35:00Z
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

# Ensure paths are set up correctly
OMNICHAT_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = OMNICHAT_ROOT.parent

# Add both project root and omnichat root to path
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if str(OMNICHAT_ROOT) not in sys.path:
    sys.path.insert(0, str(OMNICHAT_ROOT))


def main() -> int:
    """Run OMNI-CHAT application."""
    # Import after path setup
    from app import HopeOmniChat

    app = HopeOmniChat()
    app.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
