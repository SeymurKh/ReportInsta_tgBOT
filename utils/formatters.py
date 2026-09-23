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


def format_context_for_ai(context: dict) -> str:
    """Format report context for AI dialogue prompt."""
    lines = [
        f"Аккаунт: {context.get('account', '—')}",
        f"Период: {context.get('period', '—')}",
    ]

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
        lines.append(f"Подписчики: {stats.get('followers_end', 0)} (прирост: {stats.get('followers_growth', 0)})")
        lines.append(f"Охват: {stats.get('reach_total', 0)} | Просмотры: {stats.get('views_total', 0)}")
        lines.append(f"Вовлечено: {stats.get('accounts_engaged_total', 0)}")
        lines.append(f"Публикаций: {content.get('total_posts', 0)} (Reels: {content.get('total_reels', 0)}, "
                     f"Видео: {content.get('total_videos', 0)}, "
                     f"Фото: {content.get('total_images', 0)}, Карусели: {content.get('total_carousels', 0)})")
        lines.append(f"Ср. лайки: {content.get('avg_likes', 0)} | Ср. охват: {content.get('avg_reach', 0)}")
        lines.append(f"ER: {content.get('engagement_rate', 0)}%")

        # Daily stats
        daily = context.get("daily_stats", [])
        if daily:
            lines.append("\nДанные по дням:")
            for d in daily[:14]:
                lines.append(f"  {d['date']}: охват {d['reach']}, подписчики {d['followers']:+d}, "
                             f"просмотры {d['views']}, вовлечено {d['accounts_engaged']}")

        # Publications calendar
        publications = context.get("publications", [])
        if publications:
            lines.append("\nПубликации по дням:")
            type_name = {"IMAGE": "Фото", "VIDEO": "Видео", "CAROUSEL_ALBUM": "Карусель", "REELS": "Reels"}
            for day in publications[:12]:
                lines.append(f"  {day['date']}:")
                for p in day["posts"]:
                    mtype = type_name.get(p["type"], p["type"])
                    lines.append(f"    {mtype}: \"{p['caption'][:50]}\" — "
                                 f"❤️{p['likes']} 💬{p['comments']} 💾{p['saved']} 📤{p['shares']} reach:{p['reach']}")

        # Stories of the period
        stories = context.get("stories", {})
        if stories.get("total_stories"):
            lines.append(f"\nСторис: {stories['total_stories']} шт, просмотры {stories['total_views']} "
                         f"(ср. {stories['avg_views']}), охват {stories['total_reach']}, "
                         f"ответы {stories['total_replies']}, репосты {stories['total_shares']}, "
                         f"выходы {stories['exit_rate']}%")
            for s in context.get("stories_list", [])[:20]:
                lines.append(f"  {s['date']}: 👁{s['views']} охват:{s['reach']} "
                             f"💬{s['replies']} 📤{s['shares']}")

        # Best post
        best = context.get("best_post", "")
        if best and best != "нет данных":
            lines.append(f"\nЛучший пост:\n{best}")

    top_posts = context.get("top_posts", [])
    if top_posts:
        lines.append("\nТоп публикаций по взаимодействиям:")
        for post in top_posts[:3]:
            lines.append(
                f"  {post['media_type']}: {post['value']} взаимодействий, "
                f"охват {post['reach']}, \"{post['caption']}\""
            )

    formats = context.get("format_performance", {})
    if formats:
        lines.append("\nФорматы:")
        for media_type, values in formats.items():
            lines.append(
                f"  {media_type}: {values['posts']} публикаций, "
                f"средний охват {values['reach_avg']}, ER {values['engagement_rate']}%"
            )

    quality = context.get("data_quality")
    if quality:
        lines.append(
            f"\nКачество данных: доступно {quality['available_days']} дн. "
            f"из {quality.get('expected_days', 'н/д')}, "
            f"частично загружено {quality['partial_days']} дн."
        )

    # Keep repeated dialogue requests fast and leave the model room to answer.
    return truncate_text("\n".join(lines), 14000)
