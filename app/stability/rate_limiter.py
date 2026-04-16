import threading
import time
from dataclasses import dataclass
from typing import Dict, Tuple


@dataclass
class BucketState:
    req_tokens: float
    llm_tokens: float
    last_ts: float


class RequestTokenLimiter:
    """In-memory token bucket for request + token dual limits."""

    def __init__(
        self,
        *,
        req_per_minute: float,
        token_per_minute: float,
        high_req_per_minute: float | None = None,
        high_token_per_minute: float | None = None,
    ) -> None:
        low_req = max(1.0, req_per_minute)
        low_tok = max(10.0, token_per_minute)
        high_req = max(low_req, float(high_req_per_minute)) if high_req_per_minute is not None else low_req
        high_tok = max(low_tok, float(high_token_per_minute)) if high_token_per_minute is not None else low_tok
        self._profiles = {
            "low": {"req_per_minute": low_req, "token_per_minute": low_tok},
            "high": {"req_per_minute": high_req, "token_per_minute": high_tok},
        }
        self._lock = threading.Lock()
        self._state: Dict[str, BucketState] = {}

    def allow(
        self,
        key: str,
        req_cost: float,
        token_cost: float,
        priority_tier: str = "low",
    ) -> Tuple[bool, str, Dict[str, float]]:
        now = time.time()
        tier = "high" if str(priority_tier).strip().lower() == "high" else "low"
        profile = self._profiles[tier]
        req_cap = float(profile["req_per_minute"])
        tok_cap = float(profile["token_per_minute"])
        bucket_key = f"{tier}:{key}"
        with self._lock:
            st = self._state.get(bucket_key)
            if st is None:
                st = BucketState(
                    req_tokens=req_cap,
                    llm_tokens=tok_cap,
                    last_ts=now,
                )
                self._state[bucket_key] = st

            elapsed = max(0.0, now - st.last_ts)
            st.last_ts = now
            st.req_tokens = min(
                req_cap,
                st.req_tokens + elapsed * (req_cap / 60.0),
            )
            st.llm_tokens = min(
                tok_cap,
                st.llm_tokens + elapsed * (tok_cap / 60.0),
            )

            if st.req_tokens < req_cost:
                return False, "request_quota_exceeded", {
                    "req_tokens_left": round(st.req_tokens, 4),
                    "llm_tokens_left": round(st.llm_tokens, 4),
                    "req_tokens_capacity": round(req_cap, 4),
                    "llm_tokens_capacity": round(tok_cap, 4),
                    "priority_tier": tier,
                }
            if st.llm_tokens < token_cost:
                return False, "token_quota_exceeded", {
                    "req_tokens_left": round(st.req_tokens, 4),
                    "llm_tokens_left": round(st.llm_tokens, 4),
                    "req_tokens_capacity": round(req_cap, 4),
                    "llm_tokens_capacity": round(tok_cap, 4),
                    "priority_tier": tier,
                }

            st.req_tokens -= req_cost
            st.llm_tokens -= token_cost
            return True, "ok", {
                "req_tokens_left": round(st.req_tokens, 4),
                "llm_tokens_left": round(st.llm_tokens, 4),
                "req_tokens_capacity": round(req_cap, 4),
                "llm_tokens_capacity": round(tok_cap, 4),
                "priority_tier": tier,
            }

