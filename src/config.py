"""Application configuration via environment variables."""

from pydantic_settings import BaseSettings


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
