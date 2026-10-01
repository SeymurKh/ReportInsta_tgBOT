from datetime import date, datetime

MONTHS_RU = [
    "", "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
]


def format_number(n: int | float) -> str:
    return f"{n:,.0f}".replace(",", " ")


def format_pct(n: float | None) -> str:
    if n is None:
        return "н/д"
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


def format_growth(n: int | float | None) -> str:
    if n is None:
        return "н/д"
    if n >= 0:
        return f"📈 +{format_number(n)}"
    return f"📉 {format_number(n)}"


AI_CONTEXT_MAX_CHARS = 14000
_OMISSION_TEMPLATE = "…[опущено {count} из {total} {noun} — полные данные в отчёте и Excel]"
_CONTEXT_CUT_MARKER = "\n…[контекст сокращён — полные данные в отчёте и Excel]"


def _truncate_at_line_boundary(text: str, limit: int, marker: str) -> str:
    """Cut at a line boundary and always tell the reader what happened."""
    if len(text) <= limit:
        return text
    budget = limit - len(marker)
    cut = text[:budget]
    newline = cut.rfind("\n")
    if newline > budget // 2:
        cut = cut[:newline]
    return cut + marker


def _fit_detail_groups(core_lines: list, tail_lines: list, groups: list, limit: int) -> str:
    """Assemble the AI context within `limit`, dropping detail rows from the
    least important group first. Every drop is replaced by an explicit marker —
    the model must never mistake context trimming for an incomplete report.

    Each group is (header_lines, rows, total_items, noun); headers are always
    shown, rows are counted so markers can state «опущено N из M».
    """
    kept = [len(rows) for _header, rows, _total, _noun in groups]

    def render() -> str:
        parts = list(core_lines)
        for (header, rows, total, noun), k in zip(groups, kept):
            if header:
                parts.extend(header)
            parts.extend(rows[:k])
            omitted = total - k
            if omitted > 0:
                parts.append(_OMISSION_TEMPLATE.format(count=omitted, total=total, noun=noun))
        parts.extend(tail_lines)
        return "\n".join(parts)

    text = render()
    for i in range(len(groups) - 1, -1, -1):
        while kept[i] > 0 and len(text) > limit:
            kept[i] -= 1
            text = render()
    if len(text) > limit:
        return _truncate_at_line_boundary(render(), limit, _CONTEXT_CUT_MARKER)
    return text


def format_context_for_ai(context: dict) -> str:
    """Format report context for AI dialogue prompt."""
    lines = [
        "Контекст для анализа: данные отчёта полные — все дни, публикации и сторис "
        "периода перечислены ниже, если строка не заменена маркером «опущено…». "
        "Маркер «опущено…» означает сокращение контекста диалога, а не неполноту "
        "отчёта: пользователь видит полный отчёт.",
        f"Аккаунт: {context.get('account', '—')}",
        f"Период: {context.get('period', '—')}",
    ]
    groups: list = []
    tail: list = []

    # Check if this is a comparison report
    if context.get("type") == "comparison":
        p1 = context.get("period1", {})
        p2 = context.get("period2", {})
        s1 = p1.get("stats", {})
        s2 = p2.get("stats", {})
        c1 = p1.get("content", {})
        c2 = p2.get("content", {})
        lines.append(f"\nПериод 1 ({p1.get('name', '')}):")
        lines.append(f"  Подписчики: {s1.get('followers_end', 0)}, Охват: {s1.get('reach_total', 0)}, "
                     f"Просмотры: {s1.get('views_total', 0)}, ER: {c1.get('engagement_rate', 0)}%")
        lines.append(f"\nПериод 2 ({p2.get('name', '')}):")
        lines.append(f"  Подписчики: {s2.get('followers_end', 0)}, Охват: {s2.get('reach_total', 0)}, "
                     f"Просмотры: {s2.get('views_total', 0)}, ER: {c2.get('engagement_rate', 0)}%")
        period_days = context.get("period_days", {})
        if period_days:
            lines.append(
                f"\nДоступность: период 1 {period_days.get('available1', 0)} "
                f"из {period_days.get('period1', 0)} дней; период 2 "
                f"{period_days.get('available2', 0)} из {period_days.get('period2', 0)} дней"
            )

        # Stories per period (if present)
        st1 = p1.get("stories", {})
        st2 = p2.get("stories", {})
        if st1 or st2:
            lines.append(f"\nСторис период 1: {st1.get('total_stories', 0)} шт, "
                         f"просмотры {st1.get('total_views', 0)}, ответы {st1.get('total_replies', 0)}")
            lines.append(f"Сторис период 2: {st2.get('total_stories', 0)} шт, "
                         f"просмотры {st2.get('total_views', 0)}, ответы {st2.get('total_replies', 0)}")
        comparison_formats = context.get("comparison_formats", {})
        if comparison_formats:
            lines.append("\nФорматы по периодам:")
            for media_type in sorted(
                set(comparison_formats.get("period1", {}))
                | set(comparison_formats.get("period2", {}))
            ):
                left = comparison_formats.get("period1", {}).get(media_type, {})
                right = comparison_formats.get("period2", {}).get(media_type, {})
                lines.append(
                    f"  {media_type}: P1 ER {left.get('engagement_rate', 0)}%, "
                    f"P2 ER {right.get('engagement_rate', 0)}%, "
                    f"средний охват {left.get('reach_avg', 0)} → {right.get('reach_avg', 0)}"
                )
        comparison_top = context.get("comparison_top_posts", {})
        if comparison_top:
            lines.append("\nЛучшие публикации по периодам:")
            for label, key in (("P1", "period1"), ("P2", "period2")):
                best = (comparison_top.get(key) or [{}])[0]
                lines.append(
                    f"  {label}: {best.get('media_type', '—')}, "
                    f"{best.get('value', 0)} взаимодействий, \"{best.get('caption', 'нет данных')}\""
                )
    else:
        # Regular report
        stats = context.get("stats", {})
        content = context.get("content", {})
        followers_now = stats.get("followers_current", stats.get("followers_end"))
        lines.append(
            f"Подписчики сейчас: {followers_now if followers_now is not None else 'н/д'} "
            f"(прирост за период: {stats.get('followers_growth') if stats.get('followers_growth') is not None else 'н/д'})"
        )
        lines.append(f"Охват: {stats.get('reach_total', 0)} | Просмотры: {stats.get('views_total', 0)}")
        lines.append(f"Вовлечено: {stats.get('accounts_engaged_total', 0)}")
        lines.append(f"Публикаций: {content.get('total_posts', 0)} (Reels: {content.get('total_reels', 0)}, "
                     f"Видео: {content.get('total_videos', 0)}, "
                     f"Фото: {content.get('total_images', 0)}, Карусели: {content.get('total_carousels', 0)})")
        lines.append(f"Ср. лайки: {content.get('avg_likes', 0)} | Ср. охват: {content.get('avg_reach', 0)}")
        lines.append(f"ER: {content.get('engagement_rate', 0)}%")
        if content.get("partial_insights_posts") or content.get("legacy_unknown_insights"):
            lines.append(
                f"Полнота Insights публикаций: неполных {content.get('partial_insights_posts', 0)}, "
                f"собраны до внедрения учёта полноты {content.get('legacy_unknown_insights', 0)}"
            )
    quality = context.get("data_quality")
    if quality:
        lines.append(
            f"\nКачество данных: доступно {quality['available_days']} дн. "
            f"из {quality.get('expected_days', 'н/д')}, "
            f"частично загружено {quality['partial_days']} дн."
        )
        gaps = [
            f"{metric} — {days}"
            for metric, days in quality.get("metric_missing_days", {}).items()
            if days
        ]
        if gaps:
            lines.append("Отсутствующие метрики по дням: " + ", ".join(gaps))
        if quality.get("legacy_unknown_days"):
            lines.append(
                f"Для {quality['legacy_unknown_days']} старых дней API-полнота неизвестна."
            )

    # Daily stats — full detail, trimmed only with explicit markers
    daily = context.get("daily_stats", [])
    if daily:
        daily_rows = []
        for d in daily:
            followers = d.get("followers")
            followers_text = f"{followers:+d}" if followers is not None else "н/д"
            daily_rows.append(
                f"  {d['date']}: охват {d['reach']}, подписчики {followers_text}, "
                f"просмотры {d['views']}, вовлечено {d['accounts_engaged']}"
            )
        groups.append((["\nДанные по дням:"], daily_rows, len(daily_rows), "дней"))

    # Publications calendar — full detail, trimmed only with explicit markers
    publications = context.get("publications", [])
    if publications:
        pub_rows = []
        type_name = {"IMAGE": "Фото", "VIDEO": "Видео", "CAROUSEL_ALBUM": "Карусель", "REELS": "Reels"}
        for day in publications:
            for p in day["posts"]:
                mtype = type_name.get(p["type"], p["type"])
                pub_rows.append(
                    f"  {day['date']}: {mtype}: \"{p['caption'][:50]}\" — "
                    f"❤️{p['likes']} 💬{p['comments']} 💾{p['saved']} 📤{p['shares']} reach:{p['reach']}"
                )
        groups.append((["\nПубликации:"], pub_rows, len(pub_rows), "публикаций"))

    # Stories of the period
    stories = context.get("stories", {})
    if stories.get("total_stories"):
        story_header = [f"\nСторис: {stories['total_stories']} шт, просмотры {stories['total_views']} "
                        f"(ср. {stories['avg_views']}), охват {stories['total_reach']}, "
                        f"ответы {stories['total_replies']}, репосты {stories['total_shares']}, "
                        f"выходы {stories['exit_rate']}%"]
        if stories.get("partial_insights_stories") or stories.get("legacy_unknown_insights"):
            story_header.append(
                f"Полнота Insights сторис: неполных — {stories.get('partial_insights_stories', 0)}; "
                f"собраны до внедрения учёта полноты — {stories.get('legacy_unknown_insights', 0)} "
                f"(данные этих сторис реальные, но полнота их метрик документально не подтверждена)"
            )
        story_rows = [
            f"  {s['date']}: 👁{s['views']} охват:{s['reach']} "
            f"💬{s['replies']} 📤{s['shares']}"
            for s in context.get("stories_list", [])
        ]
        groups.append((story_header, story_rows, len(story_rows), "сторис"))

    # Best post
    best = context.get("best_post", "")
    if best and best != "нет данных":
        tail.append(f"\nЛучший пост:\n{best}")

    top_posts = context.get("top_posts", [])
    if top_posts:
        tail.append("\nТоп публикаций по взаимодействиям:")
        for post in top_posts[:3]:
            tail.append(
                f"  {post['media_type']}: {post['value']} взаимодействий, "
                f"охват {post['reach']}, \"{post['caption']}\""
            )

    formats = context.get("format_performance", {})
    if formats:
        tail.append("\nФорматы:")
        for media_type, values in formats.items():
            tail.append(
                f"  {media_type}: {values['posts']} публикаций, "
                f"средний охват {values['reach_avg']}, ER {values['engagement_rate']}%"
            )

    # Keep repeated dialogue requests fast and leave the model room to answer,
    # but never trim silently: _fit_detail_groups marks every omission.
    return _fit_detail_groups(lines, tail, groups, AI_CONTEXT_MAX_CHARS)
