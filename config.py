import os
from typing import Optional
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", "../.env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # LLM Settings
    LLM_PROVIDER: str = Field(
        default="auto",
        description="LLM provider: 'openai', 'gemini', 'groq', 'anthropic', 'ollama', 'heuristic', or 'auto'",
    )
    ALLOW_HEURISTIC: bool = Field(
        default=False,
        description="Whether to permit offline heuristic mode when no LLM key or endpoint is found",
    )
    OPENAI_API_KEY: Optional[str] = None
    GEMINI_API_KEY: Optional[str] = None
    GOOGLE_API_KEY: Optional[str] = None
    GROQ_API_KEY: Optional[str] = None
    ANTHROPIC_API_KEY: Optional[str] = None
    OPENAI_BASE_URL: Optional[str] = None
    MODEL_NAME: Optional[str] = None

    # Composio & Search Settings
    COMPOSIO_API_KEY: Optional[str] = None
    TAVILY_API_KEY: Optional[str] = None
    SERPAPI_API_KEY: Optional[str] = None
    BRAVE_API_KEY: Optional[str] = None

    # Concurrency and Performance
    CONCURRENCY_LIMIT: int = Field(default=5, description="Async concurrency limit (default 5)")
    REQUEST_TIMEOUT: int = Field(default=20, description="HTTP request timeout in seconds")
    MAX_RETRIES: int = Field(default=3, description="Max retries for HTTP requests")
    RATE_LIMIT_DELAY: float = Field(default=0.5, description="Polite delay between external requests")

    # Paths & Verification
    CACHE_DIR: str = "cache"
    RESULTS_DIR: str = "results"
    VERIFY_QUOTES: bool = True
    MIN_CONFIDENCE_THRESHOLD: float = 0.65


settings = Settings()
