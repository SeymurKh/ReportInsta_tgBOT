"""Offline tests for AI prompt grounding and response safety."""

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from analytics.ai_analyzer import AIAnalyzer


def _response(content, finish_reason="stop"):
    return SimpleNamespace(
        choices=[SimpleNamespace(
            message=SimpleNamespace(content=content),
            finish_reason=finish_reason,
        )],
        usage=SimpleNamespace(),
    )


class AIPromptTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.analyzer = AIAnalyzer.__new__(AIAnalyzer)
        self.analyzer.model = "test-model"
        self.analyzer.reasoning_chat = "low"
        self.analyzer.reasoning_report = "low"
        self.analyzer.client = SimpleNamespace(
            chat=SimpleNamespace(
                completions=SimpleNamespace(
                    create=AsyncMock(return_value=_response("short answer"))
                )
            )
        )

    async def test_account_prompt_defines_best_post_and_short_term_trend(self):
        await self.analyzer.analyze_account(
            "01.09–29.09",
            {"followers_current": 208, "followers_growth": 23, "followers_growth_pct": None},
            {"total_posts": 5, "avg_likes": 60, "avg_reach": 841,
             "engagement_rate": 8.9, "total_interactions": 375, "total_reach": 4204},
            '"Best post" | reach 121',
            "declining",
        )

        prompt = self.analyzer.client.chat.completions.create.await_args.kwargs["messages"][1]["content"]
        self.assertIn("не обязательно имеет максимальный охват", prompt)
        self.assertIn("не прирост за весь период", prompt)
        self.assertIn("Не называй охват выбранного лучшего поста слабым", prompt)

    async def test_comparison_prompt_contains_new_data_rules(self):
        result = await self.analyzer.analyze_comparison(
            "P1 reach 100; P2 reach 200\nFORMAT PERFORMANCE BY PERIOD:\n"
            "REELS: P1 ER=4%; P2 ER=7%\nData availability: P1 7/7 days, P2 5/14 days"
        )

        self.assertEqual(result, "short answer")
        prompt = self.analyzer.client.chat.completions.create.await_args.kwargs["messages"][1]["content"]
        self.assertIn("FORMAT PERFORMANCE BY PERIOD", prompt)
        self.assertIn("средние за день", prompt)
        self.assertIn("разной длине периодов", prompt)

    async def test_chat_prompt_contains_format_context_and_history(self):
        result = await self.analyzer.chat_with_context(
            "Report period\nFormats:\nREELS ER 7%\nData quality: 7/7",
            [{"role": "user", "content": "What is better?"}],
            "Why are Reels better?",
        )

        self.assertEqual(result, "short answer")
        messages = self.analyzer.client.chat.completions.create.await_args.kwargs["messages"]
        combined = "\n".join(message["content"] for message in messages)
        self.assertIn("REELS ER 7%", combined)
        self.assertIn("What is better?", combined)
        self.assertIn("Why are Reels better?", combined)

    async def test_empty_ai_response_has_safe_fallback(self):
        self.analyzer.client.chat.completions.create = AsyncMock(
            return_value=_response("   ", "length")
        )

        result = await self.analyzer.chat_with_context("Context", [], "Question")

        self.assertTrue(result.strip())
        self.assertNotEqual(result, "   ")

    async def test_long_ai_response_is_bounded_for_telegram(self):
        self.analyzer.client.chat.completions.create = AsyncMock(
            return_value=_response("x" * 5000)
        )

        result = await self.analyzer.chat_with_context("Context", [], "Question")

        self.assertLessEqual(len(result), 3501)
        self.assertEqual(result[-1], chr(8230))

    async def test_token_budgets_leave_room_for_reasoning_models(self):
        """Regression: with reasoning_effort the hidden reasoning tokens consume
        max_completion_tokens; small budgets (550–900) ended with
        finish_reason=length and an empty visible answer."""
        from analytics.ai_analyzer import (
            REPORT_MAX_COMPLETION_TOKENS, CHAT_MAX_COMPLETION_TOKENS,
        )

        self.assertGreaterEqual(REPORT_MAX_COMPLETION_TOKENS, 2000)
        self.assertGreaterEqual(CHAT_MAX_COMPLETION_TOKENS, 2000)

        # every call path must use the generous budgets
        self.analyzer.client.chat.completions.create = AsyncMock(
            return_value=_response("ok")
        )
        await self.analyzer.analyze_account(
            "01.09–29.09", {"followers_current": 1}, {"total_posts": 1},
            "best", "stable",
        )
        kwargs = self.analyzer.client.chat.completions.create.await_args.kwargs
        self.assertGreaterEqual(kwargs["max_completion_tokens"], 2000)

    async def test_chat_context_truncation_is_explicit(self):
        """Regression: silent context truncation made the model claim the
        report itself was incomplete."""
        captured = {}

        async def capture(**kwargs):
            captured.update(kwargs)
            return _response("ok")

        self.analyzer.client.chat.completions.create = AsyncMock(side_effect=capture)
        long_context = "строка контекста\n" * 3000  # >> MAX_CONTEXT_CHARS
        await self.analyzer.chat_with_context(long_context, [], "Вопрос")

        combined = "\n".join(
            m["content"] for m in captured["messages"]
        )
        self.assertIn("контекст сокращён", combined)
        self.assertLessEqual(len(combined), 14000 + 2000)

    def test_chat_prompt_forbids_doubting_report_completeness(self):
        from analytics.ai_analyzer import CHAT_SYSTEM_PROMPT

        self.assertIn("а НЕ неполноту самого отчёта", CHAT_SYSTEM_PROMPT)
        self.assertIn("Никогда не делай вывод, что отчёт неполный", CHAT_SYSTEM_PROMPT)
