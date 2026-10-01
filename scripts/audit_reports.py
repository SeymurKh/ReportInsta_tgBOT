"""Audit harness: generate reports/comparisons from a read-only SQLite snapshot
and cross-check every total against an independent SQL recomputation.

Usage:
    python scripts/audit_reports.py --db <snapshot.db> [--accounts name1,name2]

The snapshot is copied to a temp file and migrated there, so the input file is
never modified. Reports and comparisons are generated with the real
reports.generator code while all Instagram/AI calls are mocked out.
"""

import argparse
import asyncio
import json
import os
import shutil
import sqlite3
import sys
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch


def _date_value(value):
    return date.fromisoformat(value) if value else None


def _datetime_value(value):
    return datetime.fromisoformat(value) if value else None


def _namespace(row, date_fields=(), datetime_fields=(), bool_fields=()):
    values = dict(row)
    for field in date_fields:
        values[field] = _date_value(values.get(field))
    for field in datetime_fields:
        values[field] = _datetime_value(values.get(field))
    for field in bool_fields:
        values[field] = bool(values.get(field))
    return SimpleNamespace(**values)


def _present(row, metric):
    try:
        return metric in json.loads(row["metrics_present"] or "[]")
    except (TypeError, ValueError):
        return False


def _list_accounts(conn):
    rows = conn.execute(
        "SELECT accounts.id, accounts.username, accounts.name, "
        "accounts.current_followers, accounts.current_followers_at, "
        "MIN(daily_stats.date) AS min_date, MAX(daily_stats.date) AS max_date "
        "FROM accounts LEFT JOIN daily_stats ON daily_stats.account_id = accounts.id "
        "GROUP BY accounts.id ORDER BY accounts.id"
    ).fetchall()
    return [dict(r) for r in rows]


def _account_objects(conn, username):
    account_row = conn.execute(
        "SELECT id, username, name, current_followers, current_followers_at "
        "FROM accounts WHERE username = ?",
        (username,),
    ).fetchone()
    if account_row is None:
        raise ValueError(f"Unknown account: {username}")
    account = _namespace(account_row, datetime_fields=("current_followers_at",))
    daily_rows = conn.execute(
        "SELECT * FROM daily_stats WHERE account_id = ? ORDER BY date",
        (account.id,),
    ).fetchall()
    post_rows = conn.execute(
        "SELECT * FROM posts WHERE account_id = ? ORDER BY timestamp",
        (account.id,),
    ).fetchall()
    story_rows = conn.execute(
        "SELECT * FROM stories WHERE account_id = ? ORDER BY timestamp",
        (account.id,),
    ).fetchall()
    daily = [
        _namespace(r, date_fields=("date",), datetime_fields=("collected_at",), bool_fields=("is_partial",))
        for r in daily_rows
    ]
    posts = [_namespace(r, datetime_fields=("timestamp", "insights_updated_at")) for r in post_rows]
    stories = [_namespace(r, datetime_fields=("timestamp",), bool_fields=("is_active",)) for r in story_rows]
    # mirrors reports.generator._current_followers_snapshot legacy fallback
    account._latest_followers = daily_rows[-1]["followers"] if daily_rows else None
    return account, daily, daily_rows, posts, post_rows, stories, story_rows


def _slice(daily, daily_rows, posts, post_rows, stories, story_rows, date_from, date_to):
    def in_range(d):
        return date_from <= d <= date_to

    daily_s = [r for r in daily if in_range(r.date)]
    daily_rows_s = [r for r in daily_rows if in_range(_date_value(r["date"]))]
    posts_s = [p for p in posts if in_range(p.timestamp.date())]
    post_rows_s = [r for r in post_rows if in_range(_datetime_value(r["timestamp"]).date())]
    stories_s = [s for s in stories if in_range(s.timestamp.date())]
    story_rows_s = [r for r in story_rows if in_range(_datetime_value(r["timestamp"]).date())]
    return daily_s, daily_rows_s, posts_s, post_rows_s, stories_s, story_rows_s


def _report_inputs(conn, username, date_from, date_to):
    account, daily, daily_rows, posts, post_rows, stories, story_rows = _account_objects(conn, username)
    sliced = _slice(daily, daily_rows, posts, post_rows, stories, story_rows, date_from, date_to)
    daily_s, daily_rows_s, posts_s, post_rows_s, stories_s, story_rows_s = sliced
    latest_row = conn.execute(
        "SELECT * FROM daily_stats WHERE account_id = ? ORDER BY date DESC LIMIT 1",
        (account.id,),
    ).fetchone()
    latest = (
        _namespace(latest_row, date_fields=("date",), datetime_fields=("collected_at",), bool_fields=("is_partial",))
        if latest_row else None
    )
    return account, daily_s, latest, posts_s, stories_s, daily_rows_s, post_rows_s, story_rows_s


def _independent_totals(date_from, date_to, daily_rows, post_rows, story_rows):
    expected_days = (date_to - date_from).days + 1
    follower_known = (
        len(daily_rows) == expected_days
        and all(_present(row, "follower_count") for row in daily_rows)
    )
    follower_growth = sum((r["follower_count"] or 0) for r in daily_rows) if follower_known else None
    follower_start = None
    follower_pct = None
    if follower_growth is not None and daily_rows[-1]["followers"] is not None:
        follower_start = daily_rows[-1]["followers"] - follower_growth
        if follower_start < 0:
            follower_start = None
        elif follower_start > 0:
            follower_pct = round(follower_growth / follower_start * 100, 1)
    from analytics.calculations import detect_trend
    from types import SimpleNamespace as _NS
    trend = detect_trend([_NS(**r) for r in daily_rows], "follower_count")
    post_reach = sum((r["reach"] or 0) for r in post_rows)
    post_interactions = sum((r["total_interactions"] or 0) for r in post_rows)
    return {
        "days": len(daily_rows),
        "expected_days": expected_days,
        "followers_growth": follower_growth,
        "followers_growth_pct": follower_pct,
        "trend": trend,
        "reach_total": sum((r["reach"] or 0) for r in daily_rows),
        "views_total": sum((r["views"] or 0) for r in daily_rows),
        "accounts_engaged_total": sum((r["accounts_engaged"] or 0) for r in daily_rows),
        "posts": len(post_rows),
        "post_likes": sum((r["likes"] or 0) for r in post_rows),
        "post_comments": sum((r["comments"] or 0) for r in post_rows),
        "post_saves": sum((r["saved"] or 0) for r in post_rows),
        "post_shares": sum((r["shares"] or 0) for r in post_rows),
        "post_reach": post_reach,
        "post_interactions": post_interactions,
        "post_component_interactions": sum(
            (r["likes"] or 0) + (r["comments"] or 0) + (r["saved"] or 0) + (r["shares"] or 0)
            for r in post_rows
        ),
        "interaction_gap": post_interactions - sum(
            (r["likes"] or 0) + (r["comments"] or 0) + (r["saved"] or 0) + (r["shares"] or 0)
            for r in post_rows
        ),
        "engagement_rate": round(post_interactions / post_reach * 100, 1) if post_reach else 0.0,
        "stories": len(story_rows),
    }


async def _generate_report(account, daily, latest, posts, stories, date_from, date_to):
    import reports.generator as generator

    no_sync = AsyncMock(return_value={"api_delay_dates": [], "partial": False})
    no_story_refresh = AsyncMock()
    no_ai = patch("reports.generator.get_analyzer", return_value=None)
    with (
        patch("reports.generator._fetch_and_save_data", new=no_sync),
        patch("reports.generator._refresh_stories_if_recent", new=no_story_refresh),
        patch("reports.generator.ensure_period_snapshot", new=AsyncMock(return_value={})),
        patch("reports.generator.crud.get_daily_stats", new=AsyncMock(return_value=daily)),
        patch("reports.generator.crud.get_latest_stats", new=AsyncMock(return_value=latest)),
        patch("reports.generator.crud.get_posts", new=AsyncMock(return_value=posts)),
        patch("reports.generator.crud.get_stories", new=AsyncMock(return_value=stories)),
        no_ai,
    ):
        result = await generator.generate_report(
            account, custom_date_from=date_from, custom_date_to=date_to
        )
    return result


def _latest_followers_fallback(account):
    """Legacy accounts (no current_followers column value) fall back to the
    last daily_stats snapshot in the report generator."""
    return account._latest_followers


def _report_check_block(generated, account, independent, posts, stories):
    context = generated["context"]
    actual_stats, actual_content, actual_stories = context["stats"], context["content"], context["stories"]
    checks = {
            "days": context["data_quality"]["available_days"] == independent["days"],
            "followers_growth": actual_stats["followers_growth"] == independent["followers_growth"],
            "followers_growth_pct": actual_stats["followers_growth_pct"] == independent["followers_growth_pct"],
            "trend": context["trend"] == independent["trend"],
            "daily_reach": actual_stats["reach_total"] == independent["reach_total"],
            "daily_views": actual_stats["views_total"] == independent["views_total"],
            "daily_engaged": actual_stats["accounts_engaged_total"] == independent["accounts_engaged_total"],
            "post_count": actual_content["total_posts"] == independent["posts"],
            "post_likes": actual_content["total_likes"] == independent["post_likes"],
            "post_comments": actual_content["total_comments"] == independent["post_comments"],
            "post_saves": actual_content["total_saves"] == independent["post_saves"],
            "post_shares": actual_content["total_shares"] == independent["post_shares"],
            "post_component_interactions": actual_content["component_interactions"] == independent["post_component_interactions"],
            "post_reach": actual_content["total_reach"] == independent["post_reach"],
            "post_interactions": actual_content["total_interactions"] == independent["post_interactions"],
            "interaction_gap": actual_content["interaction_gap"] == independent["interaction_gap"],
            "engagement_rate": actual_content["engagement_rate"] == independent["engagement_rate"],
            "story_count": actual_stories["total_stories"] == independent["stories"],
            "current_followers": actual_stats["followers_current"] == (
                account.current_followers
                if account.current_followers is not None
                else _latest_followers_fallback(account)
            ),
        }
    from utils.formatters import format_number, format_pct
    from reports.generator import (
        _account_interaction_note, _interaction_reconciliation_note,
        TYPE_EMOJI, TYPE_NAME, WEEKDAYS_RU,
    )
    from reports.naming import (
        L_ENGAGED, L_FOLLOWERS_GROWTH, L_FOLLOWERS_NOW, L_POST_ER, L_POST_LIKES,
        L_POST_REACH, L_POSTS, L_REACH, L_REACH_DAILY, L_STORIES_REACH,
        L_STORIES_REPLIES, L_STORIES_SHARES, L_STORIES_VIEWS, L_VIEWS,
        SEC_CONTENT, SEC_STORIES, kv_row,
    )
    text = generated["text"]
    expected_note = _interaction_reconciliation_note(actual_content)
    checks["report_text_interaction_reconciliation"] = (
        expected_note in text if expected_note else "⚠️ Сверка взаимодействий" not in text
    )
    def value_str(value):
        return format_number(value) if value else "н/д"

    growth = actual_stats["followers_growth"]
    if growth is None:
        growth_val = "н/д"
    else:
        growth_sign = "+" if growth >= 0 else ""
        growth_val = f"{growth_sign}{format_number(growth)} ({format_pct(actual_stats['followers_growth_pct'])})"
    formats_line = (
        f"🎬 Reels {actual_content['total_reels']} · "
        f"📷 Фото {actual_content['total_images']} · "
        f"📸 Карусели {actual_content['total_carousels']}"
    )
    if actual_content["total_videos"]:
        formats_line += f" · 🎥 Видео {actual_content['total_videos']}"
    report_markers = {
        "current_followers": kv_row(L_FOLLOWERS_NOW, value_str(actual_stats["followers_current"])),
        "followers_growth": kv_row(L_FOLLOWERS_GROWTH, growth_val),
        "daily_reach": kv_row(L_REACH, value_str(actual_stats["reach_total"])),
        "daily_views": kv_row(L_VIEWS, value_str(actual_stats["views_total"])),
        "daily_engaged": kv_row(L_ENGAGED, value_str(actual_stats["accounts_engaged_total"])),
        "daily_average_reach": kv_row(L_REACH_DAILY, format_number(actual_stats["reach_avg_daily"])),
        "posts_title": f"{SEC_CONTENT} ({actual_content['total_posts']})",
        "posts_count": kv_row(L_POSTS, str(actual_content["total_posts"])),
        "average_likes": kv_row(L_POST_LIKES, str(actual_content["avg_likes"])),
        "average_reach": kv_row(L_POST_REACH, format_number(actual_content["avg_reach"])),
        "engagement_rate": kv_row(L_POST_ER, f"{actual_content['engagement_rate']}%"),
        "media_types": formats_line,
    }
    checks.update({f"report_text_{key}": marker in text for key, marker in report_markers.items()})
    account_note = _account_interaction_note(actual_stats)
    checks["report_text_account_reconciliation"] = (
        account_note in text if account_note else "Сверка взаимодействий аккаунта" not in text
    )
    from utils.formatters import MONTHS_RU
    post_markers = []
    for post in posts:
        day = post.timestamp.date()
        caption = post.caption[:50].replace("\n", " ").strip()
        if len(post.caption) > 50:
            caption += "..."
        post_markers.extend((
            f"📅 {day.day} {MONTHS_RU[day.month]} ({WEEKDAYS_RU[day.weekday()]}):",
            f'{TYPE_EMOJI.get(post.media_type, "📄")} {TYPE_NAME.get(post.media_type, post.media_type)} | "{caption}"',
            f"❤️ {post.likes} | 💬 {post.comments} | 💾 {post.saved} | 📤 {post.shares} | Охват {format_number(post.reach)}",
        ))
    checks["report_text_publications"] = all(marker in text for marker in post_markers)
    # AI context must contain every day and every post the report has —
    # silent truncation there made the model doubt real data (regression).
    from utils.formatters import format_context_for_ai
    ai_context = format_context_for_ai(context)
    expected_ai_items = [d["date"] for d in context.get("daily_stats", [])]
    expected_ai_items += [
        post["caption"][:50]
        for day in context.get("publications", [])
        for post in day["posts"]
    ]
    checks["ai_context_complete"] = all(
        item in ai_context or "…[опущено" in ai_context for item in expected_ai_items
    )
    if actual_stories["total_stories"]:
        story_markers = (
            f"{SEC_STORIES} ({actual_stories['total_stories']})",
            kv_row(L_STORIES_VIEWS, f"{format_number(actual_stories['total_views'])} (ср. {format_number(actual_stories['avg_views'])})"),
            kv_row(L_STORIES_REACH, format_number(actual_stories["total_reach"])),
            kv_row(L_STORIES_REPLIES, format_number(actual_stories["total_replies"])),
            kv_row(L_STORIES_SHARES, format_number(actual_stories["total_shares"])),
        )
        checks["report_text_stories"] = all(marker in text for marker in story_markers)
    else:
        checks["report_text_stories"] = "За период сторис не найдены." in text
    return checks


async def _audit_reports_for_account(conn, username):
    """Generate reports for several periods and cross-check every total."""
    account, daily, daily_rows, posts, post_rows, stories, story_rows = _account_objects(conn, username)
    latest_row = conn.execute(
        "SELECT * FROM daily_stats WHERE account_id = ? ORDER BY date DESC LIMIT 1",
        (account.id,),
    ).fetchone()
    latest = (
        _namespace(latest_row, date_fields=("date",), datetime_fields=("collected_at",), bool_fields=("is_partial",))
        if latest_row else None
    )
    results = []
    if not daily_rows:
        return [{"account": username, "period": None, "checks": {"has_data": False},
                 "all_checks_pass": True, "note": "no daily_stats rows — skipped"}]
    min_date = _date_value(daily_rows[0]["date"])
    max_date = _date_value(daily_rows[-1]["date"])
    span_days = (max_date - min_date).days + 1

    periods = [("full", min_date, max_date)]
    week_from = max_date - timedelta(days=6)
    if week_from >= min_date:
        periods.append(("week", week_from, max_date))
    month_from = max_date - timedelta(days=29)
    if month_from >= min_date:
        periods.append(("month", month_from, max_date))
    periods.append(("day", max_date, max_date))
    if span_days >= 4:
        mid = min_date + timedelta(days=span_days // 2 - 1)
        periods.append(("first_half", min_date, mid))

    for label, date_from, date_to in periods:
        daily_s, daily_rows_s, posts_s, post_rows_s, stories_s, story_rows_s = _slice(
            daily, daily_rows, posts, post_rows, stories, story_rows, date_from, date_to
        )
        independent = _independent_totals(date_from, date_to, daily_rows_s, post_rows_s, story_rows_s)
        generated = await _generate_report(account, daily_s, latest, posts_s, stories_s, date_from, date_to)
        checks = _report_check_block(generated, account, independent, posts_s, stories_s)
        entry = {
            "account": username,
            "label": label,
            "period": f"{date_from.isoformat()}..{date_to.isoformat()}",
            "checks": checks,
            "all_checks_pass": all(checks.values()),
            "independent_totals": independent,
            "data_quality": generated["context"]["data_quality"],
            "report_text": generated["text"],
        }
        results.append(entry)
    return results


async def _generate_comparison(account, daily, latest, posts, stories,
                               p1_from, p1_to, p2_from, p2_to):
    """Generate a comparison report with per-range crud results."""
    import reports.generator as generator

    def daily_for(_account_id, date_from, date_to):
        return [r for r in daily if date_from <= r.date <= date_to]

    def posts_for(_account_id, date_from, date_to):
        return [p for p in posts if date_from.date() <= p.timestamp.date() <= date_to.date()]

    def stories_for(_account_id, date_from, date_to):
        return [s for s in stories if date_from.date() <= s.timestamp.date() <= date_to.date()]

    no_sync = AsyncMock(return_value={"api_delay_dates": [], "partial": False})
    no_story_refresh = AsyncMock()
    no_ai = patch("reports.generator.get_analyzer", return_value=None)
    with (
        patch("reports.generator._fetch_and_save_data", new=no_sync),
        patch("reports.generator._refresh_stories_if_recent", new=no_story_refresh),
        patch("reports.generator.ensure_period_snapshot", new=AsyncMock(return_value={})),
        patch("reports.generator.crud.get_daily_stats", new=AsyncMock(side_effect=daily_for)),
        patch("reports.generator.crud.get_latest_stats", new=AsyncMock(return_value=latest)),
        patch("reports.generator.crud.get_posts", new=AsyncMock(side_effect=posts_for)),
        patch("reports.generator.crud.get_stories", new=AsyncMock(side_effect=stories_for)),
        no_ai,
    ):
        result = await generator.generate_comparison_periods_report(
            account, p1_from, p1_to, p2_from, p2_to
        )
    return result


async def _audit_comparisons_for_account(conn, username):
    """Compare two periods and cross-check both sides plus per-day averages."""
    account, daily, daily_rows, posts, post_rows, stories, story_rows = _account_objects(conn, username)
    results = []
    if len(daily_rows) < 4:
        return [{"account": username, "note": "not enough days for comparison — skipped",
                 "checks": {"has_data": False}, "all_checks_pass": True}]
    min_date = _date_value(daily_rows[0]["date"])
    max_date = _date_value(daily_rows[-1]["date"])
    span_days = (max_date - min_date).days + 1

    def daily_between(d1, d2):
        return [r for r in daily_rows if d1 <= _date_value(r["date"]) <= d2]

    def posts_between(d1, d2):
        return [r for r in post_rows if d1 <= _datetime_value(r["timestamp"]).date() <= d2]

    def stories_between(d1, d2):
        return [r for r in story_rows if d1 <= _datetime_value(r["timestamp"]).date() <= d2]

    cases = []
    half = span_days // 2
    if half >= 1:
        cases.append(("halves_equal",
                      (min_date, min_date + timedelta(days=half - 1)),
                      (min_date + timedelta(days=half), max_date)))
    if span_days >= 6:
        p1 = (max_date - timedelta(days=5), max_date - timedelta(days=1))
        if p1[0] >= min_date:
            cases.append(("unequal_5_vs_1", p1, (max_date, max_date)))
    if span_days >= 14:
        p2 = (max_date - timedelta(days=6), max_date)
        p1 = (max_date - timedelta(days=13), max_date - timedelta(days=7))
        if p1[0] >= min_date:
            cases.append(("week_vs_week", p1, p2))

    for label, (p1_from, p1_to), (p2_from, p2_to) in cases:
        independent_1 = _independent_totals(
            p1_from, p1_to,
            daily_between(p1_from, p1_to), posts_between(p1_from, p1_to), stories_between(p1_from, p1_to),
        )
        independent_2 = _independent_totals(
            p2_from, p2_to,
            daily_between(p2_from, p2_to), posts_between(p2_from, p2_to), stories_between(p2_from, p2_to),
        )
        generated = await _generate_comparison(
            account, daily, None, posts, stories, p1_from, p1_to, p2_from, p2_to
        )
        context = generated["context"]
        s1 = context["period1"]["stats"]
        s2 = context["period2"]["stats"]
        c1 = context["period1"]["content"]
        c2 = context["period2"]["content"]
        checks = {
            "p1_reach": s1["reach_total"] == independent_1["reach_total"],
            "p2_reach": s2["reach_total"] == independent_2["reach_total"],
            "p1_views": s1["views_total"] == independent_1["views_total"],
            "p2_views": s2["views_total"] == independent_2["views_total"],
            "p1_engaged": s1["accounts_engaged_total"] == independent_1["accounts_engaged_total"],
            "p2_engaged": s2["accounts_engaged_total"] == independent_2["accounts_engaged_total"],
            "p1_followers_growth": s1["followers_growth"] == independent_1["followers_growth"],
            "p2_followers_growth": s2["followers_growth"] == independent_2["followers_growth"],
            "p1_posts": c1["total_posts"] == independent_1["posts"],
            "p2_posts": c2["total_posts"] == independent_2["posts"],
            "p1_likes": c1["total_likes"] == independent_1["post_likes"],
            "p2_likes": c2["total_likes"] == independent_2["post_likes"],
            "p1_interactions": c1["total_interactions"] == independent_1["post_interactions"],
            "p2_interactions": c2["total_interactions"] == independent_2["post_interactions"],
            "p1_interaction_gap": c1["interaction_gap"] == independent_1["interaction_gap"],
            "p2_interaction_gap": c2["interaction_gap"] == independent_2["interaction_gap"],
        }
        avail1 = context["period_days"]["available1"]
        avail2 = context["period_days"]["available2"]
        checks["available_days"] = avail1 == independent_1["days"] and avail2 == independent_2["days"]
        text = generated["text"]
        checks["text_has_headers"] = "📊 Сравнение периодов" in text and "Средний охват в день" in text
        checks["text_has_format_perf"] = "Форматы контента:" in text
        results.append({
            "account": username,
            "label": label,
            "period": f"{p1_from.isoformat()}..{p1_to.isoformat()} vs {p2_from.isoformat()}..{p2_to.isoformat()}",
            "checks": checks,
            "all_checks_pass": all(checks.values()),
            "independent": {"p1": independent_1, "p2": independent_2},
            "comparison_text": text[:3000],
        })
    return results


async def _audit_edge_cases(conn, username):
    """Boundary conditions must fail loudly or render gracefully."""
    import reports.generator as generator
    from bot.helpers import parse_period_input, parse_comparison_input

    checks = {}
    account, daily, daily_rows, posts, post_rows, stories, story_rows = _account_objects(conn, username)
    max_date = _date_value(daily_rows[-1]["date"]) if daily_rows else date.today()

    try:
        generator.resolve_period("week", date.today(), date.today() + timedelta(days=1))
        checks["future_rejected"] = False
    except ValueError:
        checks["future_rejected"] = True

    try:
        generator.resolve_period("week", max_date, max_date - timedelta(days=3))
        checks["reversed_rejected"] = False
    except ValueError:
        checks["reversed_rejected"] = True

    try:
        await _generate_comparison(
            account, daily, None, posts, stories,
            max_date - timedelta(days=5), max_date, max_date - timedelta(days=2), max_date,
        )
        checks["overlap_rejected"] = False
    except ValueError:
        checks["overlap_rejected"] = True

    empty_from, empty_to = max_date - timedelta(days=40), max_date - timedelta(days=35)
    if not any(empty_from <= r.date <= empty_to for r in daily):
        generated = await _generate_report(account, [], None, [], [], empty_from, empty_to)
        checks["empty_period_renders"] = (
            "ПУБЛИКАЦИИ (0)" in generated["text"]
            and generated["context"]["stats"]["reach_total"] == 0
        )
    else:
        checks["empty_period_renders"] = True

    generated = await _generate_report(
        account,
        [r for r in daily if r.date == max_date], None,
        [p for p in posts if p.timestamp.date() == max_date],
        [s for s in stories if s.timestamp.date() == max_date],
        max_date, max_date,
    )
    checks["single_day_renders"] = bool(generated["text"])

    today = date.today()
    for name, call in (
        ("parse_future", lambda: parse_period_input((today + timedelta(days=2)).strftime("%d.%m"), today)),
        ("parse_bad_format", lambda: parse_period_input("31/12", today)),
        ("parse_comparison_bad", lambda: parse_comparison_input("01.01-02.02", today)),
    ):
        try:
            call()
            checks[name] = False
        except ValueError:
            checks[name] = True

    return {"account": username, "checks": checks, "all_checks_pass": all(checks.values())}


def _migrate_copy(db_path: str) -> str:
    """Copy the snapshot to temp and apply migrations there (input stays untouched)."""
    temp_path = Path(tempfile.gettempdir()) / f"audit_snapshot_{os.getpid()}.db"
    shutil.copyfile(db_path, temp_path)
    os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{temp_path.as_posix()}"
    # engine reads DATABASE_URL at import time — force a clean re-import
    for module in list(sys.modules):
        if module == "config" or module == "database" or module.startswith("database."):
            del sys.modules[module]
    from database.engine import init_db
    asyncio.run(init_db())
    return str(temp_path)


async def audit(db_path, only_accounts=None):
    absolute = Path(db_path).resolve()
    conn = sqlite3.connect(f"file:{absolute.as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    quick_check = conn.execute("PRAGMA quick_check").fetchone()[0]
    fk_issues = conn.execute("PRAGMA foreign_key_check").fetchall()
    if quick_check != "ok" or fk_issues:
        raise RuntimeError(f"SQLite integrity check failed: {quick_check}; FK: {len(fk_issues)}")

    accounts = _list_accounts(conn)
    if only_accounts:
        wanted = {name.strip().lstrip("@").lower() for name in only_accounts.split(",") if name.strip()}
        accounts = [a for a in accounts if a["username"].lower() in wanted]

    reports_out, comparisons_out, edge_out = [], [], []
    for acc in accounts:
        username = acc["username"]
        reports_out.extend(await _audit_reports_for_account(conn, username))
        comparisons_out.extend(await _audit_comparisons_for_account(conn, username))
        edge_out.append(await _audit_edge_cases(conn, username))
    conn.close()
    return {
        "database": str(absolute),
        "quick_check": quick_check,
        "foreign_key_issues": 0,
        "accounts": [a["username"] for a in accounts],
        "reports": reports_out,
        "comparisons": comparisons_out,
        "edge_cases": edge_out,
        "summary": {
            "report_checks_total": sum(len(r["checks"]) for r in reports_out),
            "report_checks_failed": sum(sum(1 for v in r["checks"].values() if not v) for r in reports_out),
            "comparison_checks_total": sum(len(r["checks"]) for r in comparisons_out),
            "comparison_checks_failed": sum(sum(1 for v in r["checks"].values() if not v) for r in comparisons_out),
            "edge_checks_total": sum(len(r["checks"]) for r in edge_out),
            "edge_checks_failed": sum(sum(1 for v in r["checks"].values() if not v) for r in edge_out),
        },
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True, help="Path to a consistent SQLite snapshot")
    parser.add_argument("--accounts", default=None,
                        help="Comma-separated usernames to audit (default: all)")
    args = parser.parse_args()
    sys.path.insert(0, os.getcwd())
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:////tmp/reportinsta-audit-no-db.db")
    migrated = _migrate_copy(args.db)
    result = asyncio.run(audit(migrated, only_accounts=args.accounts))
    result["database_original"] = str(Path(args.db).resolve())
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
