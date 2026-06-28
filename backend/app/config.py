import os
from dataclasses import dataclass
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parents[1]
ENV_PATH = BASE_DIR / ".env"


def load_dotenv(path: Path = ENV_PATH) -> None:
    if not path.exists():
        return

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


load_dotenv()


@dataclass(frozen=True)
class Settings:
    vocab_env: str = os.getenv("VOCAB_ENV", "development")
    database_url: str | None = os.getenv("DATABASE_URL")
    database_path: str | None = os.getenv("DATABASE_PATH")
    allowed_origins: str = os.getenv("ALLOWED_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173")
    google_api_key: str | None = os.getenv("GOOGLE_API_KEY")
    llm_model: str = os.getenv("LLM_MODEL", "gemini-2.5-flash")
    openrouter_api_key: str | None = os.getenv("OPENROUTER_API_KEY")
    openrouter_model: str | None = os.getenv("OPENROUTER_MODEL")
    llm_timeout_seconds: int = int(os.getenv("LLM_TIMEOUT_SECONDS", "45"))
    report_timezone: str = os.getenv("REPORT_TIMEZONE", "Asia/Taipei")
    app_public_url: str | None = os.getenv("APP_PUBLIC_URL")
    discord_webhook_url: str | None = os.getenv("DISCORD_WEBHOOK_URL")
    notification_poll_seconds: int = int(os.getenv("NOTIFICATION_POLL_SECONDS", "60"))


settings = Settings()
