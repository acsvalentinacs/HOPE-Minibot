# === AI SIGNATURE ===
# Created by: Claude
# Created at: 2026-01-20 12:30:00 UTC
# Modified by: Claude
# Modified at: 2026-01-20 12:30:00 UTC
# === END SIGNATURE ===
"""
Config module for HOPE minibot.

Uses core.secrets for secret loading (Defense in Depth).
Legacy _maybe_load_dotenv kept for backward compatibility but deprecated.
"""
import os
from typing import Any, Dict, Optional


def _maybe_load_dotenv(env_file: Optional[str] = None) -> None:
    """
    DEPRECATED: Use core.secrets.get_secret() instead.

    This function is kept for backward compatibility only.
    New code should use:
        from core.secrets import get_secret, require_secret
    """
    # No-op: secrets are now loaded via core.secrets module
    # which handles keyring, env vars, and .env file with proper priority
    pass

def load_yaml(path: str = "risk_config.yaml") -> Dict[str, Any]:
    try:
        import yaml  # type: ignore
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f)
                if isinstance(data, dict):
                    return data
    except Exception:
        pass
    return {}

def get(cfg: Dict[str, Any], dotted: str, default=None):
    node: Any = cfg
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            return default
        node = node[part]
    return node

def bootstrap(config_path: str = "risk_config.yaml", env_file: Optional[str] = None) -> Dict[str, Any]:
    _maybe_load_dotenv(env_file)
    cfg = load_yaml(config_path)
    return cfg
