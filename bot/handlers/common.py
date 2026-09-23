"""Common handlers: /start, /status, main menu, account selection, back."""
import logging

from aiogram import Router, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, CallbackQuery

from database import crud
from bot.keyboards import (
    main_menu_kb, accounts_kb, period_kb, comparison_period_kb,
)
from bot.helpers import send_excel
from utils.formatters import format_number

logger = logging.getLogger(__name__)
router = Router()


@router.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "👋 Привет! Я бот аналитики Instagram.\n\n"
        "Выбери действие из меню ниже:",
        reply_markup=main_menu_kb(),
    )


@router.message(Command("status"))
async def cmd_status(message: Message):
    """Health check: accounts, last data, stories in DB."""
    accounts = await crud.get_all_accounts()
    if not accounts:
        await message.answer("❌ Нет добавленных аккаунтов.")
        return
    lines = ["🩺 Статус:\n"]
    for acc in accounts:
        latest = await crud.get_latest_stats(acc.id)
        last_date = latest.date.strftime("%d.%m.%Y") if latest else "нет данных"
        lines.append(f"• @{acc.username} — последние данные: {last_date}")
    await message.answer("\n".join(lines), parse_mode=None)


# ── Main menu buttons ──

async def _show_accounts(message: Message, state: FSMContext, flow: str, text: str):
    accounts = await crud.get_all_accounts()
    if not accounts:
        await message.answer("❌ Нет добавленных аккаунтов.")
        return
    await state.set_state(None)
    await state.update_data(flow=flow)
    await message.answer(text, reply_markup=accounts_kb(accounts))


@router.message(F.text == "📊 Отчёты")
async def reports_handler(message: Message, state: FSMContext):
    await _show_accounts(message, state, "report", "📱 Выберите аккаунт:")


@router.message(F.text == "📊 Сравнение")
async def comparison_handler(message: Message, state: FSMContext):
    await _show_accounts(message, state, "comparison", "📱 Выберите аккаунт для сравнения:")


@router.message(F.text == "📱 Аккаунты")
async def accounts_handler(message: Message, state: FSMContext):
    await state.clear()
    accounts = await crud.get_all_accounts()
    if not accounts:
        await message.answer("❌ Нет добавленных аккаунтов.")
        return
    lines = ["📱 Аккаунты:\n"]
    for acc in accounts:
        latest = await crud.get_latest_stats(acc.id)
        followers = format_number(latest.followers) if latest else "—"
        lines.append(f"• @{acc.username} — {acc.name}\n  👥 {followers} подписчиков")
    await message.answer("\n".join(lines), parse_mode=None, reply_markup=main_menu_kb())


@router.message(F.text == "◀️ В меню")
async def back_to_menu(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("Главное меню:", reply_markup=main_menu_kb())


@router.message(F.text == "📄 Скачать Excel")
async def download_excel_handler(message: Message, state: FSMContext):
    data = await state.get_data()
    context = data.get("report_context")
    if not context:
        await message.answer("⚠️ Нет данных для отчёта. Сначала сгенерируйте отчёт.")
        return
    await send_excel(message, context, data.get("dialogue_history"))


# ── Account selection (shared by report / stories / comparison flows) ──

@router.callback_query(F.data.startswith("account_"))
async def account_callback(callback: CallbackQuery, state: FSMContext):
    account_id = int(callback.data.split("_")[1])
    data = await state.get_data()
    flow = data.get("flow", "report")
    await state.update_data(selected_account=account_id)

    if flow == "comparison":
        await callback.message.edit_text("📅 Выберите первый период:", reply_markup=comparison_period_kb())
    else:
        await callback.message.edit_text("📅 Выберите период:", reply_markup=period_kb())
    await callback.answer()


@router.callback_query(F.data == "back")
async def back_callback(callback: CallbackQuery, state: FSMContext):
    await state.set_state(None)
    try:
        await callback.message.delete()
    except Exception:
        pass
    await callback.message.answer("Главное меню:", reply_markup=main_menu_kb())
    await callback.answer()
