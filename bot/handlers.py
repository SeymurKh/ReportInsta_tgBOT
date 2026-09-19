import logging
import re
from datetime import date, timedelta

from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InputMediaPhoto, BufferedInputFile
from aiogram.filters import Command

from config import settings
from database import crud
from reports.generator import generate_report, generate_comparison_periods_report
from analytics.ai_analyzer import AIAnalyzer
from bot.keyboards import (
    main_menu_kb, accounts_kb, period_kb, comparison_period_kb,
    ai_period_kb, question_after_report_kb, dialogue_kb,
)
from utils.formatters import format_number, format_period, format_context_for_ai

logger = logging.getLogger(__name__)
router = Router()
ai = AIAnalyzer()

# ── Per-user state management (replaces unsafe bot attributes) ──

user_states: dict[int, dict] = {}


def _get_state(user_id: int) -> dict:
    if user_id not in user_states:
        user_states[user_id] = {
            "selected_account": None,
            "flow": "report",
            "dialogue_active": False,
            "dialogue_history": [],
            "report_context": None,
            "awaiting_custom_dates": False,
            "awaiting_comparison_dates": False,
            "awaiting_ai_period": False,
        }
    return user_states[user_id]


# ── Admin check ──

async def _check_admin(user_id: int) -> bool:
    return user_id == settings.ADMIN_TELEGRAM_ID


# ── Start ──

@router.message(Command("start"))
async def cmd_start(message: Message):
    if not await _check_admin(message.from_user.id):
        await message.answer("⛔ Доступ запрещён.")
        return
    await message.answer(
        "👋 Привет! Я бот аналитики Instagram.\n\n"
        "Выбери действие из меню ниже:",
        reply_markup=main_menu_kb(),
    )


# ── 📊 Отчёты ──

@router.message(F.text == "📊 Отчёты")
async def reports_handler(message: Message):
    if not await _check_admin(message.from_user.id):
        return
    state = _get_state(message.from_user.id)
    state["dialogue_active"] = False
    state["dialogue_history"] = []

    accounts = await crud.get_all_accounts()
    if not accounts:
        await message.answer("❌ Нет добавленных аккаунтов.")
        return
    state["flow"] = "report"
    await message.answer("📱 Выберите аккаунт:", reply_markup=accounts_kb(accounts))


# ── Account selection callback ──

@router.callback_query(F.data.startswith("account_"))
async def account_callback(callback: CallbackQuery):
    if not await _check_admin(callback.from_user.id):
        return
    state = _get_state(callback.from_user.id)
    state["selected_account"] = int(callback.data.split("_")[1])

    if state["flow"] == "comparison":
        await callback.message.edit_text("📅 Выберите первый период:", reply_markup=comparison_period_kb())
    else:
        await callback.message.edit_text("📅 Выберите период:", reply_markup=period_kb())
    await callback.answer()


# ── Period selection callback ──

@router.callback_query(F.data.startswith("period_"))
async def period_callback(callback: CallbackQuery):
    if not await _check_admin(callback.from_user.id):
        return
    period = callback.data.split("_")[1]
    state = _get_state(callback.from_user.id)
    account_id = state["selected_account"]

    if not account_id:
        await callback.answer("⚠️ Аккаунт не выбран.", show_alert=True)
        return

    account = await crud.get_account_by_id(account_id)
    if not account:
        await callback.answer("⚠️ Аккаунт не найден.", show_alert=True)
        return

    # Custom period - ask for dates
    if period == "custom":
        state["awaiting_custom_dates"] = True
        await callback.message.edit_text(
            "📅 Введите период в формате: ДД.ММ-ДД.ММ\n"
            "Например: 01.09-15.09",
            reply_markup=None,
        )
        await callback.answer()
        return

    # Answer callback immediately to avoid timeout
    await callback.answer()

    # Delete the period selection message
    try:
        await callback.message.delete()
    except Exception:
        pass

    # Send loading message
    loading_msg = await callback.message.answer("⏳ Собираю данные и генерирую отчёт...")

    try:
        result = await generate_report(account, period)
        text = result["text"]
        charts = result.get("charts", {})
        context = result.get("context", {})

        # Save context for AI dialogue
        state["report_context"] = context

        # Delete loading message
        try:
            await loading_msg.delete()
        except Exception:
            pass

        if len(text) > 4096:
            for i in range(0, len(text), 4096):
                await callback.message.answer(text[i:i + 4096], parse_mode=None)
        else:
            await callback.message.answer(text, parse_mode=None)

        if charts:
            media_group = []
            for name, chart_bytes in charts.items():
                media_group.append(InputMediaPhoto(
                    media=BufferedInputFile(chart_bytes, filename=f"{name}.png")
                ))
            if media_group:
                await callback.message.answer_media_group(media_group)

        # Show "Ask question" button
        await callback.message.answer(
            "💡 Хотите задать вопрос по этим данным?",
            reply_markup=question_after_report_kb()
        )

    except Exception as e:
        logger.error(f"Report error: {e}", exc_info=True)
        try:
            await loading_msg.delete()
        except Exception:
            pass
        await callback.message.answer(f"❌ Ошибка при генерации отчёта: {e}", parse_mode=None)


# ── Custom dates text input ──

@router.message(F.text.regexp(r"^\d{2}\.\d{2}(-\d{2}\.\d{2})?$"))
async def custom_dates_handler(message: Message):
    if not await _check_admin(message.from_user.id):
        return

    state = _get_state(message.from_user.id)
    account_id = state["selected_account"]
    awaiting = state["awaiting_custom_dates"]

    if not account_id or not awaiting:
        return

    state["awaiting_custom_dates"] = False
    awaiting_ai = state["awaiting_ai_period"]
    state["awaiting_ai_period"] = False

    account = await crud.get_account_by_id(account_id)
    if not account:
        await message.answer("⚠️ Аккаунт не найден.")
        return

    try:
        text_input = message.text.strip()
        today = date.today()

        # Handle single date (DD.MM)
        if "." in text_input and "-" not in text_input:
            d_parts = text_input.split(".")
            day, month = int(d_parts[0]), int(d_parts[1])
            if not (1 <= day <= 31 and 1 <= month <= 12):
                raise ValueError("Invalid date")
            date_from = date(today.year, month, day)
            date_to = date_from
        else:
            # Handle range (DD.MM-DD.MM)
            parts = text_input.split("-")
            d1_parts = parts[0].strip().split(".")
            d2_parts = parts[1].strip().split(".")
            day1, month1 = int(d1_parts[0]), int(d1_parts[1])
            day2, month2 = int(d2_parts[0]), int(d2_parts[1])
            if not all(1 <= d <= 31 and 1 <= m <= 12 for d, m in [(day1, month1), (day2, month2)]):
                raise ValueError("Invalid date")
            date_from = date(today.year, month1, day1)
            date_to = date(today.year, month2, day2)
    except (ValueError, IndexError):
        await message.answer("❌ Неверный формат. Используйте: ДД.ММ или ДД.ММ-ДД.ММ\nНапример: 15.09 или 01.09-15.09")
        return

    loading_msg = await message.answer("⏳ Собираю данные и генерирую отчёт...")

    try:
        result = await generate_report(account, custom_date_from=date_from, custom_date_to=date_to)
        text = result["text"]
        charts = result.get("charts", {})
        context = result.get("context", {})

        # Save context for AI dialogue
        state["report_context"] = context

        try:
            await loading_msg.delete()
        except Exception:
            pass

        if len(text) > 4096:
            for i in range(0, len(text), 4096):
                await message.answer(text[i:i + 4096], parse_mode=None)
        else:
            await message.answer(text, parse_mode=None)

        if charts:
            media_group = []
            for name, chart_bytes in charts.items():
                media_group.append(InputMediaPhoto(
                    media=BufferedInputFile(chart_bytes, filename=f"{name}.png")
                ))
            if media_group:
                await message.answer_media_group(media_group)

        if awaiting_ai:
            # Came from AI flow — enter dialogue mode
            state["dialogue_active"] = True
            state["dialogue_history"] = []
            await message.answer(
                "💬 Теперь можете задавать вопрос по этим данным.\n"
                "Можно задавать несколько вопросов подряд.\n"
                "Для выхода нажмите «◀️ В меню».",
                reply_markup=dialogue_kb(),
            )
        else:
            # Came from report flow — show "Ask question" button
            await message.answer(
                "💡 Хотите задать вопрос по этим данным?",
                reply_markup=question_after_report_kb()
            )

    except Exception as e:
        logger.error(f"Report error: {e}", exc_info=True)
        try:
            await loading_msg.delete()
        except Exception:
            pass
        await message.answer(f"❌ Ошибка: {e}", parse_mode=None)


# ── 📱 Аккаунты ──

@router.message(F.text == "📱 Аккаунты")
async def accounts_handler(message: Message):
    if not await _check_admin(message.from_user.id):
        return
    state = _get_state(message.from_user.id)
    state["dialogue_active"] = False
    state["dialogue_history"] = []

    accounts = await crud.get_all_accounts()
    if not accounts:
        await message.answer("❌ Нет добавленных аккаунтов.")
        return
    lines = ["📱 **Аккаунты:**\n"]
    for acc in accounts:
        latest = await crud.get_latest_stats(acc.id)
        followers = latest.followers if latest else "—"
        lines.append(f"• @{acc.username} — {acc.name}\n  👥 {followers} подписчиков")
    await message.answer("\n".join(lines), reply_markup=main_menu_kb())


# ── Back callback ──

@router.callback_query(F.data == "back")
async def back_callback(callback: CallbackQuery):
    if not await _check_admin(callback.from_user.id):
        return
    try:
        await callback.message.delete()
    except Exception:
        pass
    await callback.message.answer("Главное меню:", reply_markup=main_menu_kb())
    await callback.answer()


# ── 📊 Сравнение периодов ──

@router.message(F.text == "📊 Сравнение")
async def comparison_handler(message: Message):
    if not await _check_admin(message.from_user.id):
        return
    state = _get_state(message.from_user.id)
    state["dialogue_active"] = False
    state["dialogue_history"] = []

    accounts = await crud.get_all_accounts()
    if not accounts:
        await message.answer("❌ Нет добавленных аккаунтов.")
        return
    state["flow"] = "comparison"
    await message.answer("📱 Выберите аккаунт для сравнения:", reply_markup=accounts_kb(accounts))


# ── Comparison period callbacks ──

@router.callback_query(F.data.startswith("comp_"))
async def comparison_period_callback(callback: CallbackQuery):
    if not await _check_admin(callback.from_user.id):
        return
    state = _get_state(callback.from_user.id)
    account_id = state["selected_account"]
    if not account_id:
        await callback.answer("⚠️ Аккаунт не выбран.", show_alert=True)
        return

    account = await crud.get_account_by_id(account_id)
    if not account:
        await callback.answer("⚠️ Аккаунт не найден.", show_alert=True)
        return

    comp_type = callback.data.split("_")[1]
    today = date.today()

    if comp_type == "week":
        p2_to = today - timedelta(days=1)
        p2_from = today - timedelta(days=7)
        p1_to = today - timedelta(days=8)
        p1_from = today - timedelta(days=14)
    elif comp_type == "month":
        p2_to = today - timedelta(days=1)
        p2_from = today - timedelta(days=30)
        p1_to = today - timedelta(days=31)
        p1_from = today - timedelta(days=60)
    elif comp_type == "custom":
        state["awaiting_comparison_dates"] = True
        await callback.message.edit_text(
            "📅 Введите два периода в формате:\n"
            "ДД.ММ-ДД.ММ vs ДД.ММ-ДД.ММ\n"
            "Например: 01.09-07.09 vs 08.09-14.09",
            reply_markup=None,
        )
        await callback.answer()
        return
    else:
        await callback.answer("⚠️ Неизвестный тип сравнения.", show_alert=True)
        return

    await callback.answer()
    try:
        await callback.message.delete()
    except Exception:
        pass

    loading_msg = await callback.message.answer("⏳ Сравниваю периоды...")

    try:
        result = await generate_comparison_periods_report(account, p1_from, p1_to, p2_from, p2_to)
        text = result["text"]
        context = result.get("context", {})

        # Save context for AI dialogue
        state["report_context"] = context

        try:
            await loading_msg.delete()
        except Exception:
            pass

        if len(text) > 4096:
            for i in range(0, len(text), 4096):
                await callback.message.answer(text[i:i + 4096], parse_mode=None)
        else:
            await callback.message.answer(text, parse_mode=None)

        # Show "Ask question" button
        await callback.message.answer(
            "💡 Хотите задать вопрос по этим данным?",
            reply_markup=question_after_report_kb()
        )

    except Exception as e:
        logger.error(f"Comparison error: {e}", exc_info=True)
        try:
            await loading_msg.delete()
        except Exception:
            pass
        await callback.message.answer(f"❌ Ошибка: {e}", parse_mode=None)


# ── Custom comparison dates text input ──

@router.message(F.text.regexp(r"^\d{2}\.\d{2}-\d{2}\.\d{2}\s+vs\s+\d{2}\.\d{2}-\d{2}\.\d{2}$"))
async def custom_comparison_dates_handler(message: Message):
    if not await _check_admin(message.from_user.id):
        return

    state = _get_state(message.from_user.id)
    account_id = state["selected_account"]
    awaiting = state["awaiting_comparison_dates"]

    if not account_id or not awaiting:
        return

    state["awaiting_comparison_dates"] = False
    account = await crud.get_account_by_id(account_id)
    if not account:
        await message.answer("⚠️ Аккаунт не найден.")
        return

    try:
        parts = message.text.split("vs")
        p1_parts = parts[0].strip().split("-")
        p2_parts = parts[1].strip().split("-")
        today = date.today()

        d1_from = p1_parts[0].split(".")
        d1_to = p1_parts[1].split(".")
        d2_from = p2_parts[0].split(".")
        d2_to = p2_parts[1].split(".")

        day1, month1 = int(d1_from[0]), int(d1_from[1])
        day2, month2 = int(d1_to[0]), int(d1_to[1])
        day3, month3 = int(d2_from[0]), int(d2_from[1])
        day4, month4 = int(d2_to[0]), int(d2_to[1])

        if not all(1 <= d <= 31 and 1 <= m <= 12 for d, m in [
            (day1, month1), (day2, month2), (day3, month3), (day4, month4)
        ]):
            raise ValueError("Invalid date")

        p1_from = date(today.year, month1, day1)
        p1_to = date(today.year, month2, day2)
        p2_from = date(today.year, month3, day3)
        p2_to = date(today.year, month4, day4)
    except (ValueError, IndexError):
        await message.answer(
            "❌ Неверный формат. Используйте:\n"
            "ДД.ММ-ДД.ММ vs ДД.ММ-ДД.ММ\n"
            "Например: 01.09-07.09 vs 08.09-14.09"
        )
        return

    loading_msg = await message.answer("⏳ Сравниваю периоды...")

    try:
        result = await generate_comparison_periods_report(account, p1_from, p1_to, p2_from, p2_to)
        text = result["text"]
        context = result.get("context", {})

        # Save context for AI dialogue
        state["report_context"] = context

        try:
            await loading_msg.delete()
        except Exception:
            pass

        if len(text) > 4096:
            for i in range(0, len(text), 4096):
                await message.answer(text[i:i + 4096], parse_mode=None)
        else:
            await message.answer(text, parse_mode=None)

        # Show "Ask question" button
        await message.answer(
            "💡 Хотите задать вопрос по этим данным?",
            reply_markup=question_after_report_kb()
        )

    except Exception as e:
        logger.error(f"Comparison error: {e}", exc_info=True)
        try:
            await loading_msg.delete()
        except Exception:
            pass
        await message.answer(f"❌ Ошибка: {e}", parse_mode=None)


# ── 💬 Вопрос по отчёту (кнопка после отчёта) ──

@router.callback_query(F.data == "question_about_report")
async def question_about_report(callback: CallbackQuery):
    if not await _check_admin(callback.from_user.id):
        return

    state = _get_state(callback.from_user.id)
    state["dialogue_active"] = True
    state["dialogue_history"] = []

    context = state["report_context"]
    period = context.get("period", "—") if context else "—"

    await callback.message.edit_text(
        f"💬 Задайте вопрос по данным за {period}.\n"
        "Можно задавать несколько вопросов подряд.\n"
        "Для выхода нажмите «◀️ В меню».",
        reply_markup=None,
    )
    await callback.answer()


# ── 💬 Вопрос нейронке (из меню) ──

@router.message(F.text == "💬 Вопрос нейронке")
async def ai_chat_start(message: Message):
    if not await _check_admin(message.from_user.id):
        return

    state = _get_state(message.from_user.id)
    state["dialogue_active"] = False
    state["dialogue_history"] = []

    context = state["report_context"]

    if context:
        # Context exists — enter dialogue immediately
        state["dialogue_active"] = True
        period = context.get("period", "—")
        await message.answer(
            f"💬 Задайте вопрос по данным за {period}.\n"
            "Можно задавать несколько вопросов подряд.\n"
            "Для выхода нажмите «◀️ В меню».",
            reply_markup=dialogue_kb(),
        )
    else:
        # No context — ask for period
        state["awaiting_ai_period"] = True
        await message.answer(
            "📅 Сначала соберу данные. За какой период?",
            reply_markup=ai_period_kb(),
        )


# ── AI period selection callback ──

@router.callback_query(F.data.startswith("ai_period_"))
async def ai_period_callback(callback: CallbackQuery):
    if not await _check_admin(callback.from_user.id):
        return

    period = callback.data.split("_")[2]  # week/month/custom
    state = _get_state(callback.from_user.id)
    account_id = state["selected_account"]

    # If no account selected, select first
    if not account_id:
        accounts = await crud.get_all_accounts()
        if not accounts:
            await callback.answer("❌ Нет аккаунтов.", show_alert=True)
            return
        account = accounts[0]
        state["selected_account"] = account.id
    else:
        account = await crud.get_account_by_id(account_id)

    if not account:
        await callback.answer("⚠️ Аккаунт не найден.", show_alert=True)
        return

    if period == "custom":
        state["awaiting_custom_dates"] = True
        state["awaiting_ai_period"] = True
        await callback.message.edit_text(
            "📅 Введите дату или период:\n"
            "ДД.ММ (конкретный день) или ДД.ММ-ДД.ММ (период)\n"
            "Например: 15.09 или 01.09-15.09",
            reply_markup=None,
        )
        await callback.answer()
        return

    await callback.answer()
    try:
        await callback.message.delete()
    except Exception:
        pass

    loading_msg = await callback.message.answer("⏳ Собираю данные...")

    try:
        result = await generate_report(account, period)
        context = result.get("context", {})

        # Save context
        state["report_context"] = context

        # Send report
        text = result["text"]
        charts = result.get("charts", {})

        try:
            await loading_msg.delete()
        except Exception:
            pass

        if len(text) > 4096:
            for i in range(0, len(text), 4096):
                await callback.message.answer(text[i:i + 4096], parse_mode=None)
        else:
            await callback.message.answer(text, parse_mode=None)

        if charts:
            media_group = []
            for name, chart_bytes in charts.items():
                media_group.append(InputMediaPhoto(
                    media=BufferedInputFile(chart_bytes, filename=f"{name}.png")
                ))
            if media_group:
                await callback.message.answer_media_group(media_group)

        # Enter dialogue mode
        state["dialogue_active"] = True
        state["dialogue_history"] = []

        await callback.message.answer(
            "💬 Теперь можете задавать вопрос по этим данным.\n"
            "Можно задавать несколько вопросов подряд.\n"
            "Для выхода нажмите «◀️ В меню».",
            reply_markup=dialogue_kb(),
        )

    except Exception as e:
        logger.error(f"AI period error: {e}", exc_info=True)
        try:
            await loading_msg.delete()
        except Exception:
            pass
        await callback.message.answer(f"❌ Ошибка: {e}", parse_mode=None)


# ── Multi-turn dialogue handler ──

@router.message(F.text & ~F.text.startswith("/") & ~F.text.in_({
    "📊 Отчёты", "📊 Сравнение", "📱 Аккаунты", "💬 Вопрос нейронке",
    "◀️ В меню", "📄 Скачать Excel"
}))
async def ai_chat_handler(message: Message):
    if not await _check_admin(message.from_user.id):
        return

    state = _get_state(message.from_user.id)
    if not state["dialogue_active"]:
        return

    context = state["report_context"]
    if not context:
        await message.answer("⚠️ Нет данных для вопросов. Сначала сгенерируйте отчёт.")
        return

    history = state["dialogue_history"]
    context_str = format_context_for_ai(context)

    loading_msg = await message.answer("🤔 Думаю...")

    try:
        answer = await ai.chat_with_context(context_str, history, message.text)

        # Save to history
        history.append({"role": "user", "content": message.text})
        history.append({"role": "assistant", "content": answer})

        # Limit history to last 20 messages to prevent token overflow
        if len(history) > 20:
            history = history[-20:]
        state["dialogue_history"] = history

        try:
            await loading_msg.delete()
        except Exception:
            pass

        await message.answer(answer, parse_mode=None, reply_markup=dialogue_kb())

    except Exception as e:
        logger.error(f"AI chat error: {e}", exc_info=True)
        try:
            await loading_msg.delete()
        except Exception:
            pass
        await message.answer(f"❌ Ошибка: {e}", parse_mode=None)


# ── Download Excel report ──

@router.message(F.text == "📄 Скачать Excel")
async def download_excel_handler(message: Message):
    if not await _check_admin(message.from_user.id):
        return

    state = _get_state(message.from_user.id)
    context = state["report_context"]
    if not context:
        await message.answer("⚠️ Нет данных для отчёта.")
        return

    dialogue_history = state["dialogue_history"]

    loading_msg = await message.answer("⏳ Формирую Excel...")

    try:
        from reports.excel_generator import generate_excel_report

        stats = context.get("stats", {})
        content = context.get("content", {})
        daily = context.get("daily_stats", [])
        publications = context.get("publications", [])

        excel_bytes = generate_excel_report(
            account_username=context.get("account", "").split("@")[1].split(" —")[0] if "@" in context.get("account", "") else "account",
            account_name=context.get("account", ""),
            period_str=context.get("period", ""),
            stats_summary=stats,
            content_summary=content,
            daily_stats=daily,
            publications=publications,
            ai_analysis=context.get("ai_analysis", "Нет данных"),
            dialogue_history=dialogue_history,
        )

        try:
            await loading_msg.delete()
        except Exception:
            pass

        filename = f"report_{context.get('period', 'period').replace(' ', '_').replace('–', '-')}.xlsx"
        await message.answer_document(
            BufferedInputFile(excel_bytes, filename=filename),
            caption="📊 Отчёт в формате Excel",
        )

    except Exception as e:
        logger.error(f"Excel error: {e}", exc_info=True)
        try:
            await loading_msg.delete()
        except Exception:
            pass
        await message.answer(f"❌ Ошибка: {e}", parse_mode=None)


# ── Back to menu (exit dialogue) ──

@router.message(F.text == "◀️ В меню")
async def back_to_menu(message: Message):
    if not await _check_admin(message.from_user.id):
        return

    state = _get_state(message.from_user.id)
    state["dialogue_active"] = False
    state["dialogue_history"] = []
    state["awaiting_ai_period"] = False

    await message.answer("Главное меню:", reply_markup=main_menu_kb())