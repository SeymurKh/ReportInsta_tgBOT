from aiogram.types import (
    ReplyKeyboardMarkup, KeyboardButton,
    InlineKeyboardMarkup, InlineKeyboardButton,
)


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