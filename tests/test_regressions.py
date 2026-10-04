import io
import asyncio

from openpyxl import load_workbook

from bot.helpers import build_excel_from_context, parse_period_input, send_long_text
from reports.generator import _day_end, _interaction_reconciliation_note, resolve_period


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


def test_interaction_reconciliation_note_is_only_emitted_for_a_gap():
    assert _interaction_reconciliation_note({"interaction_gap": 0}) == ""

    note = _interaction_reconciliation_note({
        "component_interactions": 335,
        "total_interactions": 375,
        "interaction_gap": 40,
    })
    assert "335" in note
    assert "375" in note
    assert "+40" in note
    assert "ER рассчитан по total_interactions" in note


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
