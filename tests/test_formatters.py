"""Unit tests for utils/formatters.py and bot/helpers.py date parsing."""
from datetime import date

import pytest

from utils.formatters import (
    format_number, format_pct, format_date, format_period, format_growth,
    format_context_for_ai,
)
from bot.helpers import parse_period_input, parse_comparison_input


# ── formatters ──

def test_format_number():
    assert format_number(12540) == "12 540"
    assert format_number(0) == "0"


def test_format_pct():
    assert format_pct(5.3) == "+5,3%"
    assert format_pct(-2.1) == "-2,1%"


def test_format_period():
    assert format_period(date(2026, 9, 1), date(2026, 9, 7)) == "1–7 сентября"
    assert format_period(date(2026, 9, 1), date(2026, 9, 1)) == "1 сентября"
    assert format_period(date(2026, 8, 28), date(2026, 9, 3)) == "28 августа – 3 сентября"


def test_format_growth():
    assert format_growth(640) == "📈 +640"
    assert format_growth(-120) == "📉 -120"


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


# ── AI context completeness (regression: silent truncation misled the model) ──

def _big_report_context(days=29, posts=17, stories=20):
    daily_stats = [
        {"date": f"{i + 1:02d}.09", "reach": 100 + i, "followers": 5,
         "views": 1000 + i, "accounts_engaged": 50 + i}
        for i in range(days)
    ]
    publications = []
    for i in range(posts):
        publications.append({
            "date": f"2026-09-{(i % 28) + 1:02d}",
            "posts": [{
                "type": "REELS" if i % 2 else "IMAGE",
                "caption": f"unique-caption-{i:03d} " + "x" * 40,
                "likes": 10 + i, "comments": 1, "saved": 2, "shares": 3,
                "reach": 500 + i, "views": 900 + i,
            }],
        })
    return {
        "account": "@test — Test",
        "period": "1–29 сентября",
        "type": "report",
        "stats": {"followers_current": 5000, "followers_growth": 840, "reach_total": 31253,
                  "views_total": 177197, "accounts_engaged_total": 1972,
                  "reach_avg_daily": 1078},
        "content": {"total_posts": posts, "total_reels": 8, "total_videos": 0,
                    "total_images": 4, "total_carousels": 5, "avg_likes": 73,
                    "avg_reach": 1828, "engagement_rate": 5.1,
                    "total_interactions": 1570, "total_reach": 31082,
                    "partial_insights_posts": 0, "legacy_unknown_insights": 0},
        "stories": {"total_stories": stories, "total_views": 4232, "avg_views": 423,
                    "total_reach": 3184, "total_replies": 5, "total_shares": 18,
                    "exit_rate": 20.4, "partial_insights_stories": 0,
                    "legacy_unknown_insights": 10},
        "stories_list": [
            {"date": f"2026-09-{(i % 28) + 1:02d}", "views": 100 + i, "reach": 90 + i,
             "replies": 1, "shares": 2}
            for i in range(stories)
        ],
        "best_post": "unique-caption-016\n❤️ 190 | 💬 17 | 💾 8 | 📤 8 | Охват 2 222",
        "daily_stats": daily_stats,
        "publications": publications,
        "top_posts": [{"media_type": "Reels", "value": 222, "reach": 2222,
                       "caption": "unique-caption-016"}],
        "format_performance": {"REELS": {"posts": 8, "reach_avg": 1828, "engagement_rate": 5.1}},
        "data_quality": {"available_days": 29, "expected_days": 29, "missing_days": 0,
                         "partial_days": 0, "legacy_unknown_days": 0,
                         "metric_missing_days": {}, "complete": True},
        "ai_analysis": "анализ",
    }


def test_ai_context_contains_all_days_and_posts_for_month():
    """Regression: daily[:14] / publications[:12] made the model believe the
    report itself was incomplete. Every day and every post must be present."""
    ctx = _big_report_context(days=29, posts=17)
    text = format_context_for_ai(ctx)
    for i in range(29):
        assert f"{i + 1:02d}.09" in text
    for i in range(17):
        assert f"unique-caption-{i:03d}" in text
    # no silent trimming for a context that fits the budget:
    # no omission markers (the disclaimer mentions «опущено…» by design)
    assert "…[опущено" not in text
    assert len(text) <= 14000


def test_ai_context_marks_omissions_when_over_budget():
    """When the context is over budget, every trim must carry an explicit marker."""
    ctx = _big_report_context(days=300, posts=300, stories=300)
    text = format_context_for_ai(ctx)
    assert len(text) <= 14000
    assert "…[опущено" in text
    assert "из 300" in text  # «опущено N из 300 …» — counts are explicit
    assert "полные данные в отчёте" in text


def test_ai_context_disclaimer_present():
    text = format_context_for_ai(_big_report_context(days=3, posts=2))
    assert "Маркер «опущено…» означает сокращение контекста диалога, а не неполноту" in text


# ── message splitting (regression: «ком/ментариев» mid-word breaks) ──

def test_split_message_text_keeps_words_intact():
    from bot.helpers import split_message_text

    line = "слово " * 680  # ~4080 chars — just under the limit
    text = "\n".join([line, "короткая строка", line])
    chunks = split_message_text(text, max_len=4096)
    assert chunks
    assert all(len(c) <= 4096 for c in chunks)
    assert "\n".join(chunks) == text  # lossless
    # splitting happens at line boundaries only — words stay intact
    assert all(token == "слово" for token in line.split())
    for chunk in chunks:
        assert set(chunk.split()) <= {"слово", "короткая", "строка"}


def test_split_message_text_hard_splits_only_monster_lines():
    from bot.helpers import split_message_text

    monster = "x" * 10000  # single word longer than the limit — last resort
    chunks = split_message_text(monster, max_len=4096)
    assert "".join(chunks) == monster
    assert all(len(c) <= 4096 for c in chunks)


def test_split_message_text_preserves_short_lines_unsplit():
    from bot.helpers import split_message_text

    text = "⚠️ Контроль действий: сумма лайков, комментариев, сохранений и репостов"
    chunks = split_message_text(text, max_len=4096)
    assert chunks == [text]


def test_split_message_text_calendar_header_move_respects_limit():
    """Regression: moving a trailing «📅 ...» header to the next chunk must
    never push that chunk over max_len (Telegram rejects oversized messages)."""
    from bot.helpers import split_message_text

    max_len = 60
    header = "📅 25 сентября (Пт):"
    posts = [f'  🎬 Reels | "пост {i}"' + "ю" * 20 for i in range(10)]
    text = "\n".join(["а" * 30, header] + posts)

    chunks = split_message_text(text, max_len=max_len)

    assert chunks
    assert all(0 < len(c) <= max_len for c in chunks)
    # the header never ends a chunk — it always travels with its posts
    for chunk in chunks[:-1]:
        assert not chunk.split("\n")[-1].startswith("📅 ")
    assert "\n".join(chunks) == text  # lossless
