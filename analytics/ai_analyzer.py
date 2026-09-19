import logging
from openai import AsyncOpenAI
from config import settings

logger = logging.getLogger(__name__)


class AIAnalyzer:
    def __init__(self):
        self.client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)
        self.model_report = settings.OPENAI_MODEL_REPORT
        self.model_chat = settings.OPENAI_MODEL_CHAT

    async def analyze_account(
        self, period: str, stats_summary: dict, content_summary: dict,
        best_post_info: str, trend: str
    ) -> str:
        prompt = f"""Ты — senior SMM-аналитик. Проанализируй статистику Instagram-аккаунта. Будь КРАТОК — максимум 10-12 строк.

ДАННЫЕ:
Период: {period}
Подписчики: {stats_summary.get('followers_end', 0)} (прирост: {stats_summary.get('followers_growth', 0)}, {stats_summary.get('followers_growth_pct', 0)}%)
Охват: {stats_summary.get('reach_total', 0)} | Просмотры: {stats_summary.get('views_total', 'н/д')} | Вовлечено: {stats_summary.get('accounts_engaged_total', 'н/д')}
Контент: {content_summary.get('total_posts', 0)} постов (Reels: {content_summary.get('total_reels', 0)}, Image: {content_summary.get('total_images', 0)}, Carousel: {content_summary.get('total_carousels', 0)})
Средние: лайки {content_summary.get('avg_likes', 0)}, охват {content_summary.get('avg_reach', 0)}, ER: {content_summary.get('engagement_rate', 0)}%
Лучший пост: {best_post_info}
Тренд: {trend}

ФОРМАТ ОТВЕТА (строго):
• 2-3 ключевых вывода (одной строкой каждый)
• 1 сильная + 1 слабая сторона
• 2 конкретные рекомендации с цифрами и приоритетом

Без воды. Без повтора данных. Только ДОПОЛНИТЕЛЬНАЯ ценность. Эмодзи допускаются."""

        return await self._call_openai(prompt, max_tokens=500)

    async def analyze_comparison(self, accounts_summary: str) -> str:
        prompt = f"""Ты — senior SMM-аналитик. Сравни два периода Instagram-аккаунта. Будь КРАТОК — максимум 8-10 строк.

ДАННЫЕ:
{accounts_summary}

ФОРМАТ ОТВЕТА (строго):
• 2-3 ключевых изменения между периодами
• Что улучшилось / что ухудшилось
• 1-2 рекомендации на основе динамики

Без воды. Только конкретика."""

        return await self._call_openai(prompt, max_tokens=400)

    async def generate_recommendations(
        self, stats_summary: dict, content_summary: dict, ai_analysis: str
    ) -> str:
        prompt = f"""На основе анализа и данных, сгенерируй 3-5 ПРАКТИЧЕСКИХ рекомендаций.

АНАЛИЗ: {ai_analysis}
ДАННЫЕ: подписчики {stats_summary.get('followers_end', 0)}, охват {stats_summary.get('reach_total', 0)}, ER {content_summary.get('engagement_rate', 0)}%, постов {content_summary.get('total_posts', 0)}

ТРЕБОВАНИЯ:
- Конкретные действия (не общие слова)
- Привязка к цифрам
- Ожидаемый эффект
- Приоритет (высокий/средний/низкий)"""

        return await self._call_openai(prompt, max_tokens=600)

    async def chat_with_context(self, context: str, history: list, question: str) -> str:
        """Answer user questions about account data with conversation history."""
        messages = [
            {"role": "system", "content": "Ты — профессиональный SMM-аналитик. Отвечай на русском языке, кратко и по делу, ссылаясь на конкретные цифры из данных."},
            {"role": "user", "content": f"ДАННЫЕ ОТЧЁТА:\n{context}"},
            {"role": "assistant", "content": "Понял, данные принял. Готов отвечать на вопросы по этим данным."},
        ]
        # Add conversation history
        for msg in history:
            messages.append(msg)
        # Add new question
        messages.append({"role": "user", "content": question})

        try:
            response = await self.client.chat.completions.create(
                model=self.model_chat,
                messages=messages,
                max_tokens=400,
                temperature=0.3,
            )
            return response.choices[0].message.content.strip()
        except Exception as e:
            logger.error(f"OpenAI chat error: {e}")
            return "⚠️ Ошибка при обращении к AI. Попробуйте позже."

    async def _call_openai(self, prompt: str, max_tokens: int = 1000, model: str = None) -> str:
        if model is None:
            model = self.model_report
        try:
            response = await self.client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": "Ты — профессиональный SMM-аналитик. Отвечай на русском языке."},
                    {"role": "user", "content": prompt},
                ],
                max_tokens=max_tokens,
                temperature=0.3,
            )
            return response.choices[0].message.content.strip()
        except Exception as e:
            logger.error(f"OpenAI error: {e}")
            return "⚠️ Анализ временно недоступен. Попробуйте позже."