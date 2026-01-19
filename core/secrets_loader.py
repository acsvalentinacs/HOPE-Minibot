"""
HOPE/NORE Unified Secrets Loader

Fail-closed design:
- Missing required secret = STOP
- Invalid format = STOP
- File not found = STOP
- Any doubt = FAIL, not PASS

Usage:
    from core.secrets_loader import SecretsLoader

    secrets = SecretsLoader.load()
    api_key = secrets.get_required('BINANCE_API_KEY')
    optional = secrets.get('SOME_OPTIONAL_KEY', default='fallback')
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

# Default secrets location
DEFAULT_SECRETS_PATH = Path(r'C:\secrets\hope\.env')


class SecretsLoadError(Exception):
    """Raised when secrets cannot be loaded - triggers STOP."""
    pass


class MissingSecretError(Exception):
    """Raised when a required secret is missing - triggers STOP."""
    pass


@dataclass
class SecretsLoader:
    """
    Unified secrets loader with fail-closed validation.

    Single Source of Truth for all credential access.
    """
    _secrets: Dict[str, str] = field(default_factory=dict)
    _loaded_from: Optional[Path] = None
    _accessed_keys: Set[str] = field(default_factory=set)

    @classmethod
    def load(cls, path: Optional[Path] = None) -> 'SecretsLoader':
        """
        Load secrets from .env file.

        Fail-closed: Any error = SecretsLoadError

        Args:
            path: Path to .env file. Defaults to C:\\secrets\\hope\\.env

        Returns:
            SecretsLoader instance with loaded secrets

        Raises:
            SecretsLoadError: If file not found or parse error
        """
        secrets_path = path or DEFAULT_SECRETS_PATH

        if not secrets_path.exists():
            raise SecretsLoadError(
                f"STOP: Secrets file not found: {secrets_path}\n"
                f"Expected location: {DEFAULT_SECRETS_PATH}\n"
                f"Create from .env.template and populate with real values."
            )

        if not secrets_path.is_file():
            raise SecretsLoadError(
                f"STOP: Secrets path is not a file: {secrets_path}"
            )

        try:
            content = secrets_path.read_text(encoding='utf-8')
        except Exception as e:
            raise SecretsLoadError(
                f"STOP: Cannot read secrets file: {secrets_path}\n"
                f"Error: {e}"
            ) from e

        secrets = cls._parse_env(content, secrets_path)

        loader = cls(_secrets=secrets, _loaded_from=secrets_path)
        return loader

    @staticmethod
    def _parse_env(content: str, source_path: Path) -> Dict[str, str]:
        """
        Parse .env content with strict validation.

        Fail-closed: Invalid line = error (not skip)
        """
        secrets: Dict[str, str] = {}
        line_num = 0

        for line in content.splitlines():
            line_num += 1
            stripped = line.strip()

            # Skip empty lines and comments
            if not stripped or stripped.startswith('#'):
                continue

            # Must have = sign
            if '=' not in stripped:
                raise SecretsLoadError(
                    f"STOP: Invalid line {line_num} in {source_path}\n"
                    f"Expected KEY=VALUE format, got: {stripped[:50]}"
                )

            key, _, value = stripped.partition('=')
            key = key.strip()
            value = value.strip()

            # Validate key format
            if not key or not key.replace('_', '').isalnum():
                raise SecretsLoadError(
                    f"STOP: Invalid key at line {line_num} in {source_path}\n"
                    f"Key must be alphanumeric with underscores, got: {key}"
                )

            # Remove quotes if present
            if len(value) >= 2:
                if (value.startswith('"') and value.endswith('"')) or \
                   (value.startswith("'") and value.endswith("'")):
                    value = value[1:-1]

            secrets[key] = value

        return secrets

    def get_required(self, key: str) -> str:
        """
        Get a required secret. Missing = STOP.

        Args:
            key: Secret key name

        Returns:
            Secret value

        Raises:
            MissingSecretError: If key not found
        """
        self._accessed_keys.add(key)

        if key not in self._secrets:
            raise MissingSecretError(
                f"STOP: Required secret missing: {key}\n"
                f"Secrets loaded from: {self._loaded_from}\n"
                f"Available keys: {sorted(self._secrets.keys())}"
            )

        value = self._secrets[key]

        # Fail-closed: Placeholder values are invalid
        placeholders = ['your_', 'placeholder', 'xxx', 'changeme', 'TODO']
        for p in placeholders:
            if p.lower() in value.lower():
                raise MissingSecretError(
                    f"STOP: Secret {key} contains placeholder value\n"
                    f"Update {self._loaded_from} with real credentials."
                )

        return value

    def get(self, key: str, default: Optional[str] = None) -> Optional[str]:
        """
        Get an optional secret with default fallback.

        Args:
            key: Secret key name
            default: Default value if not found

        Returns:
            Secret value or default
        """
        self._accessed_keys.add(key)
        return self._secrets.get(key, default)

    def has(self, key: str) -> bool:
        """Check if secret exists (without accessing value)."""
        return key in self._secrets

    def get_all_keys(self) -> Set[str]:
        """Get all available secret keys (for debugging)."""
        return set(self._secrets.keys())

    def get_accessed_keys(self) -> Set[str]:
        """Get keys that were accessed during runtime."""
        return self._accessed_keys.copy()

    def validate_required(self, keys: List[str]) -> None:
        """
        Validate that all required keys exist and are not placeholders.

        Call at startup to fail-fast.

        Args:
            keys: List of required key names

        Raises:
            MissingSecretError: If any key is missing or placeholder
        """
        missing = []
        for key in keys:
            try:
                self.get_required(key)
            except MissingSecretError as e:
                missing.append(str(e))

        if missing:
            raise MissingSecretError(
                f"STOP: {len(missing)} required secrets invalid:\n" +
                "\n".join(missing)
            )

    def __repr__(self) -> str:
        return (
            f"SecretsLoader(loaded_from={self._loaded_from}, "
            f"keys={len(self._secrets)}, accessed={len(self._accessed_keys)})"
        )


# Convenience singleton for simple usage
_global_loader: Optional[SecretsLoader] = None


def get_secrets(path: Optional[Path] = None) -> SecretsLoader:
    """
    Get or create global secrets loader singleton.

    Thread-safe for reads after initial load.
    """
    global _global_loader

    if _global_loader is None:
        _global_loader = SecretsLoader.load(path)

    return _global_loader


def require(key: str) -> str:
    """Shortcut to get required secret from global loader."""
    return get_secrets().get_required(key)


def optional(key: str, default: Optional[str] = None) -> Optional[str]:
    """Shortcut to get optional secret from global loader."""
    return get_secrets().get(key, default)
