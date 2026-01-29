# -*- coding: utf-8 -*-
# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-30 02:58:00 UTC
# Purpose: Gates package - mandatory checks before trading operations
# === END SIGNATURE ===
"""
GATES PACKAGE

Mandatory checks before any trading operation:
- policy_preflight: Environment, credentials, risk, connectivity
- retrain_policy: Retrain permission with ACK requirement

All gates follow fail-closed semantics:
- If check cannot be performed -> FAIL
- If check fails -> FAIL
- Only explicit PASS allows operation
"""

from pathlib import Path

GATES_DIR = Path(__file__).parent
