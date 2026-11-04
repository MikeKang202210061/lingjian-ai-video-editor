"""Document identity and recovery snapshots, without a GUI dependency."""
from __future__ import annotations

import hashlib
import json
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from project_files import write_project


def fingerprint(data: dict) -> str:
    return hashlib.sha256(json.dumps(data, ensure_ascii=False, sort_keys=True,
                                     separators=(',', ':')).encode()).hexdigest()


class DocumentState:
    def __init__(self, data: dict):
        self.mark_clean(data)

    def mark_clean(self, data: dict) -> None:
        self.saved = fingerprint(data)

    def force_dirty(self) -> None:
        self.saved = None

    def is_dirty(self, data: dict) -> bool:
        return self.saved != fingerprint(data)


@dataclass(frozen=True)
class RecoveryEntry:
    path: Path
    title: str
    saved_at: float
    source_project: str


class RecoveryStore:
    """Each running window writes its own snapshot, preserving older sessions."""
    def __init__(self, folder: Path, legacy: Path | None = None):
        self.folder = Path(folder)
        self.legacy = Path(legacy) if legacy else None
        self.current = self.folder / (uuid.uuid4().hex + '.ljproject')
        self.last_snapshot = None

    def save(self, data: dict, source_project: str = '') -> bool:
        identity = fingerprint({'project': data, 'source_project': source_project})
        if identity == self.last_snapshot and self.current.is_file():
            return False
        self.folder.mkdir(parents=True, exist_ok=True)
        snapshot = dict(data)
        snapshot['_recovery'] = {'source_project': source_project, 'saved_at': time.time()}
        write_project(self.current, snapshot)
        self.last_snapshot = identity
        return True

    def entries(self) -> list[RecoveryEntry]:
        paths = list(self.folder.glob('*.ljproject'))
        if self.legacy and self.legacy.is_file():
            paths.append(self.legacy)
        entries = []
        for path in paths:
            if path == self.current:
                continue
            try:
                data = json.loads(path.read_text(encoding='utf-8'))
                if (not isinstance(data, dict) or not isinstance(data.get('clips', []), list)
                        or not isinstance(data.get('overlays', []), list)
                        or not isinstance(data.get('title'), str)):
                    continue
                meta = data.get('_recovery') or {}
                source = meta.get('source_project', '') if isinstance(meta, dict) else ''
                entries.append(RecoveryEntry(path, data['title'][:120], path.stat().st_mtime,
                                             source if isinstance(source, str) else ''))
            except (OSError, ValueError, TypeError):
                continue
        return sorted(entries, key=lambda entry: entry.saved_at, reverse=True)

    def remove(self, path: Path, *, protected: Path | None = None) -> None:
        path = Path(path)
        if protected is not None and path.resolve() == Path(protected).resolve():
            return
        # Only delete snapshots owned by this store, never an arbitrary project.
        if path.parent.resolve() != self.folder.resolve() and path != self.legacy:
            return
        path.unlink(missing_ok=True)
        if path == self.current:
            self.last_snapshot = None

    def clear_current(self, *, protected: Path | None = None) -> None:
        self.remove(self.current, protected=protected)
