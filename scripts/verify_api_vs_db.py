"""Live reconciliation: compare Instagram Graph API responses against DB rows.

Read-only with respect to the database — fetches live API data and reports
every discrepancy (API value vs stored value). Run after (or before) a sync to
verify that DB writes and freshness handling are correct.

Usage:
    python scripts/verify_api_vs_db.py [--days 14] [--accounts name1,name2]
"""

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime, timedelta, timezone


def _round_diff(api_value, db_value):
    if api_value is None or db_value is None:
        return {"api": api_value, "db": db_value, "match": api_value == db_value}
    return {"api": api_value, "db": db_value, "match": int(api_value) == int(db_value)}


async def verify_account(account, days: int) -> dict:
    from instagram.client import InstagramClient, parse_ig_timestamp
    from database import crud

    client = InstagramClient(account.instagram_user_id, account.access_token)
    out = {
        "account": account.username,
        "checks": {},
        "profile": {},
        "daily": [],
        "posts": [],
        "stories": [],
        "errors": [],
    }

    # ── 1. Profile snapshot vs accounts table ──
    try:
        user_info = await client.get_user_info()
    except Exception as e:
        out["errors"].append(f"user_info: {e}")
        user_info = {}

    if user_info:
        stored = (
            account.current_followers
            if account.current_followers is not None
            else getattr(account, "_latest_followers", None)
        )
        out["profile"] = {
            "followers": _round_diff(user_info.get("followers_count"), stored),
            "api_media_count": user_info.get("media_count"),
            "api_follows_count": user_info.get("follows_count"),
        }
        out["checks"]["profile_username"] = user_info.get("username") == account.username

    # ── 2. Daily insights vs daily_stats ──
    today = datetime.now(timezone.utc).date()
    window_start = today - timedelta(days=days)
    since = int(datetime(window_start.year, window_start.month, window_start.day,
                         tzinfo=timezone.utc).timestamp())
    until = int(datetime.now(timezone.utc).timestamp())
    try:
        insights_raw = await client.get_account_insights(since, until)
    except Exception as e:
        out["errors"].append(f"account_insights: {e}")
        insights_raw = {}

    api_daily: dict[str, dict] = {}
    for item in insights_raw.get("data", []):
        name = item.get("name")
        for val in item.get("values", []):
            end_time = val.get("end_time", "")
            if not end_time:
                continue
            day = parse_ig_timestamp(end_time).strftime("%Y-%m-%d")
            api_daily.setdefault(day, {})[name] = val.get("value", 0)

    daily_rows = await crud.get_all_daily_stats_dates(account.id)
    db_daily = {r.date.isoformat(): r for r in daily_rows}
    account._latest_followers = daily_rows[-1].followers if daily_rows else None

    for day in sorted(api_daily):
        metrics = api_daily[day]
        row = db_daily.get(day)
        entry = {"date": day, "api": metrics}
        if row is None:
            entry["status"] = "missing_in_db"
        else:
            diffs = {}
            for metric in ("reach", "follower_count"):
                if metric in metrics:
                    diffs[metric] = _round_diff(metrics[metric], getattr(row, metric, None))
            entry["diffs"] = diffs
            entry["status"] = "match" if all(d["match"] for d in diffs.values()) else "mismatch"
        out["daily"].append(entry)

    # new-format metrics (views, accounts_engaged) for the last 3 days
    for offset in range(0, min(3, days)):
        d = today - timedelta(days=offset)
        day_start = int(datetime(d.year, d.month, d.day, tzinfo=timezone.utc).timestamp())
        day_end = day_start + 86400
        try:
            data = await client.get_account_insights_new_metrics_day(day_start, day_end)
            new_metrics = {
                item.get("name"): item.get("total_value", {}).get("value", 0)
                for item in data.get("data", [])
            }
        except Exception as e:
            out["errors"].append(f"new_metrics {d}: {e}")
            continue
        row = db_daily.get(d.isoformat())
        diffs = {}
        for metric in ("views", "accounts_engaged"):
            if metric in new_metrics:
                diffs[metric] = _round_diff(new_metrics[metric], getattr(row, metric, None) if row else None)
        out["daily"].append({
            "date": d.isoformat(), "api_new_metrics": new_metrics, "diffs": diffs,
            "status": "match" if diffs and all(v["match"] for v in diffs.values()) else "mismatch",
        })

    # ── 3. Media + insights vs posts ──
    from database.engine import async_session_factory
    from sqlalchemy import select, and_
    from database.models import Post, Story
    try:
        all_media = await client.get_media_list(limit=100)
    except Exception as e:
        out["errors"].append(f"media_list: {e}")
        all_media = []

    now_naive = datetime.now(timezone.utc).replace(tzinfo=None)
    cutoff = now_naive - timedelta(days=days)
    async with async_session_factory() as session:
        result = await session.execute(
            select(Post).where(and_(Post.account_id == account.id, Post.timestamp >= cutoff))
        )
        db_posts = {p.instagram_media_id: p for p in result.scalars().all()}

    checked = 0
    for media in all_media:
        ts = parse_ig_timestamp(media["timestamp"])
        if ts < cutoff:
            continue
        media_id = media["id"]
        row = db_posts.get(media_id)
        entry = {"media_id": media_id, "timestamp": media["timestamp"]}
        if row is None:
            entry["status"] = "missing_in_db"
            out["posts"].append(entry)
            continue
        diffs = {
            "likes": _round_diff(media.get("like_count"), row.likes),
            "comments": _round_diff(media.get("comments_count"), row.comments),
        }
        media_type = media.get("media_type", "IMAGE")
        if media_type == "VIDEO" and media.get("media_product_type", "") == "REELS":
            media_type = "REELS"
        entry["media_type_match"] = media_type == row.media_type
        try:
            insights = await client.get_media_insights(media_id, media_type)
            for metric in ("reach", "saved", "shares", "total_interactions", "views"):
                if metric in insights:
                    diffs[metric] = _round_diff(insights[metric], getattr(row, metric, None))
        except Exception as e:
            out["errors"].append(f"media_insights {media_id}: {e}")
        entry["diffs"] = diffs
        entry["status"] = (
            "match"
            if all(v["match"] for v in diffs.values()) and entry["media_type_match"]
            else "mismatch"
        )
        out["posts"].append(entry)
        checked += 1
        if checked >= 15:
            break

    # ── 4. Active stories vs stories ──
    try:
        active = await client.get_active_stories()
    except Exception as e:
        out["errors"].append(f"stories: {e}")
        active = []
    for item in active:
        media_id = item["id"]
        async with async_session_factory() as session:
            result = await session.execute(
                select(Story).where(Story.instagram_media_id == media_id)
            )
            row = result.scalar_one_or_none()
        entry = {"media_id": media_id}
        if row is None:
            entry["status"] = "missing_in_db"
        else:
            try:
                metrics = await client.get_story_insights(media_id)
                diffs = {
                    k: _round_diff(metrics.get(k), getattr(row, k, None))
                    for k in ("views", "reach", "replies", "shares")
                    if k in metrics
                }
                entry["diffs"] = diffs
                entry["status"] = "match" if diffs and all(v["match"] for v in diffs.values()) else "mismatch"
            except Exception as e:
                out["errors"].append(f"story_insights {media_id}: {e}")
                entry["status"] = "error"
        out["stories"].append(entry)

    return out


async def main(days: int, only_accounts: str | None) -> dict:
    from database import crud

    accounts = await crud.get_all_accounts()
    if only_accounts:
        wanted = {n.strip().lstrip("@").lower() for n in only_accounts.split(",") if n.strip()}
        accounts = [a for a in accounts if a.username.lower() in wanted]

    results = []
    for account in accounts:
        results.append(await verify_account(account, days))
    from instagram.client import close_shared_session
    await close_shared_session()

    totals = {"match": 0, "mismatch": 0, "missing_in_db": 0, "error": 0}
    for r in results:
        for group in ("daily", "posts", "stories"):
            for entry in r[group]:
                status = entry.get("status")
                if status in totals:
                    totals[status] += 1
    return {
        "days": days,
        "accounts": [r["account"] for r in results],
        "results": results,
        "summary": {
            "entries_match": totals["match"],
            "entries_mismatch": totals["mismatch"],
            "entries_missing_in_db": totals["missing_in_db"],
            "entries_error": totals["error"],
            "errors": sum(len(r["errors"]) for r in results),
        },
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=14)
    parser.add_argument("--accounts", default=None)
    args = parser.parse_args()
    sys.path.insert(0, os.getcwd())
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    result = asyncio.run(main(args.days, args.accounts))
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))