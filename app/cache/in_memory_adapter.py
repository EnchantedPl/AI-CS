from typing import Any, Dict, Optional


class InMemoryCacheAdapter:
    """Minimal cache adapter for local demo.

    This adapter mimics L1/L2 lookup behavior and keeps data in process memory.
    It is intentionally simple so it can be replaced by Redis later.
    """

    def __init__(self) -> None:
        self._l1_store: Dict[str, Dict[str, Any]] = {}
        self._l2_store: Dict[str, Dict[str, Any]] = {}

    def get_l1(self, key: str) -> Optional[Dict[str, Any]]:
        return self._l1_store.get(key)

    def set_l1(self, key: str, value: Dict[str, Any]) -> None:
        self._l1_store[key] = value

    def get_l2(self, key: str) -> Optional[Dict[str, Any]]:
        return self._l2_store.get(key)

    def set_l2(self, key: str, value: Dict[str, Any]) -> None:
        self._l2_store[key] = value

    def warmup_demo_data(self, l1_key: str) -> None:
        """Seed a deterministic demo hit case."""
        self.set_l1(
            l1_key,
            {
                "answer": "这是缓存命中的演示回复：你的退款正在处理中，预计 2 天内到账。",
                "citations": ["cache:l1:demo_seed"],
            },
        )


CACHE_ADAPTER = InMemoryCacheAdapter()

