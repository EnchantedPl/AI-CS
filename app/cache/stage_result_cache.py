import threading
import time
from typing import Any, Dict, Optional, Tuple


class StageResultCache:
    """Tiny TTL cache for deterministic stage outputs."""

    def __init__(self, default_ttl_seconds: int = 300) -> None:
        self._default_ttl_seconds = max(1, int(default_ttl_seconds))
        self._lock = threading.Lock()
        self._data: Dict[str, Tuple[float, Dict[str, Any]]] = {}

    def get(self, key: str) -> Optional[Dict[str, Any]]:
        now = time.time()
        with self._lock:
            row = self._data.get(key)
            if not row:
                return None
            expire_at, payload = row
            if expire_at <= now:
                self._data.pop(key, None)
                return None
            return dict(payload)

    def set(self, key: str, payload: Dict[str, Any], ttl_seconds: Optional[int] = None) -> None:
        ttl = self._default_ttl_seconds if ttl_seconds is None else max(1, int(ttl_seconds))
        expire_at = time.time() + ttl
        with self._lock:
            self._data[key] = (expire_at, dict(payload))
