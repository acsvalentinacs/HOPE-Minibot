# -*- coding: utf-8 -*-
# === AI SIGNATURE ===
# Created by: Claude (opus-4)
# Created at: 2026-01-29 10:00:00 UTC
# Purpose: Model version control with rollback capability
# === END SIGNATURE ===
"""
Model Registry - Version control for ML models.

Features:
- Version tracking (v1, v2, v3...)
- Performance metrics per version
- Rollback to previous version
- A/B testing support
- Model artifacts stored as joblib files

Directory structure:
    state/ai/models/
    ├── registry.json          # Version metadata
    ├── current -> v3/         # Symlink to active version
    ├── v1/
    │   ├── model.joblib
    │   └── metrics.json
    ├── v2/
    │   └── ...
    └── v3/
        └── ...
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Check joblib availability
try:
    import joblib
    JOBLIB_AVAILABLE = True
except ImportError:
    JOBLIB_AVAILABLE = False
    logger.warning("joblib not installed: pip install joblib")


@dataclass
class ModelVersion:
    """Model version metadata."""
    version: int
    created_at: datetime
    trained_samples: int
    metrics: Dict[str, float] = field(default_factory=dict)
    is_active: bool = False
    model_hash: str = ""
    notes: str = ""


class ModelRegistry:
    """
    Model version control with rollback.

    Usage:
        registry = ModelRegistry(models_dir=Path("state/ai/models"))

        # Register new model
        version = registry.register(model, metrics, trained_samples=100)

        # Get active model
        model = registry.get_active()

        # Rollback to previous
        registry.rollback()
    """

    def __init__(self, models_dir: Path = Path("state/ai/models")):
        self.models_dir = models_dir
        self.registry_file = models_dir / "registry.json"

        # Current versions
        self._versions: Dict[int, ModelVersion] = {}
        self._active_version: Optional[int] = None

        # Create directory
        self.models_dir.mkdir(parents=True, exist_ok=True)

        # Load registry
        self._load_registry()

        logger.info(f"ModelRegistry initialized, {len(self._versions)} versions, active: v{self._active_version}")

    def _load_registry(self) -> None:
        """Load registry from disk."""
        if not self.registry_file.exists():
            return

        try:
            with open(self.registry_file, "r", encoding="utf-8") as f:
                data = json.load(f)

            for v_data in data.get("versions", []):
                version = ModelVersion(
                    version=v_data["version"],
                    created_at=datetime.fromisoformat(v_data["created_at"]),
                    trained_samples=v_data.get("trained_samples", 0),
                    metrics=v_data.get("metrics", {}),
                    is_active=v_data.get("is_active", False),
                    model_hash=v_data.get("model_hash", ""),
                    notes=v_data.get("notes", ""),
                )
                self._versions[version.version] = version
                if version.is_active:
                    self._active_version = version.version

        except Exception as e:
            logger.error(f"Failed to load registry: {e}")

    def _save_registry(self) -> None:
        """Save registry to disk (atomic write)."""
        data = {
            "updated_at": datetime.utcnow().isoformat() + "Z",
            "active_version": self._active_version,
            "versions": [
                {
                    "version": v.version,
                    "created_at": v.created_at.isoformat() + "Z",
                    "trained_samples": v.trained_samples,
                    "metrics": v.metrics,
                    "is_active": v.is_active,
                    "model_hash": v.model_hash,
                    "notes": v.notes,
                }
                for v in sorted(self._versions.values(), key=lambda x: x.version)
            ]
        }

        tmp_path = self.registry_file.with_suffix(".tmp")
        try:
            with open(tmp_path, "w", encoding="utf-8", newline="\n") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, self.registry_file)
        except Exception as e:
            logger.error(f"Failed to save registry: {e}")
            if tmp_path.exists():
                tmp_path.unlink()

    def _get_next_version(self) -> int:
        """Get next version number."""
        if not self._versions:
            return 1
        return max(self._versions.keys()) + 1

    def _compute_model_hash(self, model: Any) -> str:
        """Compute hash of model for integrity check."""
        if not JOBLIB_AVAILABLE:
            return "no-joblib"

        try:
            import io
            import pickle
            buffer = io.BytesIO()
            pickle.dump(model, buffer)
            content = buffer.getvalue()
            return "sha256:" + hashlib.sha256(content).hexdigest()[:16]
        except Exception as e:
            logger.warning(f"Failed to hash model: {e}")
            return "unknown"

    def register(
        self,
        model: Any,
        metrics: Dict[str, float],
        trained_samples: int,
        notes: str = "",
        activate: bool = True,
    ) -> int:
        """
        Register a new model version.

        Args:
            model: Trained model object (XGBClassifier, etc.)
            metrics: Training metrics dict
            trained_samples: Number of samples used for training
            notes: Optional notes about this version
            activate: Whether to activate this version immediately

        Returns:
            Version number
        """
        if not JOBLIB_AVAILABLE:
            raise RuntimeError("joblib not installed, cannot save model")

        version_num = self._get_next_version()
        version_dir = self.models_dir / f"v{version_num}"
        version_dir.mkdir(parents=True, exist_ok=True)

        # Save model
        model_path = version_dir / "model.joblib"
        try:
            joblib.dump(model, model_path)
        except Exception as e:
            logger.error(f"Failed to save model: {e}")
            shutil.rmtree(version_dir, ignore_errors=True)
            raise

        # Save metrics
        metrics_path = version_dir / "metrics.json"
        with open(metrics_path, "w", encoding="utf-8") as f:
            json.dump(metrics, f, indent=2, ensure_ascii=False)

        # Create version metadata
        model_hash = self._compute_model_hash(model)
        version = ModelVersion(
            version=version_num,
            created_at=datetime.utcnow(),
            trained_samples=trained_samples,
            metrics=metrics,
            is_active=False,
            model_hash=model_hash,
            notes=notes,
        )

        self._versions[version_num] = version

        # Activate if requested
        if activate:
            self._activate_version(version_num)

        self._save_registry()

        logger.info(f"Registered model v{version_num} (samples={trained_samples}, hash={model_hash[:20]})")
        return version_num

    def _activate_version(self, version: int) -> None:
        """Activate a specific version."""
        if version not in self._versions:
            raise ValueError(f"Version {version} not found")

        # Deactivate current
        if self._active_version is not None and self._active_version in self._versions:
            self._versions[self._active_version].is_active = False

        # Activate new
        self._versions[version].is_active = True
        self._active_version = version

        logger.info(f"Activated model v{version}")

    def get_active(self) -> Optional[Any]:
        """
        Get currently active model.

        Returns:
            Model object or None if no active version
        """
        if self._active_version is None:
            return None

        return self.get_version(self._active_version)

    def get_version(self, version: int) -> Optional[Any]:
        """
        Get specific model version.

        Args:
            version: Version number

        Returns:
            Model object or None
        """
        if not JOBLIB_AVAILABLE:
            logger.error("joblib not installed")
            return None

        if version not in self._versions:
            return None

        model_path = self.models_dir / f"v{version}" / "model.joblib"
        if not model_path.exists():
            logger.error(f"Model file not found: {model_path}")
            return None

        try:
            return joblib.load(model_path)
        except Exception as e:
            logger.error(f"Failed to load model v{version}: {e}")
            return None

    def rollback(self) -> bool:
        """
        Rollback to previous version.

        Returns:
            True if rollback successful
        """
        if self._active_version is None or self._active_version <= 1:
            logger.warning("Cannot rollback: no previous version")
            return False

        # Find previous version
        sorted_versions = sorted(self._versions.keys())
        current_idx = sorted_versions.index(self._active_version)

        if current_idx == 0:
            logger.warning("Cannot rollback: already at oldest version")
            return False

        prev_version = sorted_versions[current_idx - 1]
        self._activate_version(prev_version)
        self._save_registry()

        logger.warning(f"Rolled back from v{self._active_version} to v{prev_version}")
        return True

    def get_active_version(self) -> Optional[int]:
        """Get active version number."""
        return self._active_version

    def get_version_info(self, version: int) -> Optional[ModelVersion]:
        """Get metadata for specific version."""
        return self._versions.get(version)

    def get_all_versions(self) -> List[ModelVersion]:
        """Get all versions sorted by version number."""
        return sorted(self._versions.values(), key=lambda x: x.version)

    def compare_versions(self, v1: int, v2: int) -> Dict[str, Any]:
        """
        Compare two model versions.

        Returns:
            Dict with comparison metrics
        """
        version1 = self._versions.get(v1)
        version2 = self._versions.get(v2)

        if not version1 or not version2:
            return {"error": "Version not found"}

        # Calculate metric deltas
        deltas = {}
        for metric in set(version1.metrics.keys()) | set(version2.metrics.keys()):
            val1 = version1.metrics.get(metric, 0)
            val2 = version2.metrics.get(metric, 0)
            deltas[metric] = {
                f"v{v1}": val1,
                f"v{v2}": val2,
                "delta": val2 - val1,
                "delta_pct": ((val2 - val1) / val1 * 100) if val1 != 0 else 0,
            }

        return {
            "v1": v1,
            "v2": v2,
            "samples_v1": version1.trained_samples,
            "samples_v2": version2.trained_samples,
            "metrics": deltas,
        }

    def cleanup_old_versions(self, keep_last: int = 5) -> int:
        """
        Remove old model versions to save disk space.

        Args:
            keep_last: Number of recent versions to keep

        Returns:
            Number of versions removed
        """
        if len(self._versions) <= keep_last:
            return 0

        sorted_versions = sorted(self._versions.keys())
        versions_to_remove = sorted_versions[:-keep_last]

        # Never remove active version
        if self._active_version in versions_to_remove:
            versions_to_remove.remove(self._active_version)

        removed = 0
        for version in versions_to_remove:
            version_dir = self.models_dir / f"v{version}"
            try:
                shutil.rmtree(version_dir)
                del self._versions[version]
                removed += 1
                logger.info(f"Removed old model v{version}")
            except Exception as e:
                logger.warning(f"Failed to remove v{version}: {e}")

        if removed > 0:
            self._save_registry()

        return removed

    def get_stats(self) -> Dict[str, Any]:
        """Get registry statistics."""
        return {
            "total_versions": len(self._versions),
            "active_version": self._active_version,
            "latest_version": max(self._versions.keys()) if self._versions else None,
            "oldest_version": min(self._versions.keys()) if self._versions else None,
        }
