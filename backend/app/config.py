from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    environment: str = "development"

    database_url: str = "postgresql+asyncpg://jarvis:jarvis@localhost:5432/jarvis"
    redis_url: str = "redis://localhost:6379"

    jwt_secret: str = "dev-secret-change-in-prod"
    jwt_algorithm: str = "RS256"
    access_token_expire_minutes: int = 15
    refresh_token_expire_days: int = 30

    assemblyai_api_key: str = ""
    anthropic_api_key: str = ""

    r2_access_key: str = ""
    r2_secret_key: str = ""
    r2_bucket: str = "jarvis-audio"
    r2_endpoint_url: str = ""

    sentry_dsn: str = ""

    # Dev flags
    mock_assemblyai: bool = False
    mock_graph_extraction: bool = False

    # Identity thresholds
    min_audio_seconds: float = 8.0
    identity_low_confidence: float = 0.80
    identity_high_confidence: float = 0.90
    wearer_match_threshold: float = 0.78

    # Fact extraction
    fact_max_words_per_chunk: int = 400
    fact_max_per_session: int = 10

    # Recap
    recap_timeout_seconds: float = 7.0


settings = Settings()
