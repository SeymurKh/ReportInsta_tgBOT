from aiogram.types import (
    ReplyKeyboardMarkup, KeyboardButton,
    InlineKeyboardMarkup, InlineKeyboardButton,
)
import calendar as calendar_lib
from datetime import date


def main_menu_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📊 Отчёты"), KeyboardButton(text="📊 Сравнение")],
            [KeyboardButton(text="📱 Аккаунты"), KeyboardButton(text="💬 Вопрос нейронке")],
        ],
        resize_keyboard=True,
    )


def accounts_kb(accounts: list) -> InlineKeyboardMarkup:
    buttons = []
    for acc in accounts:
        buttons.append([
            InlineKeyboardButton(
                text=f"@{acc.username}",
                callback_data=f"account_{acc.id}",
            )
        ])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def period_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📅 День", callback_data="period_day")],
            [InlineKeyboardButton(text="📅 Неделя", callback_data="period_week")],
            [InlineKeyboardButton(text="📅 Месяц", callback_data="period_month")],
            [InlineKeyboardButton(text="📅 Произвольный период", callback_data="period_custom")],
            [InlineKeyboardButton(text="◀️ Назад", callback_data="back")],
        ]
    )


def back_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="◀️ Назад", callback_data="back")]]
    )


def comparison_period_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Эта неделя vs Прошлая", callback_data="comp_week")],
            [InlineKeyboardButton(text="Этот месяц vs Прошлый", callback_data="comp_month")],
            [InlineKeyboardButton(text="📅 Произвольные даты", callback_data="comp_custom")],
            [InlineKeyboardButton(text="◀️ Назад", callback_data="back")],
        ]
    )


def comparison_calendar_kb(year: int, month: int, stage: int, today: date) -> InlineKeyboardMarkup:
    """Calendar for selecting four comparison boundary dates sequentially."""
    buttons = [[
        InlineKeyboardButton(text=label, callback_data="cmpcal_noop")
        for label in ("Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс")
    ]]
    for week in calendar_lib.monthcalendar(year, month):
        row = []
        for day in week:
            if not day:
                row.append(InlineKeyboardButton(text=" ", callback_data="cmpcal_noop"))
                continue
            value = date(year, month, day)
            if value > today:
                row.append(InlineKeyboardButton(text="·", callback_data="cmpcal_noop"))
            else:
                row.append(InlineKeyboardButton(
                    text=str(day), callback_data=f"cmpcal_day_{stage}_{year}_{month}_{day}"
                ))
        buttons.append(row)
    buttons.append([
        InlineKeyboardButton(text="‹", callback_data=f"cmpcal_nav_{stage}_{year}_{month}_-1"),
        InlineKeyboardButton(text=f"{month:02d}.{year}", callback_data="cmpcal_noop"),
        InlineKeyboardButton(text="›", callback_data=f"cmpcal_nav_{stage}_{year}_{month}_1"),
    ])
    buttons.append([InlineKeyboardButton(text="Отмена", callback_data="back")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def report_calendar_kb(year: int, month: int, stage: int, today: date) -> InlineKeyboardMarkup:
    """Calendar for selecting report start and end dates."""
    buttons = [[
        InlineKeyboardButton(text=label, callback_data="repcal_noop")
        for label in ("Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс")
    ]]
    for week in calendar_lib.monthcalendar(year, month):
        row = []
        for day in week:
            if not day:
                row.append(InlineKeyboardButton(text=" ", callback_data="repcal_noop"))
                continue
            value = date(year, month, day)
            if value > today:
                row.append(InlineKeyboardButton(text="·", callback_data="repcal_noop"))
            else:
                row.append(InlineKeyboardButton(
                    text=str(day), callback_data=f"repcal_day_{stage}_{year}_{month}_{day}"
                ))
        buttons.append(row)
    buttons.append([
        InlineKeyboardButton(text="‹", callback_data=f"repcal_nav_{stage}_{year}_{month}_-1"),
        InlineKeyboardButton(text=f"{month:02d}.{year}", callback_data="repcal_noop"),
        InlineKeyboardButton(text="›", callback_data=f"repcal_nav_{stage}_{year}_{month}_1"),
    ])
    buttons.append([InlineKeyboardButton(text="Отмена", callback_data="back")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def ai_period_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📅 Неделя", callback_data="ai_period_week")],
            [InlineKeyboardButton(text="📅 Месяц", callback_data="ai_period_month")],
            [InlineKeyboardButton(text="📅 Произвольный", callback_data="ai_period_custom")],
            [InlineKeyboardButton(text="◀️ Назад", callback_data="back")],
        ]
    )


def question_after_report_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📄 Скачать Excel", callback_data="download_excel")],
            [InlineKeyboardButton(text="💬 Вопрос по отчёту", callback_data="question_about_report")],
        ]
    )


def dialogue_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📄 Скачать Excel"), KeyboardButton(text="◀️ В меню")],
        ],
        resize_keyboard=True,
    )
