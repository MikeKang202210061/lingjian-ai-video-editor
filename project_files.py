"""Atomic project persistence, independent of Qt and editing policy."""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path


def write_project(path: str | Path, data: dict) -> None:
    """Publish a complete JSON document; failed writes preserve the old file."""
    target = Path(path)
    payload = json.dumps(data, ensure_ascii=False, indent=2)
    descriptor, temporary = tempfile.mkstemp(prefix='.' + target.name + '-', suffix='.tmp',
                                              dir=target.parent)
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8') as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    finally:
        Path(temporary).unlink(missing_ok=True)
