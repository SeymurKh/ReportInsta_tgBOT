"""Export report workbooks to Google Sheets through an Apps Script web app.

Two modes:
- convert (default): the .xlsx is sent to the web app and imported by Google,
  which keeps the Excel styling (fills, fonts, borders, merges, widths);
- values: cell values are sent and the script rebuilds a simple sheet.

Setup: deploy the Apps Script as a web app (Execute as: Me, access: Anyone),
enable the Advanced Drive service for convert mode, and set
GOOGLE_SHEETS_WEBAPP_URL / GOOGLE_SHEETS_WEBAPP_TOKEN in .env.
"""
import asyncio
import base64
import json
import logging
from datetime import date, datetime
from io import BytesIO

import aiohttp
from openpyxl import load_workbook

from config import settings

logger = logging.getLogger(__name__)

INVALID_SHEET_CHARS = "[]:*?/\\"
MAX_SHEET_TITLE = 90
XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


class GoogleSheetsError(RuntimeError):
    """Any failure while exporting a report to Google Sheets."""


class GoogleSheetsNotConfigured(GoogleSheetsError):
    """The export is disabled or its URL/token is missing."""


def is_configured() -> bool:
    return bool(
        settings.GOOGLE_SHEETS_ENABLED
        and settings.GOOGLE_SHEETS_WEBAPP_URL
        and settings.GOOGLE_SHEETS_WEBAPP_TOKEN
    )


def _cell_value(cell):
    value = cell.value
    if value is None:
        return ""
    if isinstance(value, (datetime, date)):
        return value.strftime("%d.%m.%Y")
    if isinstance(value, (int, float)) and "%" in (cell.number_format or ""):
        return f"{value * 100:.1f}%".replace(".", ",")
    return value


def _sheet_rows(worksheet) -> list[list]:
    rows = []
    for row in worksheet.iter_rows():
        values = [_cell_value(cell) for cell in row]
        while values and values[-1] == "":
            values.pop()
        if values:
            rows.append(values)
    while rows and not any(value != "" for value in rows[-1]):
        rows.pop()
    return rows


def _sheet_name(title: str) -> str:
    name = str(title)
    for char in INVALID_SHEET_CHARS:
        name = name.replace(char, " ")
    return name.strip()[:MAX_SHEET_TITLE] or "Лист"


def _report_title(context: dict) -> str:
    account = str(context.get("account_username") or context.get("account") or "report")
    if not account.startswith("@"):
        account = "@" + account
    period = str(context.get("period") or "").strip()
    return (f"{account} — {period}" if period else account)[:200]


def _build_excel(context: dict, dialogue_history: list | None):
    """Build the same workbook the Excel button sends (lazy import: no cycle)."""
    from bot.helpers import build_excel_from_context

    return build_excel_from_context(context, dialogue_history)


def build_sheets_payload(
    context: dict,
    dialogue_history: list | None = None,
    excel_bytes: bytes | None = None,
) -> dict:
    """Excel workbook bytes -> {token, title, sheets:[{name, rows}]} payload."""
    if excel_bytes is None:
        excel_bytes, _ = _build_excel(context, dialogue_history)
    workbook = load_workbook(BytesIO(excel_bytes), data_only=True)
    try:
        sheets = [
            {"name": _sheet_name(worksheet.title), "rows": _sheet_rows(worksheet)}
            for worksheet in workbook.worksheets
        ]
    finally:
        workbook.close()
    return {
        "token": settings.GOOGLE_SHEETS_WEBAPP_TOKEN,
        "title": _report_title(context),
        "sheets": sheets,
    }


def build_file_payload(excel_bytes: bytes, context: dict, filename: str) -> dict:
    """Import payload: the raw xlsx keeps Google's conversion styling intact."""
    return {
        "token": settings.GOOGLE_SHEETS_WEBAPP_TOKEN,
        "title": _report_title(context),
        "filename": filename or "report.xlsx",
        "mime_type": XLSX_MIME,
        "file_base64": base64.b64encode(excel_bytes).decode("ascii"),
    }


async def _post(session, payload: dict) -> str:
    try:
        async with session.post(settings.GOOGLE_SHEETS_WEBAPP_URL, json=payload) as response:
            status = response.status
            text = await response.text()
    except (aiohttp.ClientError, asyncio.TimeoutError) as error:
        raise GoogleSheetsError(f"request failed: {error}") from error
    if status != 200:
        raise GoogleSheetsError(f"web app returned HTTP {status}")
    try:
        data = json.loads(text)
    except json.JSONDecodeError as error:
        raise GoogleSheetsError("web app returned an invalid response") from error
    if isinstance(data, dict) and data.get("error"):
        raise GoogleSheetsError(f"web app error: {data['error']}")
    url = data.get("url") if isinstance(data, dict) else None
    if not url:
        raise GoogleSheetsError("web app did not return a link")
    return str(url)


async def _send(payload: dict, session=None) -> str:
    if session is not None:
        return await _post(session, payload)
    timeout = aiohttp.ClientTimeout(total=settings.GOOGLE_SHEETS_TIMEOUT)
    async with aiohttp.ClientSession(timeout=timeout) as own_session:
        return await _post(own_session, payload)


async def export_report(
    context: dict,
    dialogue_history: list | None = None,
    *,
    session=None,
) -> str:
    """Create a Google Sheet in the configured folder and return its URL.

    convert mode imports the workbook (keeps the Excel styling); if the web app
    cannot import it (e.g. the Advanced Drive service is off, or an older script
    version is deployed), the export automatically falls back to values.
    """
    if not is_configured():
        raise GoogleSheetsNotConfigured("Google Sheets export is not configured")
    excel_bytes, filename = _build_excel(context, dialogue_history)
    mode = settings.GOOGLE_SHEETS_MODE
    if mode not in {"convert", "values"}:
        mode = "values"
    if mode == "convert":
        try:
            return await _send(
                build_file_payload(excel_bytes, context, filename), session
            )
        except GoogleSheetsError as error:
            logger.warning(
                "Excel import to Google Sheets failed (%s); falling back to values",
                error,
            )
    payload = build_sheets_payload(context, dialogue_history, excel_bytes=excel_bytes)
    return await _send(payload, session)
