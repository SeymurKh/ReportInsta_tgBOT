import os
from dotenv import load_dotenv

load_dotenv()


class Settings:
    # Telegram
    BOT_TOKEN: str = os.getenv("BOT_TOKEN", "")

    # OpenAI
    OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")
    OPENAI_MODEL: str = "gpt-4.1"

    # Database
    DATABASE_URL: str = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///data/instagram_bot.db")

    # Admin
    ADMIN_TELEGRAM_ID: int = int(os.getenv("ADMIN_TELEGRAM_ID", "0"))

    # Instagram API
    INSTAGRAM_API_VERSION: str = "v25.0"
    INSTAGRAM_BASE_URL: str = "https://graph.instagram.com"

    # Instagram Accounts
    ACCOUNTS: list[dict] = [
        {
            "name": "biblioteka.baku",
            "user_id": "17841420413366121",
            "access_token": "IGAAhABiE4S8VBZAGE3TXUzcTFNcEZAXSjBFLVBteU9BVUtBbjZAyc2lZANEZA0Q1RDT2tZAVnFuNnljWkZAXanFxV29lbWVSU2UteHZASWllhWXhWUmdxNjAzUHNsX1R4NEhtT1pYdUtZAODJJQjUtcTVOaFc0bE1BSTlIOFJLeEFraUtqRQZDZD",
        },
    ]

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


settings = Settings()