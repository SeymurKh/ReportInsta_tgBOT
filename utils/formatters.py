from datetime import date, datetime

MONTHS_RU = [
    "", "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
]


def format_number(n: int | float) -> str:
    return f"{n:,.0f}".replace(",", " ")


def format_pct(n: float) -> str:
    sign = "+" if n >= 0 else ""
    return f"{sign}{n:.1f}%"


def format_date(d: date) -> str:
    return f"{d.day} {MONTHS_RU[d.month]}"


def format_period(d_from: date, d_to: date) -> str:
    if d_from == d_to:
        return format_date(d_from)
    if d_from.month == d_to.month:
        return f"{d_from.day}–{d_to.day} {MONTHS_RU[d_to.month]}"
    return f"{format_date(d_from)} – {format_date(d_to)}"


def format_growth(n: int | float) -> str:
    if n >= 0:
        return f"📈 +{format_number(n)}"
    return f"📉 {format_number(n)}"


def truncate_text(text: str, max_len: int = 4096) -> str:
    if len(text) <= max_len:
        return text
    return text[: max_len - 3] + "..."