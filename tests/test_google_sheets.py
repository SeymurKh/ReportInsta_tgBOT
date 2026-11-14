"""Google Sheets export: payload building and web app transport."""
import base64
import io
import unittest
from datetime import datetime
from unittest.mock import patch

from openpyxl import Workbook

from config import settings
from reports import google_sheets as gs


def _workbook_bytes() -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Сводка"
    sheet["A1"] = "Показатель"
    sheet["B1"] = "Значение"
    sheet["A2"] = "Охват"
    sheet["B2"] = 1234
    sheet["A3"] = "ER"
    sheet["B3"] = 0.124
    sheet["B3"].number_format = "0.0%"
    sheet["A4"] = "Дата"
    sheet["B4"] = datetime(2026, 11, 1)
    second = workbook.create_sheet("Публикации")
    second.append(["Дата", "Тип"])
    second.append(["01.11", "Reels"])
    buffer = io.BytesIO()
    workbook.save(buffer)
    workbook.close()
    return buffer.getvalue()


class GoogleSheetsPayloadTests(unittest.TestCase):
    def test_payload_shape_values_and_sheet_names(self):
        payload = gs.build_sheets_payload(
            {"account_username": "demo", "period": "1–7 ноября"},
            excel_bytes=_workbook_bytes(),
        )
        self.assertEqual(payload["title"], "@demo — 1–7 ноября")
        self.assertEqual(payload["token"], settings.GOOGLE_SHEETS_WEBAPP_TOKEN)
        self.assertEqual(
            [sheet["name"] for sheet in payload["sheets"]],
            ["Сводка", "Публикации"],
        )
        rows = payload["sheets"][0]["rows"]
        self.assertEqual(rows[0], ["Показатель", "Значение"])
        self.assertEqual(rows[1], ["Охват", 1234])
        self.assertEqual(rows[2], ["ER", "12,4%"])
        self.assertEqual(rows[3], ["Дата", "01.11.2026"])

    def test_report_title_keeps_a_single_at_sign(self):
        self.assertEqual(
            gs._report_title({"account_username": "demo", "period": "день"}),
            "@demo — день",
        )
        self.assertEqual(gs._report_title({"account": "@demo"}), "@demo")

    def test_sheet_name_sanitizes_and_truncates(self):
        self.assertEqual(gs._sheet_name("Публикации[]:*?"), "Публикации")
        self.assertEqual(len(gs._sheet_name("x" * 300)), gs.MAX_SHEET_TITLE)

    def test_file_payload_carries_the_workbook_as_base64(self):
        excel_bytes = _workbook_bytes()
        payload = gs.build_file_payload(
            excel_bytes,
            {"account_username": "demo", "period": "1–7 ноября"},
            "report_week.xlsx",
        )
        self.assertEqual(payload["title"], "@demo — 1–7 ноября")
        self.assertEqual(payload["filename"], "report_week.xlsx")
        self.assertEqual(payload["mime_type"], gs.XLSX_MIME)
        decoded = base64.b64decode(payload["file_base64"])
        self.assertEqual(decoded, excel_bytes)
        self.assertEqual(decoded[:2], b"PK")


class FakeResponse:
    def __init__(self, status: int, text: str):
        self.status = status
        self._text = text

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def text(self):
        return self._text


class FakeSession:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def post(self, url, json=None):
        self.calls.append({"url": url, "json": json})
        return self.response


class SequenceSession:
    """Returns queued responses one by one (for the convert -> values fallback)."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def post(self, url, json=None):
        self.calls.append({"url": url, "json": json})
        return self.responses.pop(0)


class GoogleSheetsTransportTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._patches = [
            patch.object(settings, "GOOGLE_SHEETS_ENABLED", True),
            patch.object(settings, "GOOGLE_SHEETS_WEBAPP_URL", "https://example.test/exec"),
            patch.object(settings, "GOOGLE_SHEETS_WEBAPP_TOKEN", "secret"),
            patch.object(settings, "GOOGLE_SHEETS_MODE", "values"),
            patch.object(gs, "_build_excel", return_value=(_workbook_bytes(), "report.xlsx")),
        ]
        for patcher in self._patches:
            patcher.start()

    async def asyncTearDown(self):
        for patcher in reversed(self._patches):
            patcher.stop()

    async def test_export_returns_the_created_sheet_link(self):
        session = FakeSession(FakeResponse(200, '{"url": "https://docs.google.com/spreadsheets/d/1"}'))
        with patch.object(gs, "build_sheets_payload", return_value={
            "token": "secret", "title": "t", "sheets": [],
        }):
            url = await gs.export_report({"account_username": "demo"}, session=session)

        self.assertEqual(url, "https://docs.google.com/spreadsheets/d/1")
        self.assertEqual(session.calls[0]["url"], "https://example.test/exec")
        self.assertEqual(session.calls[0]["json"]["token"], "secret")

    async def test_web_app_error_is_raised(self):
        session = FakeSession(FakeResponse(200, '{"error": "forbidden"}'))
        with patch.object(gs, "build_sheets_payload", return_value={
            "token": "x", "title": "t", "sheets": [],
        }):
            with self.assertRaises(gs.GoogleSheetsError):
                await gs.export_report({}, session=session)

    async def test_disabled_export_raises_not_configured(self):
        with patch.object(settings, "GOOGLE_SHEETS_ENABLED", False):
            with self.assertRaises(gs.GoogleSheetsNotConfigured):
                await gs.export_report({})

    async def test_convert_mode_sends_the_xlsx_payload(self):
        session = FakeSession(FakeResponse(
            200, '{"url": "https://docs.google.com/spreadsheets/d/2"}'
        ))
        with patch.object(settings, "GOOGLE_SHEETS_MODE", "convert"):
            url = await gs.export_report(
                {"account_username": "demo", "period": "week"}, session=session
            )

        self.assertEqual(url, "https://docs.google.com/spreadsheets/d/2")
        payload = session.calls[0]["json"]
        self.assertIn("file_base64", payload)
        self.assertEqual(payload["mime_type"], gs.XLSX_MIME)
        self.assertEqual(base64.b64decode(payload["file_base64"])[:2], b"PK")

    async def test_convert_failure_falls_back_to_values(self):
        session = SequenceSession([
            FakeResponse(200, '{"error": "Advanced Drive Service is not enabled"}'),
            FakeResponse(200, '{"url": "https://docs.google.com/spreadsheets/d/3"}'),
        ])
        with patch.object(settings, "GOOGLE_SHEETS_MODE", "convert"), \
             patch.object(gs, "build_sheets_payload", return_value={
                 "token": "x", "title": "t", "sheets": [],
             }):
            url = await gs.export_report({"account_username": "demo"}, session=session)

        self.assertEqual(url, "https://docs.google.com/spreadsheets/d/3")
        self.assertEqual(len(session.calls), 2)
        self.assertIn("file_base64", session.calls[0]["json"])
        self.assertIn("sheets", session.calls[1]["json"])
