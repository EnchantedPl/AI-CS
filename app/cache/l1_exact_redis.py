import json
from typing import Any, Dict, Optional

from redis import Redis


class L1ExactRedisCache:
    def __init__(self, redis_client: Redis, ttl_seconds: int) -> None:
        self._r = redis_client
        self._ttl_seconds = ttl_seconds

    def get(self, key: str) -> Optional[Dict[str, Any]]:
        raw = self._r.get(key)
        if not raw:
            return None
        try:
            return json.loads(raw.decode("utf-8"))
        except Exception:
            return None

    def set(self, key: str, value: Dict[str, Any]) -> None:
        payload = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self._r.set(key, payload, ex=self._ttl_seconds)
