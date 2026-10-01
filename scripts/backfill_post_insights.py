"""Backfill post insights fields added later (e.g. IMAGE shares).

For every post whose total_interactions exceeds the visible component sum,
re-fetch media insights and update the stored values + provenance.

Usage:
    python scripts/backfill_post_insights.py [--media-type IMAGE] [--account name]
"""

import argparse
import asyncio
import json
import os
import sys


async def main(media_type: str | None, only_account: str | None) -> None:
    from sqlalchemy import select
    from database.engine import async_session_factory, init_db
    from database.models import Account, Post
    from instagram.client import InstagramClient, close_shared_session

    await init_db()
    async with async_session_factory() as session:
        rows = list((await session.execute(select(Post))).scalars())
        accounts = {a.id: a for a in (await session.execute(select(Account))).scalars()}

    targets = []
    for p in rows:
        if media_type and p.media_type != media_type:
            continue
        comp = (p.likes or 0) + (p.comments or 0) + (p.saved or 0) + (p.shares or 0)
        if (p.total_interactions or 0) > comp:
            targets.append(p)
    print(f"posts with component gap: {len(targets)}")

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
                insights = await client.get_media_insights(p.instagram_media_id, p.media_type)
            except Exception as e:
                print(f"ERR {p.instagram_media_id}: {type(e).__name__}: {e}")
                continue
            async with async_session_factory() as session:
                row = (await session.execute(
                    select(Post).where(Post.instagram_media_id == p.instagram_media_id)
                )).scalar_one()
                before = row.shares or 0
                for field in ("saved", "shares", "reach", "total_interactions", "views"):
                    if insights.get(field) is not None:
                        setattr(row, field, insights[field])
                try:
                    present = set(json.loads(row.insights_present or "[]"))
                except (TypeError, ValueError):
                    present = set()
                present.update(k for k, v in insights.items() if v is not None)
                row.insights_present = json.dumps(sorted(present))
                await session.commit()
                print(
                    f"@{acc.username} {p.instagram_media_id} ({p.timestamp.date()}, {p.media_type}): "
                    f"shares {before} -> {row.shares}, total_interactions={row.total_interactions}"
                )
        await client.close()
    await close_shared_session()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--media-type", default=None, help="e.g. IMAGE")
    parser.add_argument("--account", default=None)
    args = parser.parse_args()
    sys.path.insert(0, os.getcwd())
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    asyncio.run(main(args.media_type, args.account))
