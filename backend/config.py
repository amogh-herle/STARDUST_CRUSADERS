"""
Backend Configuration
Reads from environment variables with sensible defaults for local dev.
In production (Docker Compose), these are injected via docker-compose.yml.
"""

from pydantic_settings import BaseSettings, SettingsConfigDict
import os


class Settings(BaseSettings):
    # Database
    DATABASE_URL: str = "postgresql+asyncpg://cidecode:cidecode@localhost:5432/cidecode"
    DATABASE_URL_SYNC: str = "postgresql+psycopg2://cidecode:cidecode@localhost:5432/cidecode"

    # App
    APP_NAME: str = "CIDECODE Bank Statement Analysis System"
    APP_VERSION: str = "1.0.0"
    DEBUG: bool = True

    # CORS — React dev server
    CORS_ORIGINS: list[str] = [
        "http://localhost:5173",
        "http://localhost:3000",
        "http://localhost:3001",
    ]

    # File upload
    UPLOAD_DIR: str = "uploads"
    MAX_UPLOAD_SIZE_MB: int = 50

    # Phase pipeline paths (relative to project root)
    PHASE6_INGEST_SCRIPT: str = "../phase6/ingest.py"
    PHASE7_CLEAN_SCRIPT: str = "../phase7/clean.py"

    # LLM and RAG assistant
    LLM_PROVIDER: str = "gemini"
    CHAT_MODEL: str = "gemini-2.5-flash"
    REPORT_MODEL: str = "gemini-2.5-pro"
    GEMINI_API_KEY: str = "AQ.Ab8RN6IuqqZlx50HrA8frxFCOBMkSsFHTIpRL8Gbh9q5bH8g-Q"
    OPENAI_API_KEY: str = ""
    CLAUDE_API_KEY: str = ""
    ANTHROPIC_API_KEY: str = ""

    # Qwen3-8B local assistant (tool-calling mode via Ollama)
    QWEN_ENABLED: bool = False       # flip to True to use Qwen instead of Gemini/Claude
    QWEN_BASE_URL: str = "http://localhost:11434"   # Ollama default
    QWEN_MODEL_NAME: str = "qwen3:8b"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()
