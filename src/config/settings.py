"""
Application settings and configuration.
Uses pydantic-settings for environment variable parsing.
"""
from functools import lru_cache
from typing import Optional
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application configuration loaded from environment variables."""
    
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )
    
    # Application
    app_name: str = "kubernetes-aiops-evidence-graph"
    app_env: str = Field(default="development", description="development, staging, production")
    debug: bool = False
    log_level: str = "INFO"
    
    # API Server
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    api_workers: int = 4
    
    # PostgreSQL
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_db: str = "aiops"
    postgres_user: str = "aiops"
    postgres_password: str = "aiops_secure_password_change_me"
    database_url: Optional[str] = None
    
    @property
    def pg_database_url(self) -> str:
        if self.database_url:
            return self.database_url
        return f"postgresql+asyncpg://{self.postgres_user}:{self.postgres_password}@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
    
    # Neo4j
    neo4j_uri: str = "bolt://localhost:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str = "neo4j_secure_password_change_me"
    
    # Redis
    redis_host: str = "localhost"
    redis_port: int = 6379
    redis_password: str = ""
    redis_url: Optional[str] = None
    
    @property
    def redis_connection_url(self) -> str:
        if self.redis_url:
            return self.redis_url
        if self.redis_password:
            return f"redis://:{self.redis_password}@{self.redis_host}:{self.redis_port}/0"
        return f"redis://{self.redis_host}:{self.redis_port}/0"
    
    # Temporal
    temporal_host: str = "localhost"
    temporal_port: int = 7233
    temporal_namespace: str = "aiops"
    temporal_task_queue: str = "incident-workflow"
    
    @property
    def temporal_address(self) -> str:
        return f"{self.temporal_host}:{self.temporal_port}"
    
    # Kubernetes
    kubeconfig: Optional[str] = None
    kubernetes_default_namespace: str = "default"
    
    # Prometheus
    prometheus_url: str = "http://localhost:9090"
    
    # Loki
    loki_url: str = "http://localhost:3100"
    
    # Grafana
    grafana_url: str = "http://localhost:3000"
    grafana_api_key: Optional[str] = None
    
    # OpenTelemetry
    otel_exporter_otlp_endpoint: str = "http://localhost:4317"
    otel_service_name: str = "aiops-platform"
    
    # LLM
    llm_provider: str = Field(default="gemini", description="openai, gemini, ollama")
    openai_api_key: Optional[str] = None
    openai_model: str = "gpt-4-turbo-preview"
    google_api_key: Optional[str] = None
    gemini_model: str = "gemini-pro"
    ollama_url: str = "http://localhost:11434"
    ollama_model: str = "llama2"
    
    # OPA
    opa_url: str = "http://localhost:8181"
    opa_policy_path: str = "/v1/data/remediation"
    
    # Slack
    slack_bot_token: Optional[str] = None
    slack_signing_secret: Optional[str] = None
    slack_approval_channel: Optional[str] = None
    
    # Jira
    jira_url: Optional[str] = None
    jira_user: Optional[str] = None
    jira_api_token: Optional[str] = None
    jira_project_key: Optional[str] = None
    
    # Security
    api_key_header: str = "X-API-Key"
    rate_limit_per_minute: int = 100
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:3000"])
    
    @field_validator('cors_origins', mode='before')
    @classmethod
    def parse_cors_origins(cls, v):
        if isinstance(v, str):
            import json
            try:
                return json.loads(v)
            except json.JSONDecodeError:
                return [v]
        return v
    
    # Evidence Collection
    evidence_time_window_minutes: int = 15
    max_log_lines: int = 1000
    max_metric_points: int = 500
    
    # Remediation
    remediation_auto_approve_dev: bool = True
    remediation_auto_approve_staging: bool = False
    remediation_auto_approve_prod: bool = False
    remediation_max_blast_radius: float = 50.0
    remediation_verification_wait_seconds: int = 120


@lru_cache
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()


# Convenience instance
settings = get_settings()
