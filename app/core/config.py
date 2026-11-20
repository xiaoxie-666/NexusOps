from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Application identity and runtime.
    app_name: str = "NexusOps Incident Intelligence Platform"
    app_env: str = "development"
    server_host: str = "0.0.0.0"
    server_port: int = 8080

    # Event-driven Agent runtime settings.
    agent_framework: str = "event_driven_multi_agent"
    agent_max_rounds: int = 8
    agent_max_claims_per_round: int = 4
    agent_max_claims_per_agent: int = 3
    agent_final_acceptance_min_confidence: float = 0.6
    agent_model_understanding_model: str = ""
    agent_model_impact_provider: str = ""
    agent_model_impact_model: str = ""
    agent_model_resolution_provider: str = ""
    agent_model_resolution_model: str = ""
    # Keep the CPU-friendly local model response compact and independently tunable.
    agent_model_resolution_temperature: float = 0.2
    agent_model_resolution_max_tokens: int = 1024
    agent_model_guard_provider: str = ""
    agent_model_guard_model: str = ""

    agent_model_default_provider: str = ""
    agent_model_default_model: str = ""
    agent_model_understanding_provider: str = ""

    # ModelRouter: per-role routing, fallback and three-state circuit breaker.
    model_router_enabled: bool = True
    model_fallback_provider: str = "mock"
    model_fallback_model: str = "mock"
    model_circuit_failure_threshold: int = 3
    model_circuit_reset_seconds: float = 30.0
    model_request_timeout_seconds: float = 120.0

    # Mock keeps the platform runnable without local model weights; switch to Ollama or OpenAI when configured.
    ai_provider: str = "mock"
    ai_temperature: float = 0.2
    ai_max_tokens: int = 768
    ollama_base_url: str = "http://127.0.0.1:11434"
    ollama_model: str = "qwen2.5:3b-instruct-q4_K_M"
    ai_request_timeout_seconds: float = 120.0
    openai_base_url: str = "https://api.openai.com/v1"
    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"
    openai_embedding_model: str = "text-embedding-3-small"
    knowledge_embedding_provider: str = "ollama"
    ollama_embedding_model: str = "bge-m3:latest"

    # Isolated NexusOps infrastructure defaults. Docker Compose overrides the
    # host names with the dedicated nexusops services.
    database_url: str = "mysql+pymysql://nexusops:nexusops_dev_password@127.0.0.1:13317/nexusops?charset=utf8mb4"
    # Kept separately so production startup can reject development credentials.
    mysql_root_password: str = "nexusops_root_dev"
    mysql_password: str = "nexusops_dev_password"
    redis_url: str = "redis://127.0.0.1:16390/0"
    redis_socket_timeout_seconds: float = 2.0

    # Hybrid retrieval over the operational knowledge corpus.
    knowledge_top_k: int = 4
    knowledge_candidate_k: int = 16
    knowledge_chunk_size: int = 512
    knowledge_chunk_overlap: int = 64
    knowledge_hybrid_vector_weight: float = 0.65
    knowledge_hybrid_bm25_weight: float = 0.35
    knowledge_rerank_enabled: bool = False
    # Strength 1.0 preserves the original 55/25/15/5 heuristic reranking.
    # 0.0 keeps the original hybrid-fused score. Try 0.35 for a less disruptive rerank.
    knowledge_rerank_strength: float = 1.0
    # Optional guard: retain the fused Top-1; rerank only the remaining candidates.
    knowledge_rerank_preserve_first: bool = False
    # 0 = original behavior; 1 = prefer distinct Runbook sources in Top-K,
    # then backfill from repeated sources if the corpus has too few sources.
    knowledge_max_chunks_per_source: int = 1
    knowledge_vector_enabled: bool = True
    knowledge_vector_required: bool = False
    chroma_persist_dir: str = "data/nexusops-chroma"
    chroma_collection_name: str = "nexusops_knowledge"
    chroma_snapshot_dir: str = "data/nexusops-chroma-snapshots"
    chroma_snapshot_keep: int = 5
    embedding_timeout_seconds: float = 120.0

    rag_eval_dataset: str = "app/rag_eval/nexusops-rag-eval.json"
    rag_eval_output: str = "target/rag-eval-report.json"
    rag_eval_enabled: bool = False
    rag_eval_exit_after_run: bool = False

    # Incident-scoped context memory (not chat-session memory).
    incident_context_cache_enabled: bool = True
    incident_context_cache_ttl_seconds: int = 3600
    incident_context_summary_max_chars: int = 500
    incident_context_token_budget: int = 2300

    # Incident domain.
    incident_key_prefix: str = "INC"
    incident_default_severity: str = "P2"
    incident_max_page_size: int = 100
    incident_allow_requester_create: bool = True
    alert_webhook_token: str = ""
    alert_webhook_max_payload_bytes: int = 1_000_000
    incident_tool_queue_enabled: bool = True
    incident_tool_poll_interval_seconds: float = 1.0
    incident_tool_max_attempts: int = 3
    incident_tool_retry_delay_seconds: float = 10.0
    oncall_notification_recipients: str = ""
    ops_knowledge_auto_seed: bool = True
    incident_analysis_max_rounds: int = 8
    incident_analysis_min_confidence: float = 0.55
    knowledge_source_default_status: str = "ACTIVE"

    # Demo bootstrap credentials are development-only. Override in .env.
    bootstrap_admin_password: str = "admin123"
    bootstrap_engineer_password: str = "engineer123"
    bootstrap_oncall_password: str = "oncall123"

    # Notification and export settings.
    excel_path: str = "data/nexusops-incident-ledger.xlsx"
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_use_tls: bool = True
    smtp_use_ssl: bool = False
    smtp_timeout_seconds: float = 10.0
    alert_email_delivery_mode: str = "log"
    alert_email_from: str = ""
    alert_email_to: str = ""
    alert_email_subject_prefix: str = "[NexusOps 事件预警]"

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    @property
    def project_root(self) -> Path:
        return Path(__file__).resolve().parents[2]


@lru_cache
def get_settings() -> Settings:
    return Settings()


_DEVELOPMENT_CREDENTIALS = {
    "admin123",
    "engineer123",
    "oncall123",
    "nexusops_root_dev",
    "nexusops_dev_password",
}


def validate_runtime_configuration(settings: Settings | None = None) -> None:
    """Reject known demo credentials when the application is marked production.

    Development defaults remain convenient for a localhost-only demo. Production
    startup requires unique high-entropy credentials and a webhook token.
    """
    current = settings or get_settings()
    if current.app_env.strip().lower() not in {"prod", "production"}:
        return

    credentials = {
        "BOOTSTRAP_ADMIN_PASSWORD": current.bootstrap_admin_password,
        "BOOTSTRAP_ENGINEER_PASSWORD": current.bootstrap_engineer_password,
        "BOOTSTRAP_ONCALL_PASSWORD": current.bootstrap_oncall_password,
        "MYSQL_ROOT_PASSWORD": current.mysql_root_password,
        "MYSQL_PASSWORD": current.mysql_password,
    }
    errors: list[str] = []
    for name, value in credentials.items():
        if not value or value in _DEVELOPMENT_CREDENTIALS or len(value) < 16:
            errors.append(f"{name} must be a unique, non-demo value with at least 16 characters")
    if len(set(credentials.values())) != len(credentials):
        errors.append("all bootstrap and database passwords must be different")
    if len(current.alert_webhook_token or "") < 32:
        errors.append("ALERT_WEBHOOK_TOKEN must contain at least 32 characters in production")
    if not current.database_url.startswith("mysql+"):
        errors.append("DATABASE_URL must use MySQL in production")

    if errors:
        raise RuntimeError("Unsafe production configuration: " + "; ".join(errors))
