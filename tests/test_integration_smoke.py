"""Offline smoke tests for user-visible report flows."""

import asyncio
from datetime import date, datetime
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from openpyxl import load_workbook

from bot.helpers import build_excel_from_context, send_long_text
from bot.helpers import parse_comparison_input, parse_period_input
from reports.generator import generate_comparison_periods_report
from reports.generator import generate_report


def test_period_parser_rejects_invalid_and_future_dates():
    today = date(2026, 9, 24)
    assert parse_period_input("01.09-07.09", today) == (
        date(2026, 9, 1), date(2026, 9, 7)
    )

    for value in ("31.02", "25.09", "07.09-01.09"):
        try:
            parse_period_input(value, today)
        except ValueError:
            pass
        else:
            raise AssertionError(f"invalid period accepted: {value}")


def test_comparison_parser_preserves_both_periods():
    result = parse_comparison_input(
        "01.09-07.09 vs 08.09-14.09", date(2026, 9, 24)
    )
    assert result == (
        date(2026, 9, 1), date(2026, 9, 7),
        date(2026, 9, 8), date(2026, 9, 14),
    )


def test_excel_dialogue_is_wrapped_and_readable():
    context = {
        "account": "@demo — Demo",
        "account_username": "demo",
        "period": "1–7 сентября",
        "stats": {},
        "content": {},
        "stories": {},
        "stories_list": [],
        "daily_stats": [],
        "publications": [],
        "ai_analysis": "Короткий анализ",
    }
    dialogue = [
        {"role": "user", "content": "Какой формат лучше?"},
        {"role": "assistant", "content": "Первая строка\nВторая строка"},
    ]

    workbook_bytes, filename = build_excel_from_context(context, dialogue)
    workbook = load_workbook(BytesIO(workbook_bytes))
    sheet = workbook["Диалог с AI"]

    assert filename.endswith(".xlsx")
    assert sheet["B6"].alignment.wrap_text is True
    assert sheet["B6"].value == "Первая строка\nВторая строка"
    assert sheet.row_dimensions[6].height >= 24


def test_excel_comparison_contains_daily_and_format_sections():
    period = {"name": "1–7 сентября", "stats": {"reach_total": 700, "views_total": 1400, "accounts_engaged_total": 70}, "content": {}, "stories": {}}
    context = {
        "account": "@demo — Demo", "account_username": "demo", "period": "comparison",
        "period1": period, "period2": period,
        "period_days": {"available1": 7, "available2": 14},
        "comparison_formats": {"period1": {"REELS": {"posts": 2, "engagement_rate": 5.0}}, "period2": {}},
        "comparison_top_posts": {"period1": [{"caption": "Top", "value": 20}], "period2": []},
    }
    workbook_bytes, _ = build_excel_from_context(context, [])
    workbook = load_workbook(BytesIO(workbook_bytes))
    sheet = workbook["Сравнение"]
    values = [cell.value for row in sheet.iter_rows() for cell in row if cell.value]
    assert "Средние значения за день" in values
    assert "Форматы контента" in values
    assert "Лучшие публикации" in values


def test_excel_percent_cells_use_native_percent_format():
    """Regression: mobile Excel multiplies the custom literal format 0.0"%"
    by 100 — percent cells must use the native 0.0% format with fractions."""
    context = {
        "account": "@demo — Demo",
        "account_username": "demo",
        "period": "1–7 сентября",
        "stats": {"followers_growth_pct": 12.4},
        "content": {"engagement_rate": 8.9},
        "stories": {"exit_rate": 15.0},
        "stories_list": [],
        "daily_stats": [],
        "publications": [],
        "ai_analysis": "Короткий анализ",
    }
    workbook = load_workbook(BytesIO(build_excel_from_context(context, [])[0]))

    summary = workbook["Сводка"]
    assert summary["D9"].number_format == "0.0%"
    assert abs(summary["D9"].value - 0.089) < 1e-9

    percent_cells = [
        cell
        for sheet in workbook.worksheets
        for row in sheet.iter_rows()
        for cell in row
        if "%" in cell.number_format
    ]
    assert len(percent_cells) >= 4
    for cell in percent_cells:
        assert cell.number_format == "0.0%"
        assert cell.value is None or 0 <= cell.value <= 1

    comparison_context = {
        "account": "@demo — Demo",
        "account_username": "demo",
        "period": "comparison",
        "period1": {"name": "P1", "stats": {}, "content": {"engagement_rate": 8.9}, "stories": {}},
        "period2": {"name": "P2", "stats": {}, "content": {"engagement_rate": 4.2}, "stories": {}},
    }
    comparison = load_workbook(BytesIO(build_excel_from_context(comparison_context, [])[0]))
    cmp_cells = [
        cell
        for row in comparison["Сравнение"].iter_rows()
        for cell in row
        if "%" in cell.number_format
    ]
    assert len(cmp_cells) == 2
    assert abs(cmp_cells[0].value - 0.089) < 1e-9
    assert abs(cmp_cells[1].value - 0.042) < 1e-9
    for cell in cmp_cells:
        assert cell.number_format == "0.0%"


def test_send_long_text_never_sends_empty_chunks():
    class FakeMessage:
        def __init__(self):
            self.sent = []

        async def answer(self, text, **kwargs):
            self.sent.append(text)

    message = FakeMessage()
    asyncio.run(send_long_text(message, "x" * 4100))

    assert len(message.sent) == 2
    assert all(chunk.strip() for chunk in message.sent)
    assert all(len(chunk) <= 4096 for chunk in message.sent)


def test_comparison_rejects_overlapping_periods_before_api_call():
    account = SimpleNamespace(id=1, username="demo")

    async def run():
        await generate_comparison_periods_report(
            account,
            date(2026, 9, 1), date(2026, 9, 10),
            date(2026, 9, 10), date(2026, 9, 20),
        )

    try:
        asyncio.run(run())
    except ValueError as error:
        assert "пересекаться" in str(error)
    else:
        raise AssertionError("overlapping periods were accepted")


def test_full_report_pipeline_builds_context_from_mocked_data():
    account = SimpleNamespace(
        id=1, username="demo", name="Demo", current_followers=1234,
        current_followers_at=datetime(2026, 9, 1, 12, 0),
    )
    stats = [SimpleNamespace(
        date=date(2026, 9, 1), followers=100, follower_count=3,
        reach=500, views=900, accounts_engaged=40, is_partial=False,
        metrics_present='["follower_count", "reach", "views", "accounts_engaged"]',
        collected_at=datetime(2026, 9, 1, 12, 0),
    )]
    post = SimpleNamespace(
        media_type="REELS", caption="A useful post", permalink="https://example.test/p",
        timestamp=datetime(2026, 9, 1, 12, 0),
        likes=20, comments=3, saved=4, shares=2, reach=500,
        total_interactions=29, views=800,
    )
    story = SimpleNamespace(
        timestamp=datetime(2026, 9, 1, 12, 0),
        media_type="IMAGE", permalink="", views=100, reach=80, replies=2,
        shares=1, total_interactions=3, profile_activity=1, follows=1,
        tap_forward=10, tap_back=2, tap_exit=3, swipe_forward=1, is_active=False,
    )

    async def run():
        with patch("reports.generator._fetch_and_save_data", new=AsyncMock(return_value={"api_delay_dates": [], "partial": False})), \
             patch("reports.generator._refresh_stories_if_recent", new=AsyncMock()), \
             patch("reports.generator.ensure_period_snapshot", new=AsyncMock(return_value={
                 "reach": 16663, "accounts_engaged": 1053, "views": 160856,
                 "likes": 1674, "comments": 46, "saves": 120, "shares": 580,
                 "total_interactions": 3024, "profile_views": 6627,
             })), \
             patch("reports.generator.get_analyzer", return_value=None), \
             patch("reports.generator.crud.get_daily_stats", new=AsyncMock(return_value=stats)), \
             patch("reports.generator.crud.get_latest_stats", new=AsyncMock(return_value=stats[-1])), \
             patch("reports.generator.crud.get_posts", new=AsyncMock(return_value=[post])), \
             patch("reports.generator.crud.get_stories", new=AsyncMock(return_value=[story])), \
             patch("reports.generator.create_followers_chart", return_value=b"followers"), \
             patch("reports.generator.create_metrics_chart", return_value=b"reach"), \
             patch("reports.generator.create_stories_chart", return_value=b"stories"):
            return await generate_report(account, custom_date_from=date(2026, 9, 1), custom_date_to=date(2026, 9, 1))

    result = asyncio.run(run())
    context = result["context"]
    assert result["text"]
    from reports.naming import L_FOLLOWERS_NOW, kv_row
    assert kv_row(L_FOLLOWERS_NOW, "1 234") in result["text"]
    assert context["content"]["total_posts"] == 1
    assert context["top_posts"][0]["media_type"] == "REELS"
    assert context["format_performance"]["REELS"]["posts"] == 1
    assert context["data_quality"]["complete"] is True
    assert set(result["charts"]) == {"followers", "reach", "stories"}
    # API period totals (unique metrics) must be preferred over sums of days
    assert context["stats"]["reach_total"] == 16663
    assert context["stats"]["reach_total_days_sum"] == 500
    assert context["stats"]["total_interactions_total"] == 3024


def test_comparison_report_handles_unknown_follower_growth():
    account = SimpleNamespace(id=1, username="demo", name="Demo")

    async def run():
        with patch("reports.generator._fetch_and_save_data", new=AsyncMock(return_value={"api_delay_dates": [], "partial": False})), \
             patch("reports.generator._refresh_stories_if_recent", new=AsyncMock()), \
             patch("reports.generator.ensure_period_snapshot", new=AsyncMock(return_value={})), \
             patch("reports.generator.get_analyzer", return_value=None), \
             patch("reports.generator.crud.get_daily_stats", new=AsyncMock(return_value=[])), \
             patch("reports.generator.crud.get_posts", new=AsyncMock(return_value=[])), \
             patch("reports.generator.crud.get_stories", new=AsyncMock(return_value=[])):
            return await generate_comparison_periods_report(
                account,
                date(2026, 9, 1), date(2026, 9, 7),
                date(2026, 9, 8), date(2026, 9, 14),
            )

    result = asyncio.run(run())
    assert result["context"]["period1"]["stats"]["followers_growth"] is None
    from reports.naming import L_FOLLOWERS_GROWTH, cmp_row
    assert cmp_row(L_FOLLOWERS_GROWTH, "н/д", "н/д", "н/д") in result["text"]


def test_report_survives_skipped_sync_result_shape():
    """Regression: when a sync is already running for the account,
    sync_account_data returns a shortened skipped result — the report must
    not crash on missing api_delay_dates and must warn instead."""
    account = SimpleNamespace(id=1, username="demo", name="Demo")
    skipped = {"skipped": True, "reason": "already_running", "partial": True}

    async def run():
        with patch("reports.generator._fetch_and_save_data", new=AsyncMock(return_value=skipped)), \
             patch("reports.generator._refresh_stories_if_recent", new=AsyncMock()), \
             patch("reports.generator.ensure_period_snapshot", new=AsyncMock(return_value={})), \
             patch("reports.generator.get_analyzer", return_value=None), \
             patch("reports.generator.crud.get_daily_stats", new=AsyncMock(return_value=[])), \
             patch("reports.generator.crud.get_latest_stats", new=AsyncMock(return_value=None)), \
             patch("reports.generator.crud.get_posts", new=AsyncMock(return_value=[])), \
             patch("reports.generator.crud.get_stories", new=AsyncMock(return_value=[])):
            return await generate_report(
                account, custom_date_from=date(2026, 9, 1), custom_date_to=date(2026, 9, 1)
            )

    result = asyncio.run(run())
    assert result["text"]
    assert "⚠️ Часть данных не удалось получить" in result["text"]


def test_comparison_survives_skipped_sync_result_shape():
    """Regression: same shortened skipped result on the comparison path."""
    account = SimpleNamespace(id=1, username="demo", name="Demo")
    skipped = {"skipped": True, "reason": "lease_held", "partial": True}
    complete = {"api_delay_dates": [], "partial": False}

    async def run():
        with patch("reports.generator._fetch_and_save_data", new=AsyncMock(side_effect=[skipped, complete])), \
             patch("reports.generator._refresh_stories_if_recent", new=AsyncMock()), \
             patch("reports.generator.ensure_period_snapshot", new=AsyncMock(return_value={})), \
             patch("reports.generator.get_analyzer", return_value=None), \
             patch("reports.generator.crud.get_daily_stats", new=AsyncMock(return_value=[])), \
             patch("reports.generator.crud.get_posts", new=AsyncMock(return_value=[])), \
             patch("reports.generator.crud.get_stories", new=AsyncMock(return_value=[])):
            return await generate_comparison_periods_report(
                account,
                date(2026, 9, 1), date(2026, 9, 7),
                date(2026, 9, 8), date(2026, 9, 14),
            )

    result = asyncio.run(run())
    assert result["text"]
    assert "⚠️ Часть данных не удалось получить" in result["text"]


def test_merge_period_totals_prefers_api_unique_metrics():
    from reports.generator import _merge_period_totals

    summary = {"reach_total": 36156, "views_total": 160856, "accounts_engaged_total": 1741}
    totals = {"reach": 16663, "accounts_engaged": 1053, "views": 160856, "total_interactions": 3024}
    merged = _merge_period_totals(summary, totals)
    assert merged["reach_total"] == 16663
    assert merged["reach_total_days_sum"] == 36156
    assert merged["accounts_engaged_total"] == 1053
    assert merged["views_total"] == 160856
    assert merged["total_interactions_total"] == 3024
    assert merged["totals_from_api"] is True

    fallback = _merge_period_totals({"reach_total": 500}, {})
    assert fallback["reach_total"] == 500
    assert fallback["totals_from_api"] is False

