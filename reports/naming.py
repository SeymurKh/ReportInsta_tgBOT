"""Unified metric naming and tabular rendering for all report surfaces.

Every metric name answers three questions at once: WHAT is measured, WHERE
(account / publications / stories) and HOW (unique accounts vs summed events).
The Telegram report, comparison, digest, Excel and the AI context all use this
one glossary so the same number is never called by two different names.

Rendering: metric blocks are monospace (wrapped in ``` fences by the sender),
labels are left-aligned and values right-aligned into fixed columns, which
makes the report read like a table. Emoji live only in section titles so they
cannot break the alignment.
"""

FENCE = "```"

DIVIDER_WIDTH = 26
LABEL_WIDTH = 25
VALUE_WIDTH = 15

CMP_LABEL_WIDTH = 24
CMP_VALUE_WIDTH = 8
CMP_DELTA_WIDTH = 12

# ── Section titles ──
SEC_FOLLOWERS = "👥 ПОДПИСЧИКИ"
SEC_ACTIVITY = "📈 АКТИВНОСТЬ АККАУНТА (API за период)"
SEC_CONTENT = "📹 ПУБЛИКАЦИИ"
SEC_STORIES = "📲 СТОРИС"
SEC_COMPARISON = "📊 СРАВНЕНИЕ ПЕРИОДОВ"

# ── Followers ──
L_FOLLOWERS_NOW = "Сейчас"
L_FOLLOWERS_GROWTH = "Прирост за период"
L_TREND = "Тренд (7 дн. к 7 дн.)"

# ── Account activity (Instagram API period totals) ──
L_REACH = "Охват (уникальные)"
L_VIEWS = "Просмотры (с повторами)"
L_ENGAGED = "Вовлечённые (уникальные)"
L_INTERACTIONS = "Взаимодействия (все)"
L_PROFILE_VIEWS = "Просмотры профиля"
L_ACCOUNT_ER = "ER аккаунта"
L_REACH_DAILY = "Средний охват в день"

# ── Publications ──
L_POSTS = "Публикации"
L_POST_LIKES = "Лайки на публикацию"
L_POST_REACH = "Охват на публикацию"
L_POST_ER = "ER публикаций"

# ── Comparison extras ──
L_VIEWS_DAILY = "Просмотры в день"
L_ENGAGED_DAILY = "Вовлечённые в день"
L_POST_LIKES_TOTAL = "Лайки публикаций"
L_POST_COMMENTS_TOTAL = "Комментарии публикаций"
L_POST_SAVES_TOTAL = "Сохранения публикаций"
L_POST_SHARES_TOTAL = "Репосты публикаций"
L_STORIES_COUNT = "Количество сторис"

# ── Stories (hybrid: short labels — the block is self-evident) ──
L_STORIES_VIEWS = "Просмотры (с повторами)"
L_STORIES_REACH = "Охват (сумма по сторис)"
L_STORIES_REPLIES = "Ответы"
L_STORIES_SHARES = "Репосты"
L_STORIES_PROFILE = "Переходы в профиль"
L_STORIES_FOLLOWS = "Подписки со сторис"
L_STORIES_EXIT = "Доля выходов"
L_STORIES_FORWARD = "Пролистнули вперёд"
L_STORIES_BACK = "Вернулись назад"

# ── Plain values (no emoji inside table cells) ──
TREND_PLAIN = {
    "growing": "Растущий",
    "declining": "Снижающийся",
    "stable": "Стабильный",
    "unknown": "н/д",
}

# ── Footnotes: compact legend + self-explanatory reconciliations ──
LEGEND_TITLE = "📌 КАК ЧИТАТЬ"
LEGEND_ITEMS = [
    "• Охват и вовлечённые — уникальные аккаунты; просмотры — все показы, с повторами",
    "• Прирост % — от базы на начало периода; прирост — по дневным данным",
    "• ❤/💬/💾/📤 — метрики аккаунта; у публикаций — свои суммы",
]
NOTE_ACCOUNT_GAP = (
    "ℹ️ Сверка взаимодействий аккаунта: {components} (❤💬💾📤) {sign}{gap} "
    "({explanation}) = {total} — сходится."
)
NOTE_POST_GAP = (
    "ℹ️ Сверка взаимодействий публикаций{label}: {components} (❤💬💾📤) {sign}{gap} "
    "({explanation}) = {total} — сходится. ER рассчитан по total_interactions."
)
GAP_EXTRA = "другие действия Instagram"
GAP_RECOUNT = "пересчёт Instagram"


def kv_row(label: str, value: str) -> str:
    """One metric as a mobile-safe stacked row: label line + indented value.

    Fixed-width columns only look aligned on wide screens; on phones every
    long line wraps and the table turns into mush. Stacked rows wrap
    gracefully on any screen width.
    """
    return f"{label}\n  {value}"


def table_section(title: str, rows: list[tuple[str, str]]) -> str:
    """A titled metric block (title + divider + stacked rows)."""
    lines = [title, "─" * DIVIDER_WIDTH]
    lines.extend(kv_row(label, value) for label, value in rows)
    return "\n".join(lines)


def mono_block(sections: list[str]) -> str:
    """Wrap sections into one fenced monospace block for the sender."""
    return FENCE + "\n" + "\n\n".join(sections) + "\n" + FENCE


def cmp_row(label: str, p1: str, p2: str, delta: str) -> str:
    """One comparison metric as a mobile-safe stacked row."""
    return f"{label}\n  П1  {p1} → П2  {p2}\n  Δ  {delta}"