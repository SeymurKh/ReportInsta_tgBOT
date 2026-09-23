"""OpenAI integration for compact, data-grounded SMM analysis."""

import logging

from openai import AsyncOpenAI

from config import settings

logger = logging.getLogger(__name__)

REPORT_SYSTEM_PROMPT = (
    "Ты senior SMM-аналитик. Отвечай на русском языке, опирайся только на переданные данные. "
    "Не выдумывай причины, значения и тренды; если данных недостаточно, прямо скажи об этом. "
    "Пиши конкретно, без вступления, повторов и общих фраз."
)

CHAT_SYSTEM_PROMPT = (
    "Ты помощник по SMM-аналитике. Отвечай на русском, кратко и по существу. "
    "Используй только данные отчёта и факты из диалога. Для важных выводов указывай цифры. "
    "Если нужного показателя нет, скажи: 'В отчёте нет этих данных' — не додумывай. "
    "Обычно отвечай в 3–6 коротких строк; если вопрос требует сравнения, используй компактные пункты. "
    "Не пересказывай весь отчёт и не повторяй уже сказанное без необходимости."
)

MAX_CONTEXT_CHARS = 14000
MAX_HISTORY_MESSAGES = 12
MAX_HISTORY_MESSAGE_CHARS = 1200


def _compact_history(history: list) -> list[dict]:
    """Keep enough context for follow-up questions without overflowing the model budget."""
    compact = []
    for message in history[-MAX_HISTORY_MESSAGES:]:
        role = message.get("role")
        content = str(message.get("content", "")).strip()
        if role not in {"user", "assistant"} or not content:
            continue
        compact.append({"role": role, "content": content[:MAX_HISTORY_MESSAGE_CHARS]})
    return compact


class AIAnalyzer:
    def __init__(self):
        if not settings.OPENAI_API_KEY:
            raise RuntimeError("OPENAI_API_KEY is not set")
        self.client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)
        self.model = settings.OPENAI_MODEL
        self.reasoning_report = settings.OPENAI_REASONING_REPORT
        self.reasoning_chat = settings.OPENAI_REASONING_CHAT

    async def analyze_account(
        self, period: str, stats_summary: dict, content_summary: dict,
        best_post_info: str, trend: str, stories_summary: dict | None = None,
    ) -> str:
        stories_line = "Сторис: нет данных"
        if stories_summary and stories_summary.get("total_stories"):
            stories_line = (
                f"Сторис: {stories_summary['total_stories']} шт.; просмотры {stories_summary['total_views']} "
                f"(ср. {stories_summary['avg_views']}), охват {stories_summary['total_reach']}, "
                f"ответы {stories_summary['total_replies']}, выходы {stories_summary['exit_rate']}%"
            )
        prompt = f"""Проанализируй Instagram-аккаунт за период {period}.

ДАННЫЕ:
- Подписчики на конец: {stats_summary.get('followers_end', 0)}; прирост: {stats_summary.get('followers_growth', 0)} ({stats_summary.get('followers_growth_pct', 0)}%)
- Охват: {stats_summary.get('reach_total', 0)}; просмотры: {stats_summary.get('views_total', 'н/д')}; вовлечено: {stats_summary.get('accounts_engaged_total', 'н/д')}
- Контент: {content_summary.get('total_posts', 0)} публикаций; Reels {content_summary.get('total_reels', 0)}; видео {content_summary.get('total_videos', 0)}; фото {content_summary.get('total_images', 0)}; карусели {content_summary.get('total_carousels', 0)}
- Средние значения: лайки {content_summary.get('avg_likes', 0)}; охват {content_summary.get('avg_reach', 0)}; ER {content_summary.get('engagement_rate', 0)}%
- {stories_line}
- Лучший пост: {best_post_info}
- Наблюдаемый тренд: {trend}

ФОРМАТ: максимум 8 коротких строк.
1) 2–3 вывода с цифрами.
2) Сильная сторона и слабая сторона.
3) Ровно 2 действия: что сделать, в каком приоритете и на какой показатель это должно повлиять.
Не пересказывай входные данные и не добавляй вступление."""
        return await self._call_openai(prompt, max_tokens=700)

    async def analyze_comparison(self, accounts_summary: str) -> str:
        prompt = f"""Сравни два периода Instagram-аккаунта.

ДАННЫЕ:
{accounts_summary}

ФОРМАТ: максимум 7 коротких строк.
- 2–3 главных изменения с цифрами;
- что улучшилось и что ухудшилось;
- 1–2 приоритетных действия на основе динамики.
Не пересказывай таблицу и не выдумывай причины, которых нет в данных."""
        return await self._call_openai(prompt, max_tokens=550)

    async def generate_recommendations(
        self, stats_summary: dict, content_summary: dict, ai_analysis: str,
    ) -> str:
        prompt = f"""Сформируй 3 практических рекомендации для SMM на основе данных.

АНАЛИЗ: {ai_analysis}
ДАННЫЕ: подписчики {stats_summary.get('followers_end', 0)}, охват {stats_summary.get('reach_total', 0)}, ER {content_summary.get('engagement_rate', 0)}%, публикаций {content_summary.get('total_posts', 0)}.

Для каждой рекомендации укажи: приоритет (высокий/средний/низкий), конкретное действие, метрику контроля и ожидаемый эффект. Максимум 6 коротких строк, без общих советов."""
        return await self._call_openai(prompt, max_tokens=650)

    async def chat_with_context(self, context: str, history: list, question: str) -> str:
        """Answer a follow-up question using a bounded report context and history."""
        bounded_context = context[:MAX_CONTEXT_CHARS]
        messages = [
            {"role": "system", "content": CHAT_SYSTEM_PROMPT},
            {"role": "user", "content": f"КОНТЕКСТ ОТЧЁТА:\n{bounded_context}"},
        ]
        messages.extend(_compact_history(history))
        messages.append({"role": "user", "content": question.strip()[:2000]})

        try:
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                max_completion_tokens=900,
                reasoning_effort=self.reasoning_chat,
            )
            content = response.choices[0].message.content or ""
            if not content.strip():
                logger.warning(
                    "OpenAI chat returned empty content (finish_reason=%s, usage=%s)",
                    response.choices[0].finish_reason,
                    response.usage,
                )
            return content.strip() or "AI не вернул текстовый ответ. Попробуйте задать вопрос ещё раз."
        except Exception as error:
            logger.error("OpenAI chat error: %s", error, exc_info=True)
            return "⚠️ Не удалось получить ответ AI. Попробуйте повторить вопрос позже."

    async def _call_openai(self, prompt: str, max_tokens: int = 1000, model: str = None) -> str:
        try:
            response = await self.client.chat.completions.create(
                model=model or self.model,
                messages=[
                    {"role": "system", "content": REPORT_SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
                max_completion_tokens=max_tokens,
                reasoning_effort=self.reasoning_report,
            )
            content = response.choices[0].message.content or ""
            if not content.strip():
                logger.warning(
                    "OpenAI analysis returned empty content (finish_reason=%s, usage=%s)",
                    response.choices[0].finish_reason,
                    response.usage,
                )
            return content.strip() or "AI не вернул анализ. Попробуйте сформировать отчёт ещё раз."
        except Exception as error:
            logger.error("OpenAI analysis error: %s", error, exc_info=True)
            return "⚠️ Анализ временно недоступен. Попробуйте позже."


_analyzer_instance: AIAnalyzer | None = None


def get_analyzer() -> AIAnalyzer | None:
    """Return the shared analyzer, or None when OpenAI is not configured."""
    global _analyzer_instance
    if _analyzer_instance is not None:
        return _analyzer_instance
    if not settings.OPENAI_API_KEY:
        logger.warning("OPENAI_API_KEY is not set — AI features disabled")
        return None
    try:
        _analyzer_instance = AIAnalyzer()
    except Exception as error:
        logger.error("Failed to init AIAnalyzer: %s", error)
        return None
    return _analyzer_instance


AI_UNAVAILABLE_TEXT = "⚠️ AI недоступен: проверьте OPENAI_API_KEY в настройках."
