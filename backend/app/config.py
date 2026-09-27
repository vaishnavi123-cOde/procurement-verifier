"""Application configuration loaded from environment variables.

All secrets and infra addresses must come from the environment / .env file.
Never hardcode secrets in source code.
"""

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent
PROJECT_ROOT = BACKEND_DIR.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(PROJECT_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- Application ---
    app_name: str = "Procurement Intelligence & Verification System"
    app_version: str = "0.1.0"
    environment: str = "development"
    debug: bool = False

    # --- Database ---
    # Default to SQLite for zero-dependency local development.
    # In Docker this is set to the PostgreSQL connection string.
    database_url: str = f"sqlite:///{(PROJECT_ROOT / 'data' / 'procurement.db').as_posix()}"
    db_pool_size: int = 5
    db_max_overflow: int = 10
    db_connect_timeout: int = 30

    # --- Vector store ---
    # "server" -> connect to Qdrant (persistent production), "memory" -> in-process
    # Qdrant (zero-dependency dev/test). When QDRANT_URL (``qdrant_url``) is set it
    # wins over ``vector_host``/``vector_port``.
    vector_store_mode: str = "memory"
    vector_host: str = "localhost"
    vector_port: int = 6333
    vector_collection: str = "procurement_documents"
    # QDRANT_URL / QDRANT_API_KEY (preferred in production; also accepted).
    qdrant_url: str = ""
    qdrant_api_key: str = Field(default="", repr=False)
    # QDRANT_COLLECTION override for ``vector_collection``.
    qdrant_collection: str = ""
    embedding_dim: int = 384
    embedding_provider: str = "hash"  # "hash" | "ollama" | "transformers"

    # --- LLM ---
    # "none" (deterministic fallback) | "ollama" | "api"
    llm_provider: str = "none"
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "qwen2.5:1.5b"
    llm_api_base_url: str = "https://api.openai.com/v1"
    llm_api_model: str = "gpt-4o-mini"
    llm_api_key: str = Field(default="", repr=False)
    llm_timeout_seconds: float = 60.0

    # --- Uploads ---
    upload_dir: Path = PROJECT_ROOT / "data" / "uploads"
    max_upload_size_mb: int = 25
    allowed_extensions: list[str] = [".pdf"]

    # --- Datasets ---
    # Root folder holding generated benchmark datasets. Synthetic cases are
    # discovered at {dataset_root}/synthetic/cases/{case_id}/.
    dataset_root: Path = PROJECT_ROOT / "data"

    # --- Security ---
    auth_enabled: bool = False
    auth_jwt_secret: str = Field(default="dev-only-change-me", repr=False)
    access_token_ttl_minutes: int = 120
    # Bootstrap secret required to mint API tokens when auth is enabled.
    # Default is insecure by design so local development stays one-step; set a
    # strong value in production.
    auth_bootstrap_token: str = Field(default="dev-bootstrap-token", repr=False)

    # --- HTTP / CORS ---
    # Comma/JSON list of allowed origins for the browser API calls.
    api_prefix: str = "/api"
    cors_origins: list[str] = ["http://localhost:5173", "http://localhost:3000"]
    cors_allow_credentials: bool = True

    # --- Rate limiting (lightweight, in-memory; off by default) ---
    rate_limit_enabled: bool = False
    rate_limit_per_minute: int = 120

    # --- Error responses ---
    # When false (production), unhandled exceptions return a generic message and
    # the full detail is only written to the structured server log. When true,
    # internal error details are echoed back (useful for debugging).
    detail_errors: bool = False

    # --- Analysis ---
    # Confidence below this -> the field is treated as unverified / attribution starved.
    abstention_confidence_threshold: float = 0.5
    # Confidence below this on the recommendation -> abstain.
    recommendation_confidence_threshold: float = 0.6
    default_currency: str = "INR"

    # --- Observability ---
    otel_enabled: bool = False
    otel_endpoint: str = "http://localhost:4317"
    log_level: str = "INFO"

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")

    @property
    def active_collection(self) -> str:
        """QDRANT_COLLECTION overrides the defaults when provided."""
        return self.qdrant_collection or self.vector_collection

    @property
    def upload_dir_path(self) -> Path:
        path = Path(self.upload_dir)
        path.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def show_error_detail(self) -> bool:
        """True when internal error messages may leak to the client."""
        return self.debug or self.detail_errors


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()