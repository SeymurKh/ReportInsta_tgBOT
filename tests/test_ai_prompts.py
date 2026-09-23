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
