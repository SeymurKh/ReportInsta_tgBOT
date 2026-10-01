"""Calendar-day policy tests: user-facing days roll over at APP_TIMEZONE midnight."""

from datetime import date, datetime, timezone
from unittest.mock import patch

from config import settings
from reports.generator import resolve_period
from utils.timezones import app_now, app_today


def test_app_today_rolls_over_at_local_midnight_not_utc_midnight():
    with patch.object(settings, "APP_TIMEZONE", "Asia/Baku"):
        # 21:30 UTC on Oct 1 = 01:30 on Oct 2 in Asia/Baku (UTC+4)
        assert app_today(datetime(2026, 10, 1, 21, 30, tzinfo=timezone.utc)) == date(2026, 10, 2)
        # 19:30 UTC on Oct 1 = 23:30 on Oct 1 in Baku — the same calendar day
        assert app_today(datetime(2026, 10, 1, 19, 30, tzinfo=timezone.utc)) == date(2026, 10, 1)


def test_app_now_accepts_naive_utc_input():
    with patch.object(settings, "APP_TIMEZONE", "Asia/Baku"):
        assert app_now(datetime(2026, 10, 1, 21, 30)).date() == date(2026, 10, 2)


def test_resolve_period_uses_app_timezone_today():
    """Regression: report periods were computed from the UTC date, so between
    00:00 and 04:00 APP_TIMEZONE the default week ended a day too early."""
    with patch.object(settings, "APP_TIMEZONE", "Asia/Baku"), \
         patch("reports.generator.app_today", return_value=date(2026, 10, 2)):
        assert resolve_period("day") == (date(2026, 10, 1), date(2026, 10, 1))
        assert resolve_period("week") == (date(2026, 9, 25), date(2026, 10, 1))
        assert resolve_period("month") == (date(2026, 9, 2), date(2026, 10, 1))