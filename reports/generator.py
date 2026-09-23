import logging
from datetime import date, datetime, timedelta

from config import settings
from database import crud
from instagram.client import utc_now_naive
from services.data_sync import sync_account_data
from analytics.calculations import (
    calculate_period_summary, calculate_content_summary, calculate_stories_summary,
    get_best_post, get_best_story, detect_trend,
)
from analytics.ai_analyzer import get_analyzer, AI_UNAVAILABLE_TEXT
from reports.charts import (
    create_followers_chart, create_metrics_chart,
    create_stories_chart, create_comparison_chart,
)
from utils.formatters import format_number, format_pct, format_period, format_growth, MONTHS_RU

logger = logging.getLogger(__name__)

TYPE_EMOJI = {"IMAGE": "📷", "VIDEO": "🎬", "CAROUSEL_ALBUM": "📸", "REELS": "🎬"}
TYPE_NAME = {"IMAGE": "Фото", "VIDEO": "Видео", "CAROUSEL_ALBUM": "Карусель", "REELS": "Reels"}
WEEKDAYS_RU = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]


async def _fetch_and_save_data(account, since_dt: datetime, until_dt: datetime) -> dict:
    """Fetch data through the shared synchronization service.

    Kept as a small compatibility wrapper for the report generator while all
    sync policy lives in services.data_sync.
    """
    return await sync_account_data(account, since_dt, until_dt)


async def _refresh_stories_if_recent(account, until_dt: datetime) -> None:
    """Collect fresh stories when the report period overlaps the story
    insights window. TokenExpiredError propagates to the caller."""
    from instagram.client import TokenExpiredError
    from services.stories_collector import collect_stories_for_account
    cutoff = utc_now_naive() - timedelta(hours=settings.STORIES_INSIGHTS_WINDOW_HOURS)
    if until_dt < cutoff:
        return
    try:
        await collect_stories_for_account(account)
    except TokenExpiredError:
        raise
    except Exception as e:
        logger.warning(f"Stories refresh failed for @{account.username}: {e}")


def _warnings_block(api_delay_dates: list[str], partial: bool) -> str:
    parts = []
    if api_delay_dates:
        parts.append(f"⚠️ Данные за {', '.join(api_delay_dates)} могут быть неполными (задержка Instagram API ~24-48ч)")
    if partial:
        parts.append("⚠️ Часть данных не удалось получить из Instagram API — отчёт может быть неполным")
    return ("\n\n" + "\n".join(parts)) if parts else ""

# ────────────────────── Stories helpers ──────────────────────

def _stories_to_dicts(stories_list: list) -> list[dict]:
    return [{
        "date": s.timestamp.strftime("%d.%m %H:%M"),
        "media_type": s.media_type,
        "permalink": s.permalink,
        "views": s.views,
        "reach": s.reach,
        "replies": s.replies,
        "shares": s.shares,
        "total_interactions": s.total_interactions,
        "profile_activity": s.profile_activity,
        "follows": s.follows,
        "tap_forward": s.tap_forward,
        "tap_back": s.tap_back,
        "tap_exit": s.tap_exit,
        "swipe_forward": s.swipe_forward,
        "is_active": s.is_active,
    } for s in stories_list]


def _stories_section_text(stories_summary: dict, stories_list: list) -> str:
    if not stories_summary.get("total_stories"):
        return (
            "📲 Сторис\n"
            "За период сторис не найдены. Если сторис были — сбор данных "
            "начался недавно: историю сторис Instagram API не отдаёт, "
            "бот собирает их каждые несколько часов с момента запуска."
        )
    lines = [
        f"📲 Сторис ({stories_summary['total_stories']})",
        f"Просмотры: {format_number(stories_summary['total_views'])} (ср. {format_number(stories_summary['avg_views'])}/сторис)",
        f"Охват: {format_number(stories_summary['total_reach'])} | Ответы: {format_number(stories_summary['total_replies'])} | Репосты: {format_number(stories_summary['total_shares'])}",
        f"Переходы в профиль: {format_number(stories_summary['total_profile_activity'])} | Подписки: {format_number(stories_summary['total_follows'])}",
        f"Выходы: {stories_summary['exit_rate']}% | Листали дальше: {format_number(stories_summary['tap_forward_total'])} | Вернулись: {format_number(stories_summary['tap_back_total'])}",
    ]
    best = get_best_story(stories_list)
    if best and best.views > 0:
        day = best.timestamp
        lines.append(
            f"🏆 Лучшая сторис: {day.day} {MONTHS_RU[day.month]} — "
            f"👁 {format_number(best.views)} | Охват {format_number(best.reach)} | 💬 {best.replies}"
        )
    return "\n".join(lines)


def _stories_chart(stories_list: list) -> bytes | None:
    if not stories_list:
        return None
    by_day: dict[date, int] = {}
    for s in stories_list:
        d = s.timestamp.date()
        by_day[d] = by_day.get(d, 0) + s.views
    dates = sorted(by_day.keys())
    return create_stories_chart(dates, [by_day[d] for d in dates])


# ────────────────────── Main report ──────────────────────

async def generate_report(
    account, period: str = "week",
    custom_date_from: date = None, custom_date_to: date = None
) -> dict:
    date_from, date_to = resolve_period(period, custom_date_from, custom_date_to)

    since_dt = datetime(date_from.year, date_from.month, date_from.day)
    until_dt = _day_end(date_to)

    fetch_info = await _fetch_and_save_data(account, since_dt, until_dt)
    await _refresh_stories_if_recent(account, until_dt)

    stats_list = await crud.get_daily_stats(account.id, date_from, date_to)
    posts_list = await crud.get_posts(account.id, since_dt, until_dt)
    stories_list = await crud.get_stories(account.id, since_dt, until_dt)

    stats_summary = calculate_period_summary(stats_list)
    content_summary = calculate_content_summary(posts_list)
    stories_summary = calculate_stories_summary(stories_list)

    best = get_best_post(posts_list)
    best_info = "нет данных"
    if best:
        caption = best.caption[:60] + "..." if len(best.caption) > 60 else best.caption
        caption = caption.replace("\n", " ").strip()
        best_info = (
            f'"{caption}"\n'
            f"❤️ {best.likes} | 💬 {best.comments} | 💾 {best.saved} | 📤 {best.shares} | Охват {format_number(best.reach)}\n"
            f"{best.permalink}"
        )
    trend = detect_trend(stats_list, "followers")
    period_str = format_period(date_from, date_to)

    analyzer = get_analyzer()
    if analyzer:
        ai_analysis = await analyzer.analyze_account(
            period_str, stats_summary, content_summary, best_info, trend,
            stories_summary=stories_summary,
        )
    else:
        ai_analysis = AI_UNAVAILABLE_TEXT

    trend_map = {"growing": "📈 Растущий", "declining": "📉 Снижающийся", "stable": "➡️ Стабильный"}
    views_str = format_number(stats_summary['views_total']) if stats_summary['views_total'] > 0 else "н/д"
    accounts_engaged_str = format_number(stats_summary['accounts_engaged_total']) if stats_summary['accounts_engaged_total'] > 0 else "н/д"

    # Publication calendar — group posts by date
    posts_by_date: dict[str, list] = {}
    for p in posts_list:
        posts_by_date.setdefault(p.timestamp.strftime("%Y-%m-%d"), []).append(p)

    calendar_lines = []
    for day_key in sorted(posts_by_date.keys()):
        day_date = date.fromisoformat(day_key)
        weekday = WEEKDAYS_RU[day_date.weekday()]
        calendar_lines.append(f"\n📅 {day_date.day} {MONTHS_RU[day_date.month]} ({weekday}):")
        for p in posts_by_date[day_key]:
            emoji = TYPE_EMOJI.get(p.media_type, "📄")
            mtype = TYPE_NAME.get(p.media_type, p.media_type)
            caption_short = p.caption[:50].replace("\n", " ").strip()
            if len(p.caption) > 50:
                caption_short += "..."
            calendar_lines.append(f'  {emoji} {mtype} | "{caption_short}"')
            calendar_lines.append(f"  ❤️ {p.likes} | 💬 {p.comments} | 💾 {p.saved} | 📤 {p.shares} | Охват {format_number(p.reach)}")

    publications_text = "\n".join(calendar_lines) if calendar_lines else "Нет публикаций за период"
    stories_text = _stories_section_text(stories_summary, stories_list)

    report_text = f"""📱 @{account.username} — {account.name}
📅 Период: {period_str}

👥 Подписчики
Текущие: {format_number(stats_summary['followers_end'])}
Прирост: {format_growth(stats_summary['followers_growth'])} ({format_pct(stats_summary['followers_growth_pct'])})
(Подписки − отписки; отдельной метрики отписок в Instagram API нет)
Тренд: {trend_map.get(trend, trend)}

📈 Активность
Охват: {format_number(stats_summary['reach_total'])}
Просмотры: {views_str}
Вовлечено: {accounts_engaged_str}
Средний охват/день: {format_number(stats_summary['reach_avg_daily'])}

📹 Контент ({content_summary['total_posts']} публикаций)
Reels: {content_summary['total_reels']} | Видео: {content_summary['total_videos']} | Фото: {content_summary['total_images']} | Карусели: {content_summary['total_carousels']}
Средние лайки: {content_summary['avg_likes']} | Средний охват: {content_summary['avg_reach']}
Вовлечённость (ER): {content_summary['engagement_rate']}%

{stories_text}

📋 Публикации
{publications_text}

🏆 Лучший пост
{best_info}

🤖 AI-анализ
{ai_analysis}"""

    report_text += _warnings_block(fetch_info["api_delay_dates"], fetch_info["partial"])


    charts = {}
    if stats_list:
        dates = [s.date for s in stats_list]
        charts["followers"] = create_followers_chart(dates, [s.follower_count for s in stats_list])
        charts["reach"] = create_metrics_chart(dates, [s.reach for s in stats_list])
    stories_chart = _stories_chart(stories_list)
    if stories_chart:
        charts["stories"] = stories_chart

    # Context for AI dialogue / Excel
    daily_stats_data = [{
        "date": s.date.strftime("%d.%m"),
        "reach": s.reach,
        "followers": s.follower_count,
        "views": s.views,
        "accounts_engaged": s.accounts_engaged,
    } for s in stats_list]

    publications_data = []
    for day_key in sorted(posts_by_date.keys()):
        day_posts = [{
            "type": p.media_type,
            "caption": p.caption[:80],
            "likes": p.likes,
            "comments": p.comments,
            "saved": p.saved,
            "shares": p.shares,
            "reach": p.reach,
            "views": p.views,
        } for p in posts_by_date[day_key]]
        publications_data.append({"date": day_key, "posts": day_posts})

    context = {
        "account": f"@{account.username} — {account.name}",
        "account_username": account.username,
        "account_name": account.name,
        "period": period_str,
        "type": "report",
        "stats": stats_summary,
        "content": content_summary,
        "stories": stories_summary,
        "stories_list": _stories_to_dicts(stories_list),
        "best_post": best_info,
        "trend": trend,
        "daily_stats": daily_stats_data,
        "publications": publications_data,
        "ai_analysis": ai_analysis,
    }

    return {"text": report_text, "charts": charts, "context": context}


# ────────────────────── Comparison report ──────────────────────

async def generate_comparison_periods_report(
    account,
    period1_from: date, period1_to: date,
    period2_from: date, period2_to: date,
) -> dict:
    """Compare two periods."""
    p1_since_dt = datetime(period1_from.year, period1_from.month, period1_from.day)
    p1_until_dt = _day_end(period1_to)
    p2_since_dt = datetime(period2_from.year, period2_from.month, period2_from.day)
    p2_until_dt = _day_end(period2_to)

    f1 = await _fetch_and_save_data(account, p1_since_dt, p1_until_dt)
    f2 = await _fetch_and_save_data(account, p2_since_dt, p2_until_dt)
    api_delay_dates = sorted(set(f1["api_delay_dates"] + f2["api_delay_dates"]))
    partial = f1["partial"] or f2["partial"]
    await _refresh_stories_if_recent(account, max(p1_until_dt, p2_until_dt))

    p1_stats = await crud.get_daily_stats(account.id, period1_from, period1_to)
    p2_stats = await crud.get_daily_stats(account.id, period2_from, period2_to)
    p1_posts = await crud.get_posts(account.id, p1_since_dt, p1_until_dt)
    p2_posts = await crud.get_posts(account.id, p2_since_dt, p2_until_dt)
    p1_stories = await crud.get_stories(account.id, p1_since_dt, p1_until_dt)
    p2_stories = await crud.get_stories(account.id, p2_since_dt, p2_until_dt)

    s1 = calculate_period_summary(p1_stats)
    s2 = calculate_period_summary(p2_stats)
    c1 = calculate_content_summary(p1_posts)
    c2 = calculate_content_summary(p2_posts)
    st1 = calculate_stories_summary(p1_stories)
    st2 = calculate_stories_summary(p2_stories)

    p1_str = format_period(period1_from, period1_to)
    p2_str = format_period(period2_from, period2_to)

    def diff(v1, v2):
        d = v2 - v1
        pct = round(d / v1 * 100, 1) if v1 > 0 else None
        return d, pct

    def diff_str(label, v1, v2):
        d, pct = diff(v1, v2)
        sign = "+" if d >= 0 else ""
        pct_str = f" ({sign}{pct:.1f}%)" if pct is not None else ""
        emoji = "📈" if d > 0 else ("📉" if d < 0 else "➡️")
        if isinstance(v1, float):
            return f"{emoji} {label}: {v1:.1f} → {v2:.1f} ({sign}{d:.1f}{pct_str})"
        return f"{emoji} {label}: {format_number(v1)} → {format_number(v2)} ({sign}{format_number(d)}{pct_str})"

    lines = [
        f"📊 Сравнение периодов: {p1_str} vs {p2_str}\n",
        diff_str("Прирост подписчиков", s1["followers_growth"], s2["followers_growth"]),
        "",
        diff_str("Охват (итого)", s1["reach_total"], s2["reach_total"]),
        diff_str("Просмотры (итого)", s1["views_total"], s2["views_total"]),
        diff_str("Вовлечено аккаунтов", s1["accounts_engaged_total"], s2["accounts_engaged_total"]),
        "",
        diff_str("Публикаций", c1["total_posts"], c2["total_posts"]),
        diff_str("Лайки (итого)", c1["total_likes"], c2["total_likes"]),
        diff_str("Комменты (итого)", c1["total_comments"], c2["total_comments"]),
        diff_str("Сохранения (итого)", c1["total_saves"], c2["total_saves"]),
        diff_str("Репосты (итого)", c1["total_shares"], c2["total_shares"]),
        "",
        diff_str("Вовлечённость (ER%)", c1["engagement_rate"], c2["engagement_rate"]),
        "",
        diff_str("Сторис", st1["total_stories"], st2["total_stories"]),
        diff_str("Просмотры сторис", st1["total_views"], st2["total_views"]),
        diff_str("Ответы на сторис", st1["total_replies"], st2["total_replies"]),
    ]

    analyzer = get_analyzer()
    if analyzer:
        comparison_data = (
            f"Период 1 ({p1_str}): прирост подписчиков {s1['followers_growth']}, охват {s1['reach_total']}, "
            f"просмотры {s1['views_total']}, вовлечено {s1['accounts_engaged_total']}, "
            f"лайки {c1['total_likes']}, ER {c1['engagement_rate']}%, постов {c1['total_posts']}, "
            f"сторис {st1['total_stories']} (просмотры {st1['total_views']})\n"
            f"Период 2 ({p2_str}): прирост подписчиков {s2['followers_growth']}, охват {s2['reach_total']}, "
            f"просмотры {s2['views_total']}, вовлечено {s2['accounts_engaged_total']}, "
            f"лайки {c2['total_likes']}, ER {c2['engagement_rate']}%, постов {c2['total_posts']}, "
            f"сторис {st2['total_stories']} (просмотры {st2['total_views']})"
        )
        ai_analysis = await analyzer.analyze_comparison(comparison_data)
    else:
        ai_analysis = AI_UNAVAILABLE_TEXT

    lines.append(f"\n🤖 AI-сравнение\n{ai_analysis}")
    text = "\n".join(lines) + _warnings_block(api_delay_dates, partial)

    charts = {"comparison": create_comparison_chart(
        ["Подписчики", "Охват", "Просмотры", "Посты", "Сторис"],
        [s1["followers_growth"], s1["reach_total"], s1["views_total"], c1["total_posts"], st1["total_stories"]],
        [s2["followers_growth"], s2["reach_total"], s2["views_total"], c2["total_posts"], st2["total_stories"]],
        p1_str, p2_str,
    )}

    context = {
        "account": f"@{account.username} — {account.name}",
        "account_username": account.username,
        "account_name": account.name,
        "period": f"{p1_str} vs {p2_str}",
        "type": "comparison",
        "period1": {"name": p1_str, "stats": s1, "content": c1, "stories": st1},
        "period2": {"name": p2_str, "stats": s2, "content": c2, "stories": st2},
        "ai_analysis": ai_analysis,
    }

    return {"text": text, "charts": charts, "context": context}


# ────────────────────── Daily digest (for scheduler) ──────────────────────

async def generate_daily_digest(account) -> str:
    """Compact one-account summary for yesterday. Used by the daily auto-report."""
    yesterday = utc_now_naive().date() - timedelta(days=1)
    since_dt = datetime(yesterday.year, yesterday.month, yesterday.day)
    until_dt = _day_end(yesterday)

    await _fetch_and_save_data(account, since_dt, until_dt)
    await _refresh_stories_if_recent(account, until_dt)

    stats_list = await crud.get_daily_stats(account.id, yesterday, yesterday)
    posts_list = await crud.get_posts(account.id, since_dt, until_dt)
    stories_list = await crud.get_stories(account.id, since_dt, until_dt)

    summary = calculate_period_summary(stats_list)
    stories_summary = calculate_stories_summary(stories_list)

    return (
        f"@{account.username}: 👥 {format_growth(summary['followers_growth'])} "
        f"(всего {format_number(summary['followers_end'])}), "
        f"охват {format_number(summary['reach_total'])}, "
        f"просмотры {format_number(summary['views_total'])}, "
        f"постов: {len(posts_list)}, "
        f"сторис: {stories_summary['total_stories']} "
        f"(👁 {format_number(stories_summary['total_views'])})"
    )
