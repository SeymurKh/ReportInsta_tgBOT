import json
import os
from dotenv import load_dotenv

load_dotenv()


def _get_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


class Settings:
    # Telegram
    BOT_TOKEN: str = os.getenv("BOT_TOKEN", "")

    # OpenAI
    OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")
    OPENAI_MODEL: str = os.getenv("OPENAI_MODEL", "gpt-5.6-luna")  # Unified model for reports and chat
    OPENAI_REASONING_REPORT: str = os.getenv("OPENAI_REASONING_REPORT", "low")
    OPENAI_REASONING_CHAT: str = os.getenv("OPENAI_REASONING_CHAT", "low")

    # Database
    DATABASE_URL: str = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///data/instagram_bot.db")

    # Admin
    ADMIN_TELEGRAM_ID: int = int(os.getenv("ADMIN_TELEGRAM_ID", "0") or "0")

    # Instagram API
    INSTAGRAM_API_VERSION: str = os.getenv("INSTAGRAM_API_VERSION", "v25.0")
    INSTAGRAM_BASE_URL: str = "https://graph.instagram.com"

    # Instagram Accounts (loaded from .env INSTAGRAM_ACCOUNTS as JSON)
    ACCOUNTS: list[dict] = []
    ACCOUNTS_JSON_ERROR: str | None = None
    try:
        _raw_accounts = os.getenv("INSTAGRAM_ACCOUNTS", "[]")
        ACCOUNTS = json.loads(_raw_accounts)
        if not isinstance(ACCOUNTS, list):
            ACCOUNTS = []
            ACCOUNTS_JSON_ERROR = "INSTAGRAM_ACCOUNTS must be a JSON array"
    except json.JSONDecodeError as e:
        ACCOUNTS_JSON_ERROR = f"INSTAGRAM_ACCOUNTS contains invalid JSON: {e}"

    # Stories tracking
    STORIES_POLL_ENABLED: bool = _get_bool("STORIES_POLL_ENABLED", True)
    STORIES_POLL_INTERVAL_HOURS: float = float(os.getenv("STORIES_POLL_INTERVAL_HOURS", "4"))
    # How long after publishing we keep refreshing story insights (IG keeps them ~24h)
    STORIES_INSIGHTS_WINDOW_HOURS: int = int(os.getenv("STORIES_INSIGHTS_WINDOW_HOURS", "26"))

    # Daily auto-report to admin
    DAILY_REPORT_ENABLED: bool = _get_bool("DAILY_REPORT_ENABLED", True)
    DAILY_REPORT_HOUR: int = int(os.getenv("DAILY_REPORT_HOUR", "9"))  # local server time

    # Data freshness: data older than this is considered stable and served from DB cache
    CACHE_FRESHNESS_HOURS: int = int(os.getenv("CACHE_FRESHNESS_HOURS", "48"))

    # Full background sync (daily stats + posts for a rolling window)
    DATA_SYNC_ENABLED: bool = _get_bool("DATA_SYNC_ENABLED", True)
    DATA_SYNC_INTERVAL_HOURS: float = float(os.getenv("DATA_SYNC_INTERVAL_HOURS", "6"))
    DATA_SYNC_WINDOW_DAYS: int = int(os.getenv("DATA_SYNC_WINDOW_DAYS", "30"))

    # Tiered post-insights refresh (post age -> refresh policy)
    POSTS_HOT_DAYS: int = int(os.getenv("POSTS_HOT_DAYS", "3"))       # <= N days: every sync
    POSTS_WARM_DAYS: int = int(os.getenv("POSTS_WARM_DAYS", "14"))    # <= N days: once a day
    POSTS_WARM_INTERVAL_HOURS: int = int(os.getenv("POSTS_WARM_INTERVAL_HOURS", "24"))

    # Chart settings
    CHART_STYLE: str = "seaborn-v0_8-whitegrid"
    CHART_COLORS: list[str] = ["#2196F3", "#4CAF50", "#FF9800", "#F44336"]
    CHART_SIZE: tuple = (10, 6)

    # Report periods in days
    PERIODS: dict[str, int] = {
        "day": 1,
        "week": 7,
        "month": 30,
    }

    def validate(self) -> tuple[list[str], list[str]]:
        """Returns (critical_errors, warnings). Critical errors prevent startup."""
        errors: list[str] = []
        warnings: list[str] = []

        if not self.BOT_TOKEN:
            errors.append("BOT_TOKEN is not set")
        if not self.ADMIN_TELEGRAM_ID:
            errors.append("ADMIN_TELEGRAM_ID is not set or 0 — nobody will be able to use the bot")
        if self.ACCOUNTS_JSON_ERROR:
            errors.append(self.ACCOUNTS_JSON_ERROR)
        elif not self.ACCOUNTS:
            errors.append("INSTAGRAM_ACCOUNTS is empty — no Instagram accounts configured")
        else:
            for i, acc in enumerate(self.ACCOUNTS):
                for key in ("name", "user_id", "access_token"):
                    if not acc.get(key):
                        errors.append(f"INSTAGRAM_ACCOUNTS[{i}] is missing '{key}'")
        if not self.OPENAI_API_KEY:
            warnings.append("OPENAI_API_KEY is not set — AI analysis and chat will be unavailable")

        return errors, warnings


settings = Settings()