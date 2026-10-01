"""Calendar-day policy for user-facing periods.

Users think in APP_TIMEZONE days (e.g. Asia/Baku), while Instagram returns
day-keyed insights as UTC days. All *selection* of calendar days (report
periods, calendars, daily digests) uses APP_TIMEZONE so the bot's "today" and
"yesterday" match what the user sees; daily stats rows themselves remain
Instagram (UTC) days.
"""

from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from config import settings


def app_tz() -> ZoneInfo:
    return ZoneInfo(settings.APP_TIMEZONE)


def app_now(now: datetime | None = None) -> datetime:
    """Current moment expressed in APP_TIMEZONE."""
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return current.astimezone(app_tz())


def app_today(now: datetime | None = None) -> date:
    """Today's calendar date in APP_TIMEZONE."""
    return app_now(now).date()