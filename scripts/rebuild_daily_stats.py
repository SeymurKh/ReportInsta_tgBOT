"""One-shot rebuild of daily stats using Instagram's own day buckets.

Re-fetches every day bucket in the window with bucket-aligned total_value
requests, overwrites daily rows (provenance included) and refreshes the
standard period snapshots. Writes only to the configured DB.

Usage:
    python scripts/rebuild_daily_stats.py [--since YYYY-MM-DD] [--until YYYY-MM-DD] [--accounts name1,name2]
"""

import argparse
import asyncio
from datetime import datetime, timedelta

from database import crud
from database.engine import init_db
from instagram.client import InstagramClient, utc_now_naive
from services.data_sync import _refresh_standard_period_snapshots

DAY_REQUEST_PAUSE = 0.15  # stay well under IG rate limits


async def rebuild_account(account, since: datetime, until: datetime) -> None:
    now = utc_now_naive()
    client = InstagramClient(account.instagram_user_id, account.access_token)
    try:
        buckets, series = await client.get_day_buckets(since, until)
        span = f"{buckets[0].day_key} .. {buckets[-1].day_key}" if buckets else "no buckets"
        print(f"@{account.username}: {len(buckets)} day buckets ({span})")
        for bucket in buckets:
            totals = await client.get_account_day_totals(bucket.start, bucket.end)
            present = [
                name for name, values in series.items()
                if values.get(bucket.day_key) is not None
            ] + [name for name in totals]
            await crud.save_daily_stats(
                account_id=account.id,
                stats_date=bucket.label,
                followers=None,
                following=None,
                media_count=None,
                reach=series.get("reach", {}).get(bucket.day_key),
                follower_count=series.get("follower_count", {}).get(bucket.day_key),
                views=totals.get("views"),
                accounts_engaged=totals.get("accounts_engaged"),
                collected_at=now,
                is_partial=False,
                metrics_present=present,
                bucket_start=bucket.start,
                bucket_end=bucket.end,
                account_metrics=totals,
            )
            print(
                f"  {bucket.day_key}: reach={series.get('reach', {}).get(bucket.day_key)} "
                f"views={totals.get('views')} engaged={totals.get('accounts_engaged')} "
                f"interactions={totals.get('total_interactions')}"
            )
            await asyncio.sleep(DAY_REQUEST_PAUSE)
        await _refresh_standard_period_snapshots(client, account, until.date(), now)
    finally:
        await client.close()


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--since", help="YYYY-MM-DD, default: 90 days back")
    parser.add_argument("--until", help="YYYY-MM-DD, default: now")
    parser.add_argument("--accounts", help="comma-separated usernames")
    args = parser.parse_args()

    await init_db()
    accounts = await crud.get_all_accounts()
    if args.accounts:
        wanted = {name.strip().lstrip("@").lower() for name in args.accounts.split(",")}
        accounts = [a for a in accounts if a.username.lower() in wanted]

    until = datetime.fromisoformat(args.until) if args.until else utc_now_naive()
    since = datetime.fromisoformat(args.since) if args.since else until - timedelta(days=90)

    for account in accounts:
        await rebuild_account(account, since, until)
    print("Rebuild complete.")


if __name__ == "__main__":
    asyncio.run(main())