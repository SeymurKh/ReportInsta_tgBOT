import io
import asyncio

from openpyxl import load_workbook

from bot.helpers import build_excel_from_context, parse_period_input, send_long_text
from reports.generator import _day_end, resolve_period


def test_parse_period_rejects_future_date():
    from datetime import date

    try:
        parse_period_input("22.09", date(2026, 9, 21))
    except ValueError:
        return
    raise AssertionError("future dates must be rejected")


def test_resolve_period_supports_default_week():
    from datetime import date

    start, end = resolve_period("week")
    assert isinstance(start, date)
    assert isinstance(end, date)
    assert (end - start).days == 6


def test_day_end_is_available_for_digest_and_sync():
    from datetime import date, datetime

    assert _day_end(date(2026, 9, 23)) == datetime(2026, 9, 23, 23, 59, 59)


def test_interaction_reconciliation_notes_are_removed_with_their_functions():
    """Service reconciliation notes were removed from the report — the module
    must not expose them anymore."""
    import reports.generator as generator_module

    assert not hasattr(generator_module, "_interaction_reconciliation_note")
    assert not hasattr(generator_module, "_account_interaction_note")


def test_empty_message_is_replaced_with_fallback():
    class FakeMessage:
        def __init__(self):
            self.messages = []

        async def answer(self, text, **kwargs):
            self.messages.append(text)

    message = FakeMessage()
    asyncio.run(send_long_text(message, "   "))
    assert len(message.messages) == 1
    assert message.messages[0]


def test_send_long_text_monospace_chunks_fit_fenced_message():
    """Regression: a fenced (```) chunk plus its wrapper used to exceed the
    Telegram message limit and the whole chunk was rejected."""
    from bot.helpers import FENCE, MAX_MESSAGE_LEN

    class FakeMessage:
        def __init__(self):
            self.messages = []

        async def answer(self, text, **kwargs):
            self.messages.append(text)

    table_line = "метрика " + "ю" * 60
    mono_body = "\n".join([table_line] * 70)  # > 4096 chars of table
    message = FakeMessage()
    asyncio.run(send_long_text(message, f"{FENCE}\n{mono_body}\n{FENCE}"))

    assert len(message.messages) > 1
    for sent in message.messages:
        assert len(sent) <= MAX_MESSAGE_LEN
        assert sent.startswith(FENCE) and sent.endswith(FENCE)


def test_metric_str_keeps_real_zero_and_marks_missing():
    from reports.generator import metric_str

    assert metric_str({"likes_total": 0}, "likes_total") == "0"
    assert metric_str({"saves_total": 0}, "saves_total") == "0"
    assert metric_str({}, "likes_total") == "н/д"
    assert metric_str({"likes_total": None}, "likes_total") == "н/д"
    assert metric_str({"reach_total": 1234}, "reach_total") == "1 234"


# ── response wording (report must say exactly what it shows) ──

def _mocked_report(partial=False, api_delay=None, totals=None, analyzer=None):
    """Generate one report from mocked data — shared wording fixture."""
    from datetime import date, datetime
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, patch

    from reports.generator import generate_report

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
    api_totals = totals if totals is not None else {
        "reach": 16663, "accounts_engaged": 1053, "views": 160856,
        "likes": 1674, "comments": 46, "saves": 120, "shares": 580,
        "total_interactions": 3024, "profile_views": 6627,
    }
    fetch_info = {"api_delay_dates": api_delay or [], "partial": partial}

    async def run():
        with patch("reports.generator._fetch_and_save_data", new=AsyncMock(return_value=fetch_info)), \
             patch("reports.generator._refresh_stories_if_recent", new=AsyncMock()), \
             patch("reports.generator.ensure_period_snapshot", new=AsyncMock(return_value=api_totals)), \
             patch("reports.generator.get_analyzer", return_value=analyzer), \
             patch("reports.generator.crud.get_daily_stats", new=AsyncMock(return_value=stats)), \
             patch("reports.generator.crud.get_latest_stats", new=AsyncMock(return_value=stats[-1])), \
             patch("reports.generator.crud.get_posts", new=AsyncMock(return_value=[post])), \
             patch("reports.generator.crud.get_stories", new=AsyncMock(return_value=[story])), \
             patch("reports.generator.create_followers_chart", return_value=b"f"), \
             patch("reports.generator.create_metrics_chart", return_value=b"m"), \
             patch("reports.generator.create_stories_chart", return_value=b"s"):
            return await generate_report(
                account, custom_date_from=date(2026, 9, 1), custom_date_to=date(2026, 9, 1)
            )

    return asyncio.run(run())


def test_report_wording_reflects_metric_semantics():
    from reports.naming import (
        kv_row, L_ACCOUNT_ER, L_GROWTH_SPEED, L_POSTS, L_POST_COMMENTS,
        L_POST_SAVES, L_POST_SHARES, L_STORIES_EXITS, L_STORIES_EXIT,
        L_STORIES_SWIPE,
    )

    text = _mocked_report()["text"]

    assert "н/д%" not in text  # missing values never grow a percent suffix
    assert kv_row(L_ACCOUNT_ER, "18,1%") in text  # 3024 / 16663, decimal comma
    # complete per-post averages; the duplicated count row is gone
    assert kv_row(L_POST_COMMENTS, "3") in text
    assert kv_row(L_POST_SAVES, "4") in text
    assert kv_row(L_POST_SHARES, "2") in text
    assert kv_row(L_POSTS, "1") not in text
    # exit rate is backed by visible components
    assert kv_row(L_STORIES_EXITS, "3") in text
    assert kv_row(L_STORIES_SWIPE, "1") in text
    assert kv_row(L_STORIES_EXIT, "4,0%") in text  # (3 + 1) / 100 views
    # the best-post criterion is short; the formula lives in the code, not text
    assert "самый высокий отклик на 100 показов за период" in text
    assert "балл = " not in text
    # one-day period: no fake «7 дн. к 7 дн.» window claim
    assert kv_row(L_GROWTH_SPEED, "н/д") in text
    assert "Скорость роста (7 дн." not in text
    # the composition line adds up to the «all interactions» total: 3024 - 2420
    assert "прочие действия 604" in text
    # header travels with the tables in one message
    assert text.startswith("```\n📱 @demo — Demo")
    # no service blocks at all
    for service_phrase in (
        "Сверка взаимодействий", "КАК ЧИТАТЬ", "документально не подтверждена",
        "Полнота метрик", "внедрения учёта полноты",
    ):
        assert service_phrase not in text


def test_report_warnings_appear_before_ai_analysis():
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    analyzer = SimpleNamespace(analyze_account=AsyncMock(return_value="AI_ANALYSIS_TEXT"))
    text = _mocked_report(partial=True, api_delay=["01.09"], analyzer=analyzer)["text"]

    warn_pos = text.find("⚠️")
    ai_pos = text.find("AI_ANALYSIS_TEXT")
    assert warn_pos != -1 and ai_pos != -1
    assert warn_pos < ai_pos  # data caveats must be read before the verdict
    assert "01.09" in text


def test_missing_api_totals_are_disclosed_not_masked():
    from reports.naming import kv_row, L_ACCOUNT_ER

    text = _mocked_report(totals={})["text"]

    assert "⚠️ Итоги API недоступны — охват и вовлечённые показаны суммой по дням" in text
    assert kv_row(L_ACCOUNT_ER, "н/д") in text
    assert "н/д%" not in text
    # without API totals the composition line has nothing to decompose
    assert "из них по метрикам аккаунта" not in text


def test_comparison_excel_contains_comparison_sheet():
    context = {
        "account_username": "test",
        "account": "@test — Test",
        "period": "1–7 сентября vs 8–14 сентября",
        "type": "comparison",
        "period1": {
            "name": "1–7 сентября",
            "stats": {"followers_growth": 10, "reach_total": 100},
            "content": {"total_posts": 2, "engagement_rate": 3.0},
            "stories": {"total_stories": 1, "total_views": 50},
        },
        "period2": {
            "name": "8–14 сентября",
            "stats": {"followers_growth": 20, "reach_total": 200},
            "content": {"total_posts": 4, "engagement_rate": 4.0},
            "stories": {"total_stories": 2, "total_views": 100},
        },
    }

    data, filename = build_excel_from_context(context, None)
    workbook = load_workbook(filename=io.BytesIO(data), read_only=True)

    assert filename.endswith(".xlsx")
    assert "Сравнение" in workbook.sheetnames
    comparison_sheet = workbook["Сравнение"]
    values = [cell.value for row in comparison_sheet.iter_rows() for cell in row]
    assert 10 in values
    assert 20 in values
