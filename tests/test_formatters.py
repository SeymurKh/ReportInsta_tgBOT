"""Unit tests for utils/formatters.py and bot/helpers.py date parsing."""
from datetime import date

import pytest

from utils.formatters import (
    format_number, format_pct, format_date, format_period, format_growth,
    truncate_text, format_context_for_ai,
)
from bot.helpers import parse_period_input, parse_comparison_input


# ── formatters ──

def test_format_number():
    assert format_number(12540) == "12 540"
    assert format_number(0) == "0"


def test_format_pct():
    assert format_pct(5.3) == "+5.3%"
    assert format_pct(-2.1) == "-2.1%"


def test_format_period():
    assert format_period(date(2026, 9, 1), date(2026, 9, 7)) == "1–7 сентября"
    assert format_period(date(2026, 9, 1), date(2026, 9, 1)) == "1 сентября"
    assert format_period(date(2026, 8, 28), date(2026, 9, 3)) == "28 августа – 3 сентября"


def test_format_growth():
    assert format_growth(640) == "📈 +640"
    assert format_growth(-120) == "📉 -120"


def test_truncate_text():
    assert truncate_text("abc", 10) == "abc"
    assert len(truncate_text("a" * 5000)) == 4096


def test_format_context_for_ai_report():
    ctx = {
        "account": "@test — Test",
        "period": "1–7 сентября",
        "type": "report",
        "stats": {"followers_end": 1000, "followers_growth": 50, "reach_total": 5000,
                  "views_total": 8000, "accounts_engaged_total": 600},
        "content": {"total_posts": 3, "total_reels": 1, "total_videos": 0,
                    "total_images": 2, "total_carousels": 0, "avg_likes": 10,
                    "avg_reach": 500, "engagement_rate": 4.5},
        "stories": {"total_stories": 2, "total_views": 300, "avg_views": 150,
                    "total_reach": 250, "total_replies": 5, "total_shares": 1,
                    "exit_rate": 12.0},
        "stories_list": [{"date": "02.09 12:00", "views": 200, "reach": 180,
                          "replies": 3, "shares": 1}],
        "daily_stats": [],
        "publications": [],
        "best_post": "нет данных",
    }
    text = format_context_for_ai(ctx)
    assert "@test" in text
    assert "Сторис: 2 шт" in text
    assert "300" in text


def test_format_context_for_ai_comparison():
    ctx = {
        "account": "@test — Test",
        "period": "1–7 сентября vs 8–14 сентября",
        "type": "comparison",
        "period1": {"name": "1–7 сентября",
                    "stats": {"followers_end": 900, "reach_total": 4000, "views_total": 7000},
                    "content": {"engagement_rate": 4.0},
                    "stories": {"total_stories": 1, "total_views": 100, "total_replies": 2}},
        "period2": {"name": "8–14 сентября",
                    "stats": {"followers_end": 1000, "reach_total": 5000, "views_total": 8000},
                    "content": {"engagement_rate": 4.5},
                    "stories": {"total_stories": 2, "total_views": 300, "total_replies": 5}},
    }
    text = format_context_for_ai(ctx)
    assert "Период 1" in text
    assert "Сторис период 2: 2 шт" in text


# ── date parsing ──

TODAY = date(2026, 9, 21)


def test_parse_period_single_day():
    assert parse_period_input("15.09", TODAY) == (date(2026, 9, 15), date(2026, 9, 15))


def test_parse_period_range():
    assert parse_period_input("01.09-15.09", TODAY) == (date(2026, 9, 1), date(2026, 9, 15))


def test_parse_period_reversed():
    with pytest.raises(ValueError):
        parse_period_input("15.09-01.09", TODAY)


def test_parse_period_invalid_date():
    with pytest.raises(ValueError):
        parse_period_input("31.02", TODAY)


def test_parse_period_bad_format():
    with pytest.raises(ValueError):
        parse_period_input("1 сентября", TODAY)


def test_parse_comparison_ok():
    result = parse_comparison_input("01.09-07.09 vs 08.09-14.09", TODAY)
    assert result == (date(2026, 9, 1), date(2026, 9, 7), date(2026, 9, 8), date(2026, 9, 14))


def test_parse_comparison_bad():
    with pytest.raises(ValueError):
        parse_comparison_input("01.09-07.09 против 08.09-14.09", TODAY)
