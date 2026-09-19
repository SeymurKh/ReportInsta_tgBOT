import logging
from datetime import date, timedelta

from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InputMediaPhoto, BufferedInputFile
from aiogram.filters import Command

from config import settings
from database import crud
from reports.generator import generate_report, generate_comparison_periods_report
from analytics.ai_analyzer import AIAnalyzer
from bot.keyboards import main_menu_kb, accounts_kb, period_kb, comparison_period_kb
from utils.formatters import format_number, format_period

logger = logging.getLogger(__name__)
router = Router()
ai = AIAnalyzer()


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
    accounts = await crud.get_all_accounts()
    if not accounts:
        await message.answer("❌ Нет добавленных аккаунтов.")
        return
    message.bot._flow = "report"
    await message.answer("📱 Выберите аккаунт:", reply_markup=accounts_kb(accounts))


# ── Account selection callback ──

@router.callback_query(F.data.startswith("account_"))
async def account_callback(callback: CallbackQuery):
    if not await _check_admin(callback.from_user.id):
        return
    account_id = int(callback.data.split("_")[1])
    callback.bot._selected_account = account_id
    flow = getattr(callback.bot, "_flow", "report")

    if flow == "comparison":
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
    account_id = getattr(callback.bot, "_selected_account", None)

    if not account_id:
        await callback.answer("⚠️ Аккаунт не выбран.", show_alert=True)
        return

    account = await crud.get_account_by_id(account_id)
    if not account:
        await callback.answer("⚠️ Аккаунт не найден.", show_alert=True)
        return

    # Custom period - ask for dates
    if period == "custom":
        callback.bot._awaiting_custom_dates = True
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

    except Exception as e:
        logger.error(f"Report error: {e}", exc_info=True)
        try:
            await loading_msg.delete()
        except Exception:
            pass
        await callback.message.answer(f"❌ Ошибка при генерации отчёта: {e}", parse_mode=None)


# ── Custom dates text input ──

@router.message(F.text.regexp(r"^\d{2}\.\d{2}-\d{2}\.\d{2}$"))
async def custom_dates_handler(message: Message):
    if not await _check_admin(message.from_user.id):
        return

    account_id = getattr(message.bot, "_selected_account", None)
    awaiting = getattr(message.bot, "_awaiting_custom_dates", False)

    if not account_id or not awaiting:
        return

    message.bot._awaiting_custom_dates = False
    account = await crud.get_account_by_id(account_id)
    if not account:
        await message.answer("⚠️ Аккаунт не найден.")
        return

    try:
        parts = message.text.split("-")
        d1_parts = parts[0].split(".")
        d2_parts = parts[1].split(".")
        today = date.today()
        date_from = date(today.year, int(d1_parts[1]), int(d1_parts[0]))
        date_to = date(today.year, int(d2_parts[1]), int(d2_parts[0]))
    except (ValueError, IndexError):
        await message.answer("❌ Неверный формат. Используйте: ДД.ММ-ДД.ММ\nНапример: 01.09-15.09")
        return

    loading_msg = await message.answer("⏳ Собираю данные и генерирую отчёт...")

    try:
        result = await generate_report(account, custom_date_from=date_from, custom_date_to=date_to)
        text = result["text"]
        charts = result.get("charts", {})

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
    await callback.message.delete()
    await callback.answer()


# ── 📊 Сравнение периодов ──

@router.message(F.text == "📊 Сравнение")
async def comparison_handler(message: Message):
    if not await _check_admin(message.from_user.id):
        return
    accounts = await crud.get_all_accounts()
    if not accounts:
        await message.answer("❌ Нет добавленных аккаунтов.")
        return
    message.bot._flow = "comparison"
    await message.answer("📱 Выберите аккаунт для сравнения:", reply_markup=accounts_kb(accounts))


# ── Comparison period callbacks ──

@router.callback_query(F.data.startswith("comp_"))
async def comparison_period_callback(callback: CallbackQuery):
    if not await _check_admin(callback.from_user.id):
        return
    account_id = getattr(callback.bot, "_selected_account", None)
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
        callback.bot._awaiting_comparison_dates = True
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

        try:
            await loading_msg.delete()
        except Exception:
            pass

        if len(text) > 4096:
            for i in range(0, len(text), 4096):
                await callback.message.answer(text[i:i + 4096], parse_mode=None)
        else:
            await callback.message.answer(text, parse_mode=None)

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

    account_id = getattr(message.bot, "_selected_account", None)
    awaiting = getattr(message.bot, "_awaiting_comparison_dates", False)

    if not account_id or not awaiting:
        return

    message.bot._awaiting_comparison_dates = False
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

        p1_from = date(today.year, int(d1_from[1]), int(d1_from[0]))
        p1_to = date(today.year, int(d1_to[1]), int(d1_to[0]))
        p2_from = date(today.year, int(d2_from[1]), int(d2_from[0]))
        p2_to = date(today.year, int(d2_to[1]), int(d2_to[0]))
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

        try:
            await loading_msg.delete()
        except Exception:
            pass

        if len(text) > 4096:
            for i in range(0, len(text), 4096):
                await message.answer(text[i:i + 4096], parse_mode=None)
        else:
            await message.answer(text, parse_mode=None)

    except Exception as e:
        logger.error(f"Comparison error: {e}", exc_info=True)
        try:
            await loading_msg.delete()
        except Exception:
            pass
        await message.answer(f"❌ Ошибка: {e}", parse_mode=None)


# ── 💬 Вопрос нейронке ──

@router.message(F.text == "💬 Вопрос нейронке")
async def ai_chat_start(message: Message):
    if not await _check_admin(message.from_user.id):
        return
    message.bot._awaiting_ai_question = True
    await message.answer(
        "💬 Задайте вопрос по данным аккаунта.\n"
        "Например: «Почему упал охват на прошлой неделе?»\n\n"
        "Отправьте ваш вопрос текстом:"
    )


@router.message(F.text & ~F.text.startswith("/") & ~F.text.in_({
    "📊 Отчёты", "📊 Сравнение", "📱 Аккаунты", "💬 Вопрос нейронке"
}))
async def ai_chat_handler(message: Message):
    if not await _check_admin(message.from_user.id):
        return

    awaiting = getattr(message.bot, "_awaiting_ai_question", False)
    if not awaiting:
        return

    message.bot._awaiting_ai_question = False

    accounts = await crud.get_all_accounts()
    if not accounts:
        await message.answer("❌ Нет данных.")
        return

    account_id = getattr(message.bot, "_selected_account", None)
    account = None
    if account_id:
        account = await crud.get_account_by_id(account_id)
    if not account:
        account = accounts[0]

    today = date.today()
    date_from = today - timedelta(days=7)
    date_to = today - timedelta(days=1)

    stats_list = await crud.get_daily_stats(account.id, date_from, date_to)
    latest = await crud.get_latest_stats(account.id)

    context_parts = [f"Аккаунт: @{account.username}"]
    if latest:
        context_parts.append(f"Подписчики: {latest.followers}")
        context_parts.append(f"Последние данные на: {latest.date}")
    if stats_list:
        from analytics.calculations import calculate_period_summary
        s = calculate_period_summary(stats_list)
        context_parts.append(f"Охват за неделю: {s['reach_total']}")
        context_parts.append(f"Просмотры за неделю: {s['views_total']}")
        context_parts.append(f"Вовлечено за неделю: {s['accounts_engaged_total']}")
        context_parts.append(f"Прирост подписчиков: {s['followers_growth']}")

    context = "\n".join(context_parts)

    loading_msg = await message.answer("🤔 Думаю...")

    try:
        answer = await ai.chat_with_context(context, message.text)
        try:
            await loading_msg.delete()
        except Exception:
            pass
        await message.answer(answer, parse_mode=None)
    except Exception as e:
        logger.error(f"AI chat error: {e}", exc_info=True)
        try:
            await loading_msg.delete()
        except Exception:
            pass
        await message.answer(f"❌ Ошибка: {e}", parse_mode=None)