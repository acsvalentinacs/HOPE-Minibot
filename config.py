import os
from typing import Any, Dict, Optional

def _maybe_load_dotenv(env_file: Optional[str] = None) -> None:
    try:
        from dotenv import load_dotenv, find_dotenv
        if env_file and os.path.exists(env_file):
            load_dotenv(env_file, override=False)
        else:
            load_dotenv(find_dotenv(), override=False)
    except Exception:
        # Без python-dotenv — просто пропустим
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
