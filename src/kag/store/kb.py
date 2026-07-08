"""In-memory KB store.

Wave 3 ships a thread-safe in-memory backing store so the KB CRUD
endpoints can be developed and tested without committing to a DB
schema. The plan is to swap this for an ArangoDB-backed
implementation in a follow-up wave; the public surface (method
names + return types) is deliberately kept narrow so the swap is
a one-line change at the call site.
"""

from __future__ import annotations

import threading
from collections.abc import Iterable
from datetime import UTC, datetime

from kag.models import APIKey, KnowledgeBase, KnowledgeFile


class KBStore:
    """In-memory store for KBs and their API keys.

    Methods are deliberately synchronous because no I/O happens; the
    ArangoDB-backed replacement will use a sync ``ArangoStore`` and
    keep this same shape.
    """

    def __init__(self) -> None:
        self._kbs: dict[str, KnowledgeBase] = {}
        self._api_keys: dict[str, APIKey] = {}
        self._lock = threading.Lock()

    def create(self, kb: KnowledgeBase) -> None:
        with self._lock:
            self._kbs[kb.kb_key] = kb

    def get(self, kb_key: str) -> KnowledgeBase | None:
        with self._lock:
            return self._kbs.get(kb_key)

    def list(self) -> list[KnowledgeBase]:
        with self._lock:
            return sorted(self._kbs.values(), key=lambda kb: kb.created_at)

    def update(
        self,
        kb_key: str,
        *,
        name: str | None = None,
        description: str | None = None,
    ) -> KnowledgeBase | None:
        with self._lock:
            kb = self._kbs.get(kb_key)
            if kb is None:
                return None
            updated = kb.model_copy(
                update={
                    "name": name if name is not None else kb.name,
                    "description": (description if description is not None else kb.description),
                    "updated_at": datetime.now(UTC),
                }
            )
            self._kbs[kb_key] = updated
            return updated

    def delete(self, kb_key: str) -> bool:
        with self._lock:
            kb = self._kbs.pop(kb_key, None)
            if kb is None:
                return False
            # Also drop any API keys bound to this KB.
            stale_hashes = [h for h, k in self._api_keys.items() if k.kb_key == kb_key]
            for h in stale_hashes:
                self._api_keys.pop(h, None)
            return True

    def add_api_key(self, api_key: APIKey) -> None:
        with self._lock:
            self._api_keys[api_key.key_hash] = api_key

    def find_api_key(self, key_hash: str) -> APIKey | None:
        with self._lock:
            return self._api_keys.get(key_hash)

    def api_keys_for(self, kb_key: str) -> Iterable[APIKey]:
        with self._lock:
            return [k for k in self._api_keys.values() if k.kb_key == kb_key]


_store: KBStore | None = None


def get_kb_store() -> KBStore:
    """Return the process-wide :class:`KBStore` instance."""
    global _store
    if _store is None:
        _store = KBStore()
    return _store


class FileStore:
    """In-memory store for per-KB file metadata.

    Same swap-to-ArangoDB plan as :class:`KBStore`. The file bytes
    themselves live in SeaweedFS, not here — this store only tracks
    ``kag_files`` metadata (file_id, kb_key, status, seaweed_key, …).
    """

    def __init__(self) -> None:
        self._files: dict[str, KnowledgeFile] = {}
        self._by_kb: dict[str, set[str]] = {}
        self._lock = threading.Lock()

    def add(self, file: KnowledgeFile) -> None:
        with self._lock:
            self._files[file.file_id] = file
            self._by_kb.setdefault(file.kb_key, set()).add(file.file_id)

    def get(self, file_id: str) -> KnowledgeFile | None:
        with self._lock:
            return self._files.get(file_id)

    def list_for_kb(self, kb_key: str) -> list[KnowledgeFile]:
        with self._lock:
            ids = self._by_kb.get(kb_key, set())
            return sorted(
                (self._files[fid] for fid in ids),
                key=lambda f: f.uploaded_at,
            )

    def delete(self, file_id: str) -> bool:
        with self._lock:
            f = self._files.pop(file_id, None)
            if f is None:
                return False
            self._by_kb.get(f.kb_key, set()).discard(file_id)
            return True

    def count_for_kb(self, kb_key: str) -> int:
        with self._lock:
            return len(self._by_kb.get(kb_key, set()))


_file_store: FileStore | None = None


def get_file_store() -> FileStore:
    """Return the process-wide :class:`FileStore` instance."""
    global _file_store
    if _file_store is None:
        _file_store = FileStore()
    return _file_store
