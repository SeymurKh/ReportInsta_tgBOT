"""Backfill post insights fields added later (e.g. IMAGE shares, watch time).

Default: re-fetch only posts whose total_interactions exceeds the visible
component sum. With --refresh-all every post (optionally filtered) is re-read,
which backfills newly collected metrics such as feed views and reel watch time.

Usage:
    python scripts/backfill_post_insights.py [--media-type IMAGE] [--account name] [--refresh-all]
"""

import argparse
import asyncio
import json
import os
import sys


async def main(
    media_type: str | None,
    only_account: str | None,
    refresh_all: bool,
    missing_only: bool,
) -> None:
    from sqlalchemy import select
    from database.engine import async_session_factory, init_db
    from database.models import Account, Post
    from instagram.client import (
        InstagramClient, close_shared_session,
        MEDIA_OPTIONAL_VIEW_TYPES, MEDIA_WATCHTIME_METRICS, MEDIA_WATCHTIME_METRIC_TYPES,
    )

    await init_db()
    async with async_session_factory() as session:
        rows = list((await session.execute(select(Post))).scalars())
        accounts = {a.id: a for a in (await session.execute(select(Account))).scalars()}

    targets = []
    for p in rows:
        if media_type and p.media_type != media_type:
            continue
        if missing_only:
            needs_watch = (
                p.media_type in MEDIA_WATCHTIME_METRIC_TYPES
                and p.avg_watch_time is None
            )
            needs_views = (
                p.media_type in MEDIA_OPTIONAL_VIEW_TYPES
                and not (p.views or 0)
            )
            if needs_watch or needs_views:
                targets.append(p)
            continue
        if refresh_all:
            targets.append(p)
            continue
        comp = (p.likes or 0) + (p.comments or 0) + (p.saved or 0) + (p.shares or 0)
        if (p.total_interactions or 0) > comp:
            targets.append(p)
    mode = "missing-only" if missing_only else ("refresh-all" if refresh_all else "component gap")
    print(f"posts to refresh ({mode}): {len(targets)}")

    by_account: dict[int, list] = {}
    for p in targets:
        by_account.setdefault(p.account_id, []).append(p)

    for acc_id, posts in by_account.items():
        acc = accounts[acc_id]
        if only_account and acc.username.lower() != only_account.lower().lstrip("@"):
            continue
        client = InstagramClient(acc.instagram_user_id, acc.access_token)
        for p in posts:
            try:
                if missing_only:
                    insights = {}
                    if (
                        p.media_type in MEDIA_WATCHTIME_METRIC_TYPES
                        and p.avg_watch_time is None
                    ):
                        watch = await client.get_media_metric_family(
                            p.instagram_media_id, MEDIA_WATCHTIME_METRICS
                        )
                        if "ig_reels_avg_watch_time" in watch:
                            watch["avg_watch_time"] = watch.pop("ig_reels_avg_watch_time")
                        if "ig_reels_video_view_total_time" in watch:
                            watch["total_watch_time"] = watch.pop("ig_reels_video_view_total_time")
                        insights.update(watch)
                    if (
                        p.media_type in MEDIA_OPTIONAL_VIEW_TYPES
                        and not (p.views or 0)
                    ):
                        insights.update(await client.get_media_metric_family(
                            p.instagram_media_id, ["views"]
                        ))
                else:
                    insights = await client.get_media_insights(p.instagram_media_id, p.media_type)
            except Exception as e:
                print(f"ERR {p.instagram_media_id}: {type(e).__name__}: {e}")
                continue
            async with async_session_factory() as session:
                row = (await session.execute(
                    select(Post).where(Post.instagram_media_id == p.instagram_media_id)
                )).scalar_one()
                before = (row.shares or 0, row.views or 0, row.avg_watch_time)
                for field in (
                    "saved", "shares", "reach", "total_interactions", "views",
                    "follows", "profile_visits", "profile_activity",
                    "avg_watch_time", "total_watch_time",
                ):
                    if insights.get(field) is not None:
                        setattr(row, field, insights[field])
                if insights.get("profile_activity_breakdown") is not None:
                    row.profile_activity_breakdown = json.dumps(
                        insights["profile_activity_breakdown"], sort_keys=True
                    )
                try:
                    present = set(json.loads(row.insights_present or "[]"))
                except (TypeError, ValueError):
                    present = set()
                present.update(
                    key for key, value in insights.items()
                    if value is not None and not key.endswith("_breakdown")
                )
                row.insights_present = json.dumps(sorted(present))
                await session.commit()
                print(
                    f"@{acc.username} {p.instagram_media_id} ({p.timestamp.date()}, {p.media_type}): "
                    f"shares {before[0]} -> {row.shares}, views {before[1]} -> {row.views}, "
                    f"avg_watch_time {before[2]} -> {row.avg_watch_time}, "
                    f"total_interactions={row.total_interactions}"
                )
        await client.close()
    await close_shared_session()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--media-type", default=None, help="e.g. IMAGE")
    parser.add_argument("--account", default=None)
    parser.add_argument("--refresh-all", action="store_true",
                        help="Refresh every post, not only the component-gap ones")
    parser.add_argument("--missing-only", action="store_true",
                        help="Fetch only missing watch time / feed views (1 call per post)")
    args = parser.parse_args()
    sys.path.insert(0, os.getcwd())
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    asyncio.run(main(args.media_type, args.account, args.refresh_all, args.missing_only))
