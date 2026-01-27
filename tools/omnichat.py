# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-26T05:35:00Z
# Purpose: CLI entry point for HOPE Omni-Chat
# === END SIGNATURE ===
"""
HOPE Omni-Chat CLI.

Usage:
    python tools/omnichat.py
    python tools/omnichat.py --mock
    python tools/omnichat.py --help
"""
from __future__ import annotations

import sys
from pathlib import Path

# Ensure project root is in path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def main() -> int:
    """Run Omni-Chat."""
    from core.omnichat.__main__ import main as omnichat_main
    return omnichat_main()


if __name__ == "__main__":
    sys.exit(main())
