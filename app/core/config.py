import os
from dataclasses import dataclass


def _as_bool(value: str, default: bool) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    app_name: str
    app_env: str
    app_port: int
    log_level: str
    enable_debug: bool
    metrics_path: str
    enable_prometheus: bool
    intent_conf_threshold: float
    risk_high_force_human: bool
    prompt_version: str
    kb_version: str
    policy_version: str
    embedding_mode: str
    embedding_model: str
    default_language: str
    cache_region: str
    redis_host: str
    redis_port: int
    redis_db: int
    redis_password: str
    enable_l1_cache: bool
    enable_l2_sem_cache: bool
    l1_ttl_seconds: int
    l2_ttl_seconds: int
    semantic_top_k: int
    semantic_threshold_low: float
    semantic_threshold_high: float
    l2_gray_pg_topk: int
    l2_gray_enable_rerank: bool
    l2_gray_rerank_provider: str
    l2_gray_rerank_model: str
    l2_gray_rerank_candidates: int
    l2_gray_rerank_topk: int
    l2_gray_second_threshold: float
    cache_admission_enabled: bool
    cache_admission_min_answer_len: int
    cache_admission_min_citations: int
    cache_admission_blocked_domains: str
    cache_circuit_fail_threshold: int
    cache_circuit_cooldown_seconds: int
    memory_recent_turns: int
    memory_episodic_max: int
    memory_write_score_threshold: float
    memory_short_ttl_seconds: int
    memory_long_ttl_seconds: int
    memory_l3_ttl_seconds: int
    memory_read_top_k: int
    enable_memory: bool
    context_total_budget_chars: int
    context_memory_budget_ratio: float
    memory_summarizer_enabled: bool
    memory_summary_max_chars: int
    memory_dedup_enabled: bool
    memory_dedup_sim_threshold_long: float
    memory_dedup_sim_threshold_l3: float
    context_memory_short_ratio: float
    context_memory_long_ratio: float
    context_memory_l3_ratio: float
    context_memory_summarizer_enabled: bool
    context_memory_summary_max_chars: int

    @classmethod
    def from_env(cls) -> "Settings":
        embedding_mode = os.getenv("EMBEDDING_MODE", "cloud").strip().lower()
        if embedding_mode == "local":
            embedding_model = os.getenv(
                "LOCAL_EMBEDDING_MODEL",
                "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
            )
        else:
            embedding_model = os.getenv(
                "CLOUD_EMBEDDING_MODEL",
                os.getenv("EMBEDDING_MODEL", "openai/text-embedding-v3"),
            )
        return cls(
            app_name=os.getenv("APP_NAME", "ai-cs-demo"),
            app_env=os.getenv("APP_ENV", "dev"),
            app_port=int(os.getenv("APP_PORT", "8000")),
            log_level=os.getenv("LOG_LEVEL", "INFO"),
            enable_debug=_as_bool(os.getenv("ENABLE_DEBUG"), True),
            metrics_path=os.getenv("METRICS_PATH", "/metrics"),
            enable_prometheus=_as_bool(os.getenv("ENABLE_PROMETHEUS"), True),
            intent_conf_threshold=float(os.getenv("INTENT_CONF_THRESHOLD", "0.65")),
            risk_high_force_human=_as_bool(os.getenv("RISK_HIGH_FORCE_HUMAN"), True),
            prompt_version=os.getenv("PROMPT_VERSION", "v1"),
            kb_version=os.getenv("KB_VERSION", "v1"),
            policy_version=os.getenv("POLICY_VERSION", "v1"),
            embedding_mode=embedding_mode,
            embedding_model=embedding_model,
            default_language=os.getenv("DEFAULT_LANGUAGE", "zh"),
            cache_region=os.getenv("CACHE_REGION", "global"),
            redis_host=os.getenv("REDIS_HOST", "127.0.0.1"),
            redis_port=int(os.getenv("REDIS_PORT", "6379")),
            redis_db=int(os.getenv("REDIS_DB", "0")),
            redis_password=os.getenv("REDIS_PASSWORD", ""),
            enable_l1_cache=_as_bool(os.getenv("ENABLE_L1_CACHE"), True),
            enable_l2_sem_cache=_as_bool(os.getenv("ENABLE_L2_SEM_CACHE"), True),
            l1_ttl_seconds=int(os.getenv("L1_TTL_SECONDS", "1800")),
            l2_ttl_seconds=int(os.getenv("L2_TTL_SECONDS", "7200")),
            semantic_top_k=int(os.getenv("SEMANTIC_TOP_K", "5")),
            semantic_threshold_low=float(os.getenv("SEMANTIC_THRESHOLD_LOW", "0.82")),
            semantic_threshold_high=float(os.getenv("SEMANTIC_THRESHOLD_HIGH", "0.90")),
            l2_gray_pg_topk=int(os.getenv("L2_GRAY_PG_TOPK", "10")),
            l2_gray_enable_rerank=_as_bool(os.getenv("L2_GRAY_ENABLE_RERANK"), False),
            l2_gray_rerank_provider=os.getenv("L2_GRAY_RERANK_PROVIDER", "local"),
            l2_gray_rerank_model=os.getenv("L2_GRAY_RERANK_MODEL", "BAAI/bge-reranker-base"),
            l2_gray_rerank_candidates=int(os.getenv("L2_GRAY_RERANK_CANDIDATES", "10")),
            l2_gray_rerank_topk=int(os.getenv("L2_GRAY_RERANK_TOPK", "3")),
            l2_gray_second_threshold=float(os.getenv("L2_GRAY_SECOND_THRESHOLD", "0.86")),
            cache_admission_enabled=_as_bool(os.getenv("CACHE_ADMISSION_ENABLED"), True),
            cache_admission_min_answer_len=int(os.getenv("CACHE_ADMISSION_MIN_ANSWER_LEN", "20")),
            cache_admission_min_citations=int(os.getenv("CACHE_ADMISSION_MIN_CITATIONS", "1")),
            cache_admission_blocked_domains=os.getenv("CACHE_ADMISSION_BLOCKED_DOMAINS", "risk_query"),
            cache_circuit_fail_threshold=int(os.getenv("CACHE_CIRCUIT_FAIL_THRESHOLD", "3")),
            cache_circuit_cooldown_seconds=int(os.getenv("CACHE_CIRCUIT_COOLDOWN_SECONDS", "30")),
            memory_recent_turns=int(os.getenv("MEMORY_RECENT_TURNS", "8")),
            memory_episodic_max=int(os.getenv("MEMORY_EPISODIC_MAX", "5")),
            memory_write_score_threshold=float(os.getenv("MEMORY_WRITE_SCORE_THRESHOLD", "0.60")),
            memory_short_ttl_seconds=int(os.getenv("MEMORY_SHORT_TTL_SECONDS", "86400")),
            memory_long_ttl_seconds=int(os.getenv("MEMORY_LONG_TTL_SECONDS", "2592000")),
            memory_l3_ttl_seconds=int(os.getenv("MEMORY_L3_TTL_SECONDS", "604800")),
            memory_read_top_k=int(os.getenv("MEMORY_READ_TOP_K", "6")),
            enable_memory=_as_bool(os.getenv("ENABLE_MEMORY"), True),
            context_total_budget_chars=int(os.getenv("CONTEXT_TOTAL_BUDGET_CHARS", "2000")),
            context_memory_budget_ratio=float(os.getenv("CONTEXT_MEMORY_BUDGET_RATIO", "0.3")),
            memory_summarizer_enabled=_as_bool(os.getenv("MEMORY_SUMMARIZER_ENABLED"), False),
            memory_summary_max_chars=int(os.getenv("MEMORY_SUMMARY_MAX_CHARS", "800")),
            memory_dedup_enabled=_as_bool(os.getenv("MEMORY_DEDUP_ENABLED"), True),
            memory_dedup_sim_threshold_long=float(os.getenv("MEMORY_DEDUP_SIM_THRESHOLD_LONG", "0.92")),
            memory_dedup_sim_threshold_l3=float(os.getenv("MEMORY_DEDUP_SIM_THRESHOLD_L3", "0.95")),
            context_memory_short_ratio=float(os.getenv("CONTEXT_MEMORY_SHORT_RATIO", "0.5")),
            context_memory_long_ratio=float(os.getenv("CONTEXT_MEMORY_LONG_RATIO", "0.3")),
            context_memory_l3_ratio=float(os.getenv("CONTEXT_MEMORY_L3_RATIO", "0.2")),
            context_memory_summarizer_enabled=_as_bool(
                os.getenv("CONTEXT_MEMORY_SUMMARIZER_ENABLED"), False
            ),
            context_memory_summary_max_chars=int(
                os.getenv("CONTEXT_MEMORY_SUMMARY_MAX_CHARS", "180")
            ),
        )

