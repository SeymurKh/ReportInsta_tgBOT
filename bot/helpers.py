"""Shared helpers for bot handlers: sending long reports, charts, Excel."""
import logging
import re
from datetime import date

from aiogram.types import Message, BufferedInputFile, InputMediaPhoto

from bot.keyboards import question_after_report_kb

logger = logging.getLogger(__name__)

MAX_MESSAGE_LEN = 4096


async def send_long_text(message: Message, text: str) -> None:
    if not text or not text.strip():
        await message.answer("⚠️ Не удалось сформировать текстовый отчёт.", parse_mode=None)
        return
    for i in range(0, len(text), MAX_MESSAGE_LEN):
        chunk = text[i:i + MAX_MESSAGE_LEN]
        if chunk.strip():
            await message.answer(chunk, parse_mode=None)


async def send_charts(message: Message, charts: dict) -> None:
    media_group = [
        InputMediaPhoto(media=BufferedInputFile(chart_bytes, filename=f"{name}.png"))
        for name, chart_bytes in charts.items()
    ]
    if media_group:
        await message.answer_media_group(media_group)


async def send_report_result(message: Message, result: dict, ask_question: bool = True) -> None:
    """Send report text + charts + follow-up keyboard."""
    await send_long_text(message, result["text"])
    charts = result.get("charts") or {}
    if charts:
        await send_charts(message, charts)
    if ask_question:
        await message.answer(
            "💡 Хотите задать вопрос по этим данным?",
            reply_markup=question_after_report_kb(),
        )


def build_excel_from_context(context: dict, dialogue_history: list | None) -> tuple[bytes, str]:
    """Build Excel report bytes + filename from a saved report context."""
    from reports.excel_generator import generate_excel_report

    comparison = (context.get("period1"), context.get("period2"))
    is_comparison = all(comparison)
    comparison_period2 = context.get("period2", {}) if is_comparison else {}

    excel_bytes = generate_excel_report(
        account_username=context.get("account_username") or "account",
        account_name=context.get("account", ""),
        period_str=context.get("period", ""),
        stats_summary=context.get("stats", {}) or comparison_period2.get("stats", {}),
        content_summary=context.get("content", {}) or comparison_period2.get("content", {}),
        daily_stats=context.get("daily_stats", []),
        publications=context.get("publications", []),
        ai_analysis=context.get("ai_analysis", "Нет данных"),
        dialogue_history=dialogue_history,
        stories_summary=context.get("stories"),
        stories_list=context.get("stories_list"),
        comparison_periods=list(comparison) if is_comparison else None,
    )
    filename = f"report_{context.get('period', 'period').replace(' ', '_').replace('–', '-')}.xlsx"
    return excel_bytes, filename


async def send_excel(message: Message, context: dict, dialogue_history: list | None = None) -> None:
    loading_msg = await message.answer("⏳ Формирую Excel...")
    try:
        excel_bytes, filename = build_excel_from_context(context, dialogue_history)
        await message.answer_document(
            BufferedInputFile(excel_bytes, filename=filename),
            caption="📊 Отчёт в формате Excel",
        )
    except Exception as e:
        logger.error(f"Excel error: {e}", exc_info=True)
        await message.answer(f"❌ Ошибка: {e}", parse_mode=None)
    finally:
        try:
            await loading_msg.delete()
        except Exception:
            pass


# ── Date parsing ──

DATE_RANGE_RE = re.compile(r"^\d{2}\.\d{2}(-\d{2}\.\d{2})?$")
COMPARISON_RE = re.compile(r"^\d{2}\.\d{2}-\d{2}\.\d{2}\s+vs\s+\d{2}\.\d{2}-\d{2}\.\d{2}$")


def _parse_day_month(text: str, today: date) -> date:
    day, month = (int(p) for p in text.strip().split("."))
    if not (1 <= day <= 31 and 1 <= month <= 12):
        raise ValueError("Invalid date")
    parsed = date(today.year, month, day)  # raises ValueError on e.g. 31.02
    if parsed > today:
        raise ValueError("Future dates are not supported")
    return parsed


def parse_period_input(text: str, today: date) -> tuple[date, date]:
    """Parse "ДД.ММ" or "ДД.ММ-ДД.ММ" -> (date_from, date_to).

    Raises ValueError on bad format or date_from > date_to.
    """
    text = text.strip()
    if not DATE_RANGE_RE.match(text):
        raise ValueError("Bad format")
    if "-" in text:
        left, right = text.split("-")
        date_from = _parse_day_month(left, today)
        date_to = _parse_day_month(right, today)
    else:
        date_from = date_to = _parse_day_month(text, today)
    if date_from > date_to:
        raise ValueError("date_from > date_to")
    return date_from, date_to


def parse_comparison_input(text: str, today: date) -> tuple[date, date, date, date]:
    """Parse "ДД.ММ-ДД.ММ vs ДД.ММ-ДД.ММ". Raises ValueError on any problem."""
    text = text.strip()
    if not COMPARISON_RE.match(text):
        raise ValueError("Bad format")
    left, right = (p.strip() for p in text.split("vs"))
    p1_from, p1_to = parse_period_input(left, today)
    p2_from, p2_to = parse_period_input(right, today)
    return p1_from, p1_to, p2_from, p2_to
