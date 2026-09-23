"""Period comparison flow (calendar weeks/months + custom dates)."""
import logging
from datetime import date, datetime, timedelta, timezone

from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from database import crud
from instagram.client import TokenExpiredError
from reports.generator import generate_comparison_periods_report
from bot.states import ComparisonForm
from bot.keyboards import comparison_calendar_kb
from bot.helpers import send_report_result, parse_comparison_input

logger = logging.getLogger(__name__)
router = Router()


def _calendar_weeks(today: date) -> tuple[date, date, date, date]:
    """This calendar week (Mon..yesterday) vs previous full week (Mon..Sun).
    On Monday, compares the two previous full weeks instead."""
    this_monday = today - timedelta(days=today.weekday())
    if this_monday > today - timedelta(days=1):
        # Today is Monday — "this week" is empty, shift one week back
        this_monday -= timedelta(days=7)
    p2_from, p2_to = this_monday, today - timedelta(days=1)
    p1_from = this_monday - timedelta(days=7)
    p1_to = this_monday - timedelta(days=1)
    return p1_from, p1_to, p2_from, p2_to


def _calendar_months(today: date) -> tuple[date, date, date, date]:
    """This month (1st..yesterday) vs previous full month.
    On the 1st, compares the two previous full months instead."""
    first_this = today.replace(day=1)
    if first_this > today - timedelta(days=1):
        # Today is the 1st — shift one month back
        p2_to = first_this - timedelta(days=1)
        p2_from = p2_to.replace(day=1)
        p1_to = p2_from - timedelta(days=1)
        p1_from = p1_to.replace(day=1)
        return p1_from, p1_to, p2_from, p2_to
    p2_from, p2_to = first_this, today - timedelta(days=1)
    p1_to = first_this - timedelta(days=1)
    p1_from = p1_to.replace(day=1)
    return p1_from, p1_to, p2_from, p2_to


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


@router.callback_query(F.data.startswith("comp_"))
async def comparison_period_callback(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    account_id = data.get("selected_account")
    if not account_id:
        await callback.answer("⚠️ Аккаунт не выбран.", show_alert=True)
        return
    account = await crud.get_account_by_id(account_id)
    if not account:
        await callback.answer("⚠️ Аккаунт не найден.", show_alert=True)
        return

    comp_type = callback.data.split("_")[1]
    today = datetime.now(timezone.utc).date()

    if comp_type == "week":
        periods = _calendar_weeks(today)
    elif comp_type == "month":
        periods = _calendar_months(today)
    elif comp_type == "custom":
        await state.set_state(ComparisonForm.waiting_custom_dates)
        await state.update_data(calendar_stage=1)
        await callback.message.edit_text(
            "Выберите начало первого периода:",
            reply_markup=comparison_calendar_kb(today.year, today.month, 1, today),
        )
        await callback.answer()
        return
        await callback.message.edit_text(
            "📅 Введите два периода в формате:\n"
            "ДД.ММ-ДД.ММ vs ДД.ММ-ДД.ММ\n"
            "Например: 01.09-07.09 vs 08.09-14.09",
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
    await _run_comparison(callback.message, state, account, *periods)


@router.callback_query(F.data.startswith("cmpcal_"))
async def comparison_calendar_callback(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    stage = data.get("calendar_stage")
    if stage is None:
        await callback.answer("Календарь устарел. Начните сравнение заново.", show_alert=True)
        return
    parts = callback.data.split("_")
    today = datetime.now(timezone.utc).date()
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
    today = datetime.now(timezone.utc).date()
    selected_month = data.get("p1_from") or today
    await state.update_data(calendar_stage=stage)
    await message.answer(
        "Выберите даты кнопками календаря — ввод периода текстом отключён.",
        reply_markup=comparison_calendar_kb(
            selected_month.year, selected_month.month, stage, today
        ),
    )
    return

    data = await state.get_data()
    account_id = data.get("selected_account")

    account = await crud.get_account_by_id(account_id) if account_id else None
    if not account:
        await state.set_state(None)
        await message.answer("⚠️ Аккаунт не выбран. Начните заново из меню.")
        return

    try:
        p1_from, p1_to, p2_from, p2_to = parse_comparison_input(
            message.text, datetime.now(timezone.utc).date()
        )
    except ValueError:
        # State is NOT cleared — user can retry immediately
        await message.answer(
            "❌ Неверный формат. Попробуйте ещё раз:\n"
            "ДД.ММ-ДД.ММ vs ДД.ММ-ДД.ММ\n"
            "Например: 01.09-07.09 vs 08.09-14.09"
        )
        return

    await state.set_state(None)
    await _run_comparison(message, state, account, p1_from, p1_to, p2_from, p2_to)
