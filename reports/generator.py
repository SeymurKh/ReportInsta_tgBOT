import logging
from datetime import date, datetime, timedelta, timezone

from config import settings
from database import crud
from instagram.client import InstagramClient
from analytics.calculations import (
    calculate_period_summary, calculate_content_summary,
    get_best_post, detect_trend,
)
from analytics.ai_analyzer import AIAnalyzer
from reports.charts import (
    create_followers_chart, create_metrics_chart, create_engagement_chart,
)
from utils.formatters import format_number, format_pct, format_date, format_period, format_growth, MONTHS_RU

logger = logging.getLogger(__name__)
ai = AIAnalyzer()


async def _ensure_data(account, since_dt: datetime, until_dt: datetime, since_unix: int, until_unix: int):
    """Ensure we have data in DB. Fetch from API if needed."""
    latest = await crud.get_latest_stats(account.id)
    today = date.today()

    needs_fetch = latest is None or latest.date < today

    if needs_fetch:
        client = InstagramClient(account.instagram_user_id, account.access_token)
        try:
            snapshot = await client.collect_full_snapshot(since_unix, until_unix)
            user_info = snapshot["user_info"]

            for day_str, metrics in snapshot["insights"].items():
                day_date = date.fromisoformat(day_str)
                await crud.save_daily_stats(
                    account_id=account.id,
                    stats_date=day_date,
                    followers=metrics.get("followers_count", user_info.get("followers_count", 0)),
                    following=user_info.get("follows_count", 0),
                    media_count=user_info.get("media_count", 0),
                    reach=metrics.get("reach", 0),
                    follower_count=metrics.get("follower_count", 0),
                    views=metrics.get("views", 0),
                    accounts_engaged=metrics.get("accounts_engaged", 0),
                )

            posts_data = await client.collect_posts_with_insights(since_dt, until_dt)
            for p in posts_data:
                p["account_id"] = account.id
            await crud.save_posts(posts_data)
        finally:
            await client.close()


async def generate_report(
    account, period: str = "week",
    custom_date_from: date = None, custom_date_to: date = None
) -> dict:
    today = date.today()

    if custom_date_from and custom_date_to:
        date_from = custom_date_from
        date_to = custom_date_to
    else:
        days = settings.PERIODS.get(period, 7)
        date_from = today - timedelta(days=days)
        date_to = today - timedelta(days=1)

    since_dt = datetime(date_from.year, date_from.month, date_from.day, tzinfo=timezone.utc)
    until_dt = datetime(date_to.year, date_to.month, date_to.day, 23, 59, 59, tzinfo=timezone.utc)
    since_unix = int(since_dt.timestamp())
    until_unix = int(until_dt.timestamp())

    await _ensure_data(account, since_dt, until_dt, since_unix, until_unix)

    stats_list = await crud.get_daily_stats(account.id, date_from, date_to)
    posts_list = await crud.get_posts(account.id, since_dt, until_dt)

    stats_summary = calculate_period_summary(stats_list)
    content_summary = calculate_content_summary(posts_list)

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

    ai_analysis = await ai.analyze_account(
        period_str, stats_summary, content_summary, best_info, trend
    )

    trend_map = {"growing": "📈 Растущий", "declining": "📉 Снижающийся", "stable": "➡️ Стабильный"}
    views_str = format_number(stats_summary['views_total']) if stats_summary['views_total'] > 0 else "н/д"
    accounts_engaged_str = format_number(stats_summary['accounts_engaged_total']) if stats_summary['accounts_engaged_total'] > 0 else "н/д"

    # Build publication calendar - group posts by date
    type_emoji = {"IMAGE": "📷", "VIDEO": "🎬", "CAROUSEL_ALBUM": "📸", "REELS": "🎬"}
    type_name = {"IMAGE": "Фото", "VIDEO": "Видео", "CAROUSEL_ALBUM": "Карусель", "REELS": "Reels"}
    weekdays_ru = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]

    posts_by_date = {}
    for p in posts_list:
        day_key = p.timestamp.strftime("%Y-%m-%d")
        if day_key not in posts_by_date:
            posts_by_date[day_key] = []
        posts_by_date[day_key].append(p)

    calendar_lines = []
    for day_key in sorted(posts_by_date.keys()):
        day_date = date.fromisoformat(day_key)
        weekday = weekdays_ru[day_date.weekday()]
        calendar_lines.append(f"\n📅 {day_date.day} {MONTHS_RU[day_date.month]} ({weekday}):")
        for p in posts_by_date[day_key]:
            emoji = type_emoji.get(p.media_type, "📄")
            mtype = type_name.get(p.media_type, p.media_type)
            caption_short = p.caption[:50].replace("\n", " ").strip()
            if len(p.caption) > 50:
                caption_short += "..."
            calendar_lines.append(f"  {emoji} {mtype} | \"{caption_short}\"")
            calendar_lines.append(f"  ❤️ {p.likes} | 💬 {p.comments} | 💾 {p.saved} | 📤 {p.shares} | Охват {format_number(p.reach)}")

    publications_text = "\n".join(calendar_lines) if calendar_lines else "Нет публикаций за период"

    report_text = f"""📱 @{account.username} — {account.name}
📅 Период: {period_str}

👥 Подписчики
Текущие: {format_number(stats_summary['followers_end'])}
Прирост: {format_growth(stats_summary['followers_growth'])} ({format_pct(stats_summary['followers_growth_pct'])})
Тренд: {trend_map.get(trend, trend)}

📈 Активность
Охват: {format_number(stats_summary['reach_total'])}
Просмотры: {views_str}
Аккаунтов вовлечено: {accounts_engaged_str}
Средний охват/день: {format_number(stats_summary['reach_avg_daily'])}

📹 Контент ({content_summary['total_posts']} публикаций)
Reels: {content_summary['total_reels']} | Фото: {content_summary['total_images']} | Карусели: {content_summary['total_carousels']}
Средние лайки: {content_summary['avg_likes']} | Средний охват: {content_summary['avg_reach']}
Вовлечённость (ER): {content_summary['engagement_rate']}%

📋 Публикации
{publications_text}

🏆 Лучший пост
{best_info}

🤖 AI-анализ
{ai_analysis}"""

    charts = {}
    if stats_list:
        dates = [s.date for s in stats_list]
        # Use daily follower_count (change) instead of total followers
        follower_growth_vals = [s.follower_count for s in stats_list]
        reach_vals = [s.reach for s in stats_list]

        charts["followers"] = create_followers_chart(dates, follower_growth_vals)
        charts["reach"] = create_metrics_chart(dates, reach_vals)

    # Build context for AI dialogue
    daily_stats_data = []
    for s in stats_list:
        daily_stats_data.append({
            "date": s.date.strftime("%d.%m"),
            "reach": s.reach,
            "followers": s.follower_count,
            "views": s.views,
            "accounts_engaged": s.accounts_engaged,
        })

    # Build publications data for context
    publications_data = []
    for day_key in sorted(posts_by_date.keys()):
        day_posts = []
        for p in posts_by_date[day_key]:
            day_posts.append({
                "type": p.media_type,
                "caption": p.caption[:80],
                "likes": p.likes,
                "comments": p.comments,
                "saved": p.saved,
                "shares": p.shares,
                "reach": p.reach,
                "views": p.views,
            })
        publications_data.append({"date": day_key, "posts": day_posts})

    context = {
        "account": f"@{account.username} — {account.name}",
        "period": period_str,
        "stats": stats_summary,
        "content": content_summary,
        "best_post": best_info,
        "trend": trend,
        "daily_stats": daily_stats_data,
        "publications": publications_data,
        "ai_analysis": ai_analysis,
    }

    return {"text": report_text, "charts": charts, "context": context}


async def generate_comparison_periods_report(
    account,
    period1_from: date, period1_to: date,
    period2_from: date, period2_to: date,
) -> dict:
    """Compare two custom periods."""
    # Ensure data for both periods
    p1_since_dt = datetime(period1_from.year, period1_from.month, period1_from.day, tzinfo=timezone.utc)
    p1_until_dt = datetime(period1_to.year, period1_to.month, period1_to.day, 23, 59, 59, tzinfo=timezone.utc)
    p2_since_dt = datetime(period2_from.year, period2_from.month, period2_from.day, tzinfo=timezone.utc)
    p2_until_dt = datetime(period2_to.year, period2_to.month, period2_to.day, 23, 59, 59, tzinfo=timezone.utc)

    await _ensure_data(account, p1_since_dt, p1_until_dt, int(p1_since_dt.timestamp()), int(p1_until_dt.timestamp()))
    await _ensure_data(account, p2_since_dt, p2_until_dt, int(p2_since_dt.timestamp()), int(p2_until_dt.timestamp()))

    # Get data
    p1_stats = await crud.get_daily_stats(account.id, period1_from, period1_to)
    p2_stats = await crud.get_daily_stats(account.id, period2_from, period2_to)
    p1_posts = await crud.get_posts(account.id, p1_since_dt, p1_until_dt)
    p2_posts = await crud.get_posts(account.id, p2_since_dt, p2_until_dt)

    s1 = calculate_period_summary(p1_stats)
    s2 = calculate_period_summary(p2_stats)
    c1 = calculate_content_summary(p1_posts)
    c2 = calculate_content_summary(p2_posts)

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
        return f"{emoji} {label}: {v1:.1f} → {v2:.1f} ({sign}{d:.1f}{pct_str})" if isinstance(v1, float) else f"{emoji} {label}: {format_number(v1)} → {format_number(v2)} ({sign}{format_number(d)}{pct_str})"

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
    ]

    # AI comparison
    comparison_data = (
        f"Период 1 ({p1_str}): прирост подписчиков {s1['followers_growth']}, охват {s1['reach_total']}, "
        f"просмотры {s1['views_total']}, вовлечено {s1['accounts_engaged_total']}, "
        f"лайки {c1['total_likes']}, ER {c1['engagement_rate']}%, постов {c1['total_posts']}\n"
        f"Период 2 ({p2_str}): прирост подписчиков {s2['followers_growth']}, охват {s2['reach_total']}, "
        f"просмотры {s2['views_total']}, вовлечено {s2['accounts_engaged_total']}, "
        f"лайки {c2['total_likes']}, ER {c2['engagement_rate']}%, постов {c2['total_posts']}"
    )
    ai_analysis = await ai.analyze_comparison(comparison_data)

    lines.append(f"\n🤖 AI-сравнение\n{ai_analysis}")

    context = {
        "account": f"@{account.username} — {account.name}",
        "period": f"{p1_str} vs {p2_str}",
        "type": "comparison",
        "period1": {"name": p1_str, "stats": s1, "content": c1},
        "period2": {"name": p2_str, "stats": s2, "content": c2},
    }

    return {"text": "\n".join(lines), "context": context}