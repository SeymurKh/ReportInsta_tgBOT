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
REPORT_SYSTEM_PROMPT += (
    " Никогда не подменяй отсутствие метрики нулём. Разделяй факт из данных, "
    "гипотезу и рекомендацию. Не утверждай причинность, если в контексте нет "
    "данных для её доказательства."
)
REPORT_SYSTEM_PROMPT += (
    " Метрики аккаунта (охват, вовлечённые аккаунты, взаимодействия, просмотры "
    "профиля) — итоговые/уникальные значения из Instagram API за период; суммы "
    "по дням и метрики публикаций — другие величины, не сравнивай их напрямую."
)
REPORT_SYSTEM_PROMPT += (
    " Примечания «Сверка взаимодействий» и «КАК ЧИТАТЬ» — служебная информация "
    "отчёта: это не проблемы и не повод для рекомендаций. Даты публикаций бери "
    "только из поля date календаря, никогда из текста капшена."
)

CHAT_SYSTEM_PROMPT = (
    "Ты помощник по SMM-аналитике. Отвечай на русском, кратко и по существу. "
    "Используй только данные отчёта и факты из диалога. Для важных выводов указывай цифры. "
    "Если нужного показателя нет, скажи: 'В отчёте нет этих данных' — не додумывай. "
    "Обычно отвечай в 3–6 коротких строк; если вопрос требует сравнения, используй компактные пункты. "
    "Не пересказывай весь отчёт и не повторяй уже сказанное без необходимости."
)
CHAT_SYSTEM_PROMPT += (
    " Сначала ответь прямо на вопрос, затем приведи максимум 2 цифры из "
    "контекста и один практический вывод. Не пересказывай контекст целиком. "
    "Если вопрос требует данных, которых нет, так и напиши."
)
CHAT_SYSTEM_PROMPT += (
    " В контексте могут встречаться маркеры «опущено…» и «контекст сокращён» — "
    "это означает сокращение контекста диалога, а НЕ неполноту самого отчёта: "
    "пользователь видит полный отчёт. Никогда не делай вывод, что отчёт неполный "
    "или недостоверный, на основании этих маркеров. Отвечай о достоверности "
    "только по фактическим данным: полнота дней, метрик и публикаций указана "
    "в строках «Качество данных» и «Полнота»."
)

MAX_CONTEXT_CHARS = 14000
MAX_HISTORY_MESSAGES = 12
MAX_HISTORY_MESSAGE_CHARS = 1200
MAX_CHAT_RESPONSE_CHARS = 3500
# For reasoning models max_completion_tokens includes hidden reasoning tokens:
# a budget of ~700 can be consumed entirely by reasoning (finish_reason=length
# with empty content). Keep generous budgets so the visible answer always fits.
REPORT_MAX_COMPLETION_TOKENS = 2400
CHAT_MAX_COMPLETION_TOKENS = 2400


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
        data_quality: dict | None = None,
    ) -> str:
        stories_line = "Сторис: нет данных"
        if stories_summary and stories_summary.get("total_stories"):
            stories_line = (
                f"Сторис: {stories_summary['total_stories']} шт.; просмотры {stories_summary['total_views']} "
                f"(ср. {stories_summary['avg_views']}), охват {stories_summary['total_reach']}, "
                f"ответы {stories_summary['total_replies']}, выходы {stories_summary['exit_rate']}%"
            )
        quality_line = ""
        if data_quality:
            missing = data_quality.get("metric_missing_days", {})
            missing_text = ", ".join(
                f"{name}: {count} дн." for name, count in missing.items() if count
            )
            quality_line = (
                f"\n- Полнота: {data_quality.get('available_days', 0)} "
                f"из {data_quality.get('expected_days', 'н/д')} дней; "
                f"неполные дни: {data_quality.get('partial_days', 0)}; "
                f"старые дни с неизвестной полнотой: {data_quality.get('legacy_unknown_days', 0)}; "
                f"отсутствующие метрики: {missing_text or 'нет известных пропусков'}"
            )
            if content_summary.get("partial_insights_posts") or content_summary.get("legacy_unknown_insights"):
                quality_line += (
                    f"; неполные Insights публикаций: {content_summary.get('partial_insights_posts', 0)}; "
                    f"собраны до внедрения учёта полноты: {content_summary.get('legacy_unknown_insights', 0)}"
                )
            if stories_summary:
                quality_line += (
                    f"; неполные Insights сторис: {stories_summary.get('partial_insights_stories', 0)}; "
                    f"собраны до внедрения учёта полноты: {stories_summary.get('legacy_unknown_insights', 0)}"
                )
        current_followers = stats_summary.get("followers_current")
        follower_growth = stats_summary.get("followers_growth")
        follower_growth_pct = stats_summary.get("followers_growth_pct")
        prompt = f"""Проанализируй Instagram-аккаунт за период {period}.

ДАННЫЕ:
- Подписчики сейчас: {current_followers if current_followers is not None else 'н/д'}; прирост за период: {follower_growth if follower_growth is not None else 'н/д'} ({follower_growth_pct if follower_growth_pct is not None else 'н/д'}%)
- Охват (уникальные): {stats_summary.get('reach_total', 0)}; Просмотры (с повторами): {stats_summary.get('views_total', 'н/д')}; Вовлечённые (уникальные): {stats_summary.get('accounts_engaged_total', 'н/д')}; Взаимодействия (все): {stats_summary.get('total_interactions_total', 'н/д')}; Просмотры профиля: {stats_summary.get('profile_views_total', 'н/д')}
- Контент: {content_summary.get('total_posts', 0)} публикаций; Reels {content_summary.get('total_reels', 0)}; видео {content_summary.get('total_videos', 0)}; фото {content_summary.get('total_images', 0)}; карусели {content_summary.get('total_carousels', 0)}
- Средние значения на публикацию: лайки {content_summary.get('avg_likes', 0)}; охват {content_summary.get('avg_reach', 0)}; ER публикаций {content_summary.get('engagement_rate', 0)}% (total_interactions Insights {content_summary.get('total_interactions', 'н/д')} / суммарный охват публикаций {content_summary.get('total_reach', 'н/д')})
- Сверка взаимодействий: сумма ❤/💬/💾/📤 {content_summary.get('component_interactions', 'н/д')} + другие действия Instagram = total_interactions {content_summary.get('total_interactions', 'н/д')}. Это норма расширенного счётчика Instagram: не считай расхождение проблемой и не предлагай его исправлять.
- {stories_line}
- Качество данных:{quality_line or ' не указано'}
- Лучший пост (по оценочному баллу: лайки + 2×комментарии + 3×сохранения + 4×репосты на охват; он не обязательно имеет максимальный охват): {best_post_info}
- Скорость роста сейчас (сравнение соседних окон максимум по 7 дней, не прирост за весь период): {trend}

ФОРМАТ: максимум 8 коротких строк.
1) 2–3 вывода с цифрами.
2) Сильная сторона и слабая сторона.
3) Ровно 2 действия: что сделать, в каком приоритете и на какой показатель это должно повлиять.
Не пересказывай входные данные и не добавляй вступление. Не называй охват выбранного лучшего поста слабым только потому, что он ниже среднего: пост выбран по оценочному баллу взаимодействий с учётом охвата. Не трактуй краткосрочное снижение как отрицательный прирост за весь период. Если показатели противоречат друг другу или их база расчёта различается, отметь это вместо вывода причины."""
        return await self._call_openai(prompt, max_tokens=REPORT_MAX_COMPLETION_TOKENS)

    async def analyze_comparison(self, accounts_summary: str) -> str:
        prompt = f"""Сравни два периода Instagram-аккаунта.

ДАННЫЕ:
{accounts_summary}

ФОРМАТ: максимум 7 коротких строк.
- 2–3 главных изменения с цифрами;
- что улучшилось и что ухудшилось;
- 1–2 приоритетных действия на основе динамики.
Не пересказывай таблицу и не выдумывай причины, которых нет в данных."""
        prompt += (
            "\nПравила сравнения: используй средние за день при разной длине периодов; "
            "связывай рекомендацию с форматом или конкретной публикацией только если "
            "это подтверждено переданными данными; максимум 5 коротких пунктов."
        )
        return await self._call_openai(prompt, max_tokens=REPORT_MAX_COMPLETION_TOKENS)

    async def generate_recommendations(
        self, stats_summary: dict, content_summary: dict, ai_analysis: str,
    ) -> str:
        prompt = f"""Сформируй 3 практических рекомендации для SMM на основе данных.

АНАЛИЗ: {ai_analysis}
ДАННЫЕ: подписчики {stats_summary.get('followers_end', 0)}, охват {stats_summary.get('reach_total', 0)}, ER {content_summary.get('engagement_rate', 0)}%, публикаций {content_summary.get('total_posts', 0)}.

Для каждой рекомендации укажи: приоритет (высокий/средний/низкий), конкретное действие, метрику контроля и ожидаемый эффект. Максимум 6 коротких строк, без общих советов."""
        return await self._call_openai(prompt, max_tokens=REPORT_MAX_COMPLETION_TOKENS)

    async def chat_with_context(self, context: str, history: list, question: str) -> str:
        """Answer a follow-up question using a bounded report context and history."""
        # Never trim silently: a silent cut makes the model believe the report
        # itself is incomplete and it starts doubting real data.
        if len(context) <= MAX_CONTEXT_CHARS:
            bounded_context = context
        else:
            cut = context[:MAX_CONTEXT_CHARS]
            newline = cut.rfind("\n")
            if newline > MAX_CONTEXT_CHARS // 2:
                cut = cut[:newline]
            bounded_context = cut + "\n…[контекст сокращён — полные данные в отчёте и Excel]"
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
                max_completion_tokens=CHAT_MAX_COMPLETION_TOKENS,
                reasoning_effort=self.reasoning_chat,
            )
            content = response.choices[0].message.content or ""
            if not content.strip():
                logger.warning(
                    "OpenAI chat returned empty content (finish_reason=%s, usage=%s)",
                    response.choices[0].finish_reason,
                    response.usage,
                )
            content = content.strip()
            if len(content) > MAX_CHAT_RESPONSE_CHARS:
                logger.warning("OpenAI chat response truncated from %d chars", len(content))
                content = content[:MAX_CHAT_RESPONSE_CHARS].rstrip() + "…"
            return content or "AI не вернул текстовый ответ. Попробуйте задать вопрос ещё раз."
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
