"""Report flow: period selection + custom dates (stories are a section of the report)."""
import logging
from datetime import date

from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from database import crud
from instagram.client import TokenExpiredError
from reports.generator import generate_report
from bot.states import ReportForm
from bot.helpers import send_report_result
from bot.keyboards import report_calendar_kb
from utils.timezones import app_today

logger = logging.getLogger(__name__)
router = Router()


async def _run_report(message: Message, state: FSMContext, account,
                      period: str = "week",
                      custom_from: date = None, custom_to: date = None):
    loading_msg = await message.answer("⏳ Собираю данные и генерирую отчёт...")
    try:
        result = await generate_report(
            account, period, custom_date_from=custom_from, custom_date_to=custom_to
        )
        await state.update_data(report_context=result.get("context", {}))
        await send_report_result(message, result)
    except TokenExpiredError:
        await message.answer(
            f"🔑 Токен аккаунта @{account.username} истёк.\n"
            "Обновите access_token в INSTAGRAM_ACCOUNTS (.env) и перезапустите бота.",
            parse_mode=None,
        )
    except Exception as e:
        logger.error(f"Report error: {e}", exc_info=True)
        await message.answer(f"❌ Ошибка при генерации отчёта: {e}", parse_mode=None)
    finally:
        try:
            await loading_msg.delete()
        except Exception:
            pass


@router.callback_query(F.data.startswith("repcal_"))
async def report_calendar_callback(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    stage = data.get("report_calendar_stage")
    if stage is None:
        await callback.answer("Календарь устарел. Начните отчёт заново.", show_alert=True)
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
            reply_markup=report_calendar_kb(year, month, stage, today)
        )
        await callback.answer()
        return

    selected = date(int(parts[3]), int(parts[4]), int(parts[5]))
    if stage == 2 and selected < data["report_from"]:
        await callback.answer("Дата окончания не может быть раньше начала.", show_alert=True)
        return
    await state.update_data(**({"report_from": selected} if stage == 1 else {"report_to": selected}))
    if stage == 1:
        await state.update_data(report_calendar_stage=2)
        await callback.message.edit_text(
            "Выберите конец периода отчёта:",
            reply_markup=report_calendar_kb(selected.year, selected.month, 2, today),
        )
        await callback.answer()
        return

    account_id = data.get("selected_account")
    account = await crud.get_account_by_id(account_id) if account_id else None
    if not account:
        await callback.answer("Аккаунт не найден.", show_alert=True)
        return
    await state.set_state(None)
    await callback.answer()
    await _run_report(
        callback.message, state, account, period="custom",
        custom_from=data["report_from"], custom_to=selected,
    )


@router.message(ReportForm.waiting_custom_dates)
async def custom_dates_handler(message: Message, state: FSMContext):
    # Date input is intentionally calendar-only. This also handles text sent
    # while an old/stale custom-date prompt is still visible.
    data = await state.get_data()
    stage = data.get("report_calendar_stage", 1)
    today = app_today()
    selected_month = data.get("report_from") or today
    await state.update_data(report_calendar_stage=stage)
    await message.answer(
        "Выберите даты кнопками календаря — ввод периода текстом отключён.",
        reply_markup=report_calendar_kb(
            selected_month.year, selected_month.month, stage, today
        ),
    )
