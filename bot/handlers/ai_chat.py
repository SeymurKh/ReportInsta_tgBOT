"""AI dialogue flow: ask questions about the generated report.

AI dialogue is only reachable from the inline button under a generated report —
the main menu has no "AI" entry on purpose: without report context there is
nothing to ask about.
"""
import logging

from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from analytics.ai_analyzer import get_analyzer, AI_UNAVAILABLE_TEXT
from bot.states import AIForm
from bot.keyboards import dialogue_kb, main_menu_kb
from bot.helpers import send_excel
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

    question = (message.text or "").strip()
    if not question:
        await message.answer("Напишите вопрос текстом.", parse_mode=None)
        return

    history = list(data.get("dialogue_history", []))
    context_str = format_context_for_ai(context)

    loading_msg = await message.answer("🤔 Думаю...")
    try:
        answer = await analyzer.chat_with_context(context_str, history, question)

        history.append({"role": "user", "content": question})
        history.append({"role": "assistant", "content": answer})
        # Keep the dialogue useful for follow-up questions without growing
        # the FSM payload indefinitely. The analyzer applies a second bound
        # before sending messages to OpenAI.
        if len(history) > 12:
            history = history[-12:]
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