"""One-shot full sync of all configured accounts + sync status report.

Usage:
    python scripts/sync_check.py [--days 30] [--accounts name1,name2]
"""

import argparse
import asyncio
import os
import sys
from datetime import datetime, timedelta, timezone


async def main(days: int, only_accounts: str | None) -> None:
    from config import settings
    from database.engine import init_db
    from database import crud
    from services.data_sync import sync_account_data
    from instagram.client import close_shared_session, utc_now_naive

    await init_db()
    # seed all configured accounts (also adds accounts missing from the DB)
    for acc in settings.ACCOUNTS:
        await crud.save_or_update_account(
            instagram_user_id=acc["user_id"],
            username=acc["name"],
            name=acc["name"],
            access_token=acc["access_token"],
        )

    accounts = await crud.get_all_accounts()
    if only_accounts:
        wanted = {n.strip().lstrip("@").lower() for n in only_accounts.split(",") if n.strip()}
        accounts = [a for a in accounts if a.username.lower() in wanted]

    now = utc_now_naive()
    today = datetime(now.year, now.month, now.day)
    # Midnight-aligned window: IG totals are exact per requested window, so
    # off-midnight boundaries would attribute values to the wrong day.
    since = today - timedelta(days=days - 1)
    until = today + timedelta(days=1) - timedelta(seconds=1)
    for account in accounts:
        print(f"── syncing @{account.username} ({days} days) ──", flush=True)
        try:
            result = await sync_account_data(account, since, until)
            print(f"   result: {result}", flush=True)
        except Exception as e:
            print(f"   FAILED: {type(e).__name__}: {e}", flush=True)
    await close_shared_session()

    print("\n── sync status ──", flush=True)
    for account in await crud.get_all_accounts():
        print(
            f"@{account.username}: status={account.sync_status} "
            f"last_sync_at={account.last_sync_at} "
            f"followers={account.current_followers} "
            f"error={account.last_sync_error}",
            flush=True,
        )
    issues = []
    from services.maintenance import collect_sync_health_issues
    issues = await collect_sync_health_issues()
    if issues:
        print("\n── health issues ──", flush=True)
        for item in issues:
            print(f"• {item}", flush=True)
    else:
        print("\nno health issues", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--accounts", default=None)
    args = parser.parse_args()
    sys.path.insert(0, os.getcwd())
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    asyncio.run(main(args.days, args.accounts))
