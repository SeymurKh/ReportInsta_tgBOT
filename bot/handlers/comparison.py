"""Period comparison flow (custom dates selected via calendar)."""
import logging
from datetime import date

from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from database import crud
from instagram.client import TokenExpiredError
from reports.generator import generate_comparison_periods_report
from bot.states import ComparisonForm
from bot.keyboards import comparison_calendar_kb
from bot.helpers import send_report_result
from utils.timezones import app_today

logger = logging.getLogger(__name__)
router = Router()


async def _run_comparison(message: Message, state: FSMContext, account,
                          p1_from: date, p1_to: date, p2_from: date, p2_to: date):
    loading_msg = await message.answer("⏳ Сравниваю периоды...")
    try:
        result = await generate_comparison_periods_report(account, p1_from, p1_to, p2_from, p2_to)
        await state.update_data(report_context=result.get("context", {}))
        await send_report_result(message, result)
    except TokenExpiredError:
        await message.answer(
            f"🔑 Токен аккаунта @{account.username} истёк.\n"
            "Обновите access_token в INSTAGRAM_ACCOUNTS (.env) и перезапустите бота.",
            parse_mode=None,
        )
    except Exception as e:
        logger.error(f"Comparison error: {e}", exc_info=True)
        await message.answer(f"❌ Ошибка: {e}", parse_mode=None)
    finally:
        try:
            await loading_msg.delete()
        except Exception:
            pass


@router.callback_query(F.data.startswith("cmpcal_"))
async def comparison_calendar_callback(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    stage = data.get("calendar_stage")
    if stage is None:
        await callback.answer("Календарь устарел. Начните сравнение заново.", show_alert=True)
        return
    parts = callback.data.split("_")
    today = app_today()
    if parts[1] == "noop":
        await callback.answer()
        return
    if parts[1] == "nav":
        year, month, direction = int(parts[3]), int(parts[4]), int(parts[5])
        month += direction
        if month == 0:
            year, month = year - 1, 12
        elif month == 13:
            year, month = year + 1, 1
        await callback.message.edit_reply_markup(
            reply_markup=comparison_calendar_kb(year, month, stage, today)
        )
        await callback.answer()
        return

    selected = date(int(parts[3]), int(parts[4]), int(parts[5]))
    if stage == 2 and selected < data["p1_from"]:
        await callback.answer("Дата окончания не может быть раньше начала.", show_alert=True)
        return
    if stage == 3 and selected <= data["p1_to"]:
        await callback.answer("Второй период должен идти после первого.", show_alert=True)
        return
    if stage == 4 and selected < data["p2_from"]:
        await callback.answer("Дата окончания не может быть раньше начала.", show_alert=True)
        return

    keys = {1: "p1_from", 2: "p1_to", 3: "p2_from", 4: "p2_to"}
    await state.update_data(**{keys[stage]: selected})
    if stage == 4:
        account_id = data.get("selected_account")
        account = await crud.get_account_by_id(account_id) if account_id else None
        if not account:
            await callback.answer("Аккаунт не найден.", show_alert=True)
            return
        await state.set_state(None)
        await callback.answer()
        await _run_comparison(
            callback.message, state, account,
            data["p1_from"], data["p1_to"], data["p2_from"], selected,
        )
        return

    next_stage = stage + 1
    await state.update_data(calendar_stage=next_stage)
    prompts = {
        2: "Выберите конец первого периода:",
        3: "Выберите начало второго периода:",
        4: "Выберите конец второго периода:",
    }
    await callback.message.edit_text(
        prompts[next_stage],
        reply_markup=comparison_calendar_kb(selected.year, selected.month, next_stage, today),
    )
    await callback.answer()


@router.message(ComparisonForm.waiting_custom_dates)
async def custom_comparison_dates_handler(message: Message, state: FSMContext):
    # Date input is intentionally calendar-only. This also handles text sent
    # while an old/stale custom-date prompt is still visible.
    data = await state.get_data()
    stage = data.get("calendar_stage", 1)
    today = app_today()
    selected_month = data.get("p1_from") or today
    await state.update_data(calendar_stage=stage)
    await message.answer(
        "Выберите даты кнопками календаря — ввод периода текстом отключён.",
        reply_markup=comparison_calendar_kb(
            selected_month.year, selected_month.month, stage, today
        ),
    )
