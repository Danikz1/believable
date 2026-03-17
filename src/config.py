"""Application configuration via environment variables."""

import os
import warnings

from pydantic_settings import BaseSettings

_DEFAULT_ADMIN_KEY = "bm-admin-key"


class Settings(BaseSettings):
    """Believable Minds configuration."""

    # Database
    database_url: str = "postgresql://bm:bm_dev_password@localhost:5432/believable_minds"

    # Stage 2: YouTube
    youtube_api_key: str = ""

    # Stage 3: Transcription
    deepgram_api_key: str = ""
    assemblyai_api_key: str = ""
    transcription_provider: str = "assemblyai"  # 'assemblyai', 'deepgram', or 'whisperx'

    # Stage 4+: LLM
    qwen_api_key: str = ""
    qwen_api_base: str = ""
    llm_provider: str = "qwen"  # 'qwen', 'anthropic', or 'openai'
    openai_model: str = "gpt-5.2"
    anthropic_model: str = "claude-sonnet-4-6"
    qwen_model: str = "qwen3.5-plus"

    # Stage 5: Embeddings
    openai_api_key: str = ""
    embedding_dimensions: int = 1536

    # Stage 6: Mukhtar.AI
    anthropic_api_key: str = ""
    admin_api_key: str = "bm-admin-key"

    # YouTube proxy (for avoiding bot detection on cloud IPs)
    # e.g. "socks5://user:pass@proxy:1080" or "http://proxy:8080"
    youtube_proxy: str = ""

    # Database connection pooling
    db_pool_size: int = 10
    db_max_overflow: int = 20
    db_pool_recycle: int = 1800  # seconds — recycle connections after 30 min
    db_pool_pre_ping: bool = True  # verify connections before use

    # Stage 8: Delivery
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_pass: str = ""
    smtp_to: str = ""
    email_from: str = ""
    email_to: str = ""

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()

# Warn loudly if using the default admin key in production
if (
    settings.admin_api_key == _DEFAULT_ADMIN_KEY
    and os.environ.get("RAILWAY_ENVIRONMENT")
):
    warnings.warn(
        "ADMIN_API_KEY is using the insecure default value! "
        "Set a strong ADMIN_API_KEY in your environment variables.",
        stacklevel=1,
    )
