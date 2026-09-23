"""AI dialogue flow: ask questions about the generated report."""
import logging
from datetime import date, datetime, timezone

from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from database import crud
from instagram.client import TokenExpiredError
from reports.generator import generate_report
from analytics.ai_analyzer import get_analyzer, AI_UNAVAILABLE_TEXT
from bot.states import AIForm
from bot.keyboards import ai_period_kb, dialogue_kb, main_menu_kb
from bot.helpers import send_report_result, send_excel, parse_period_input
from utils.formatters import format_context_for_ai

logger = logging.getLogger(__name__)
router = Router()

DIALOGUE_HINT = (
    "💬 Теперь можете задавать вопрос по этим данным.\n"
    "Можно задавать несколько вопросов подряд.\n"
    "Для выхода нажмите «◀️ В меню»."
)


async def _enter_dialogue(target_message: Message, state: FSMContext, context: dict):
    await state.update_data(report_context=context, dialogue_history=[])
    await state.set_state(AIForm.dialogue)
    await target_message.answer(DIALOGUE_HINT, reply_markup=dialogue_kb())


# ── 💬 Вопрос нейронке (main menu) ──

@router.message(F.text == "💬 Вопрос нейронке")
async def ai_chat_start(message: Message, state: FSMContext):
    data = await state.get_data()
    context = data.get("report_context")

    if context:
        await _enter_dialogue(message, state, context)
    else:
        await message.answer(
            "📅 Сначала соберу данные. За какой период?",
            reply_markup=ai_period_kb(),
        )


# ── 💬 Вопрос по отчёту (inline button after a report) ──

@router.callback_query(F.data == "question_about_report")
async def question_about_report(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    context = data.get("report_context")
    if not context:
        await callback.answer("⚠️ Нет данных. Сначала сгенерируйте отчёт.", show_alert=True)
        return
    await _enter_dialogue(callback.message, state, context)
    await callback.answer()


# ── 📄 Скачать Excel (inline button after a report) ──

@router.callback_query(F.data == "download_excel")
async def download_excel_callback(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    context = data.get("report_context")
    if not context:
        await callback.answer("⚠️ Нет данных для отчёта.", show_alert=True)
        return
    await callback.answer()
    await send_excel(callback.message, context, data.get("dialogue_history"))

# ── AI period selection (when there is no report context yet) ──

@router.callback_query(F.data.startswith("ai_period_"))
async def ai_period_callback(callback: CallbackQuery, state: FSMContext):
    period = callback.data.split("_")[2]  # week/month/custom
    data = await state.get_data()
    account_id = data.get("selected_account")

    if account_id:
        account = await crud.get_account_by_id(account_id)
    else:
        accounts = await crud.get_all_accounts()
        if not accounts:
            await callback.answer("❌ Нет аккаунтов.", show_alert=True)
            return
        account = accounts[0]
        await state.update_data(selected_account=account.id)

    if not account:
        await callback.answer("⚠️ Аккаунт не найден.", show_alert=True)
        return

    if period == "custom":
        await state.set_state(AIForm.waiting_custom_dates)
        await callback.message.edit_text(
            "📅 Введите дату или период:\n"
            "ДД.ММ (конкретный день) или ДД.ММ-ДД.ММ (период)\n"
            "Например: 15.09 или 01.09-15.09",
        )
        await callback.answer()
        return

    await callback.answer()
    try:
        await callback.message.delete()
    except Exception:
        pass
    await _report_then_dialogue(callback.message, state, account, period=period)


@router.message(AIForm.waiting_custom_dates)
async def ai_custom_dates_handler(message: Message, state: FSMContext):
    data = await state.get_data()
    account_id = data.get("selected_account")

    account = await crud.get_account_by_id(account_id) if account_id else None
    if not account:
        accounts = await crud.get_all_accounts()
        if not accounts:
            await state.set_state(None)
            await message.answer("❌ Нет аккаунтов.")
            return
        account = accounts[0]
        await state.update_data(selected_account=account.id)

    try:
        date_from, date_to = parse_period_input(
            message.text, datetime.now(timezone.utc).date()
        )
    except ValueError:
        # State is NOT cleared — user can retry immediately
        await message.answer(
            "❌ Неверный формат. Попробуйте ещё раз:\n"
            "ДД.ММ или ДД.ММ-ДД.ММ (например: 15.09 или 01.09-15.09)"
        )
        return

    await state.set_state(None)
    await _report_then_dialogue(message, state, account,
                                custom_from=date_from, custom_to=date_to)


async def _report_then_dialogue(message: Message, state: FSMContext, account,
                                period: str = "week",
                                custom_from: date = None, custom_to: date = None):
    loading_msg = await message.answer("⏳ Собираю данные...")
    try:
        result = await generate_report(
            account, period, custom_date_from=custom_from, custom_date_to=custom_to
        )
        await send_report_result(message, result, ask_question=False)
        await _enter_dialogue(message, state, result.get("context", {}))
    except TokenExpiredError:
        await message.answer(
            f"🔑 Токен аккаунта @{account.username} истёк.\n"
            "Обновите access_token в INSTAGRAM_ACCOUNTS (.env) и перезапустите бота.",
            parse_mode=None,
        )
    except Exception as e:
        logger.error(f"AI period error: {e}", exc_info=True)
        await message.answer(f"❌ Ошибка: {e}", parse_mode=None)
    finally:
        try:
            await loading_msg.delete()
        except Exception:
            pass


# ── Multi-turn dialogue (state = AIForm.dialogue) ──

@router.message(AIForm.dialogue, F.text)
async def ai_dialogue_handler(message: Message, state: FSMContext):
    data = await state.get_data()
    context = data.get("report_context")
    if not context:
        await state.set_state(None)
        await message.answer("⚠️ Нет данных для вопросов. Сначала сгенерируйте отчёт.")
        return

    analyzer = get_analyzer()
    if not analyzer:
        await message.answer(AI_UNAVAILABLE_TEXT)
        return

    history = list(data.get("dialogue_history", []))
    context_str = format_context_for_ai(context)

    loading_msg = await message.answer("🤔 Думаю...")
    try:
        answer = await analyzer.chat_with_context(context_str, history, message.text)

        history.append({"role": "user", "content": message.text})
        history.append({"role": "assistant", "content": answer})
        if len(history) > 20:  # prevent token overflow
            history = history[-20:]
        await state.update_data(dialogue_history=history)

        await message.answer(answer, parse_mode=None, reply_markup=dialogue_kb())
    except Exception as e:
        logger.error(f"AI chat error: {e}", exc_info=True)
        await message.answer(f"❌ Ошибка: {e}", parse_mode=None)
    finally:
        try:
            await loading_msg.delete()
        except Exception:
            pass


# ── Catch-all (must stay last) ──

@router.message(F.text)
async def fallback_handler(message: Message):
    await message.answer("Используйте меню для выбора действия.", reply_markup=main_menu_kb())

