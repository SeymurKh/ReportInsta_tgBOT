from aiogram.fsm.state import State, StatesGroup


class ReportForm(StatesGroup):
    waiting_custom_dates = State()


class ComparisonForm(StatesGroup):
    waiting_custom_dates = State()


class AIForm(StatesGroup):
    waiting_custom_dates = State()
    dialogue = State()
