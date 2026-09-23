import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

import aiohttp

from config import settings

logger = logging.getLogger(__name__)

MEDIA_METRICS_MAP: dict[str, list[str]] = {
    "IMAGE": ["reach", "likes", "comments", "saved", "total_interactions"],
    "VIDEO": ["reach", "likes", "comments", "saved", "shares", "total_interactions", "views"],
    "CAROUSEL_ALBUM": ["reach", "likes", "comments", "saved", "shares", "total_interactions"],
    "REELS": ["reach", "likes", "comments", "saved", "shares", "total_interactions", "views"],
}

# Story metrics (plain, single request)
STORY_METRICS: list[str] = [
    "views", "reach", "replies", "shares", "total_interactions",
    "profile_activity", "follows",
]
# Navigation metric requires a breakdown and is requested separately
STORY_NAVIGATION_METRIC = "navigation"
STORY_NAVIGATION_BREAKDOWN = "story_navigation_action_type"

ACCOUNT_METRICS_OLD = ["reach", "follower_count"]
ACCOUNT_METRICS_NEW = ["views", "accounts_engaged"]

REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=60)


def _resolve_media_type(raw_type: str, product_type: str) -> str:
    """Determine actual media type using media_product_type from Instagram API."""
    if raw_type == "VIDEO" and product_type == "REELS":
        return "REELS"
    return raw_type  # IMAGE, VIDEO, CAROUSEL_ALBUM


def parse_ig_timestamp(ts_str: str) -> datetime:
    """Parse Instagram ISO-8601 timestamp to naive UTC datetime.

    All datetimes stored in the DB are naive UTC so SQL comparisons
    never mix aware/naive values.
    """
    dt = datetime.fromisoformat(ts_str.replace("+0000", "+00:00"))
    return dt.astimezone(timezone.utc).replace(tzinfo=None)


def utc_now_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


_shared_session: aiohttp.ClientSession | None = None


async def _get_shared_session() -> aiohttp.ClientSession:
    global _shared_session
    if _shared_session is None or _shared_session.closed:
        _shared_session = aiohttp.ClientSession(timeout=REQUEST_TIMEOUT)
    return _shared_session


async def close_shared_session():
    global _shared_session
    if _shared_session and not _shared_session.closed:
        await _shared_session.close()
        _shared_session = None


class InstagramAPIError(Exception):
    pass


class TokenExpiredError(InstagramAPIError):
    pass


class NotEnoughViewersError(InstagramAPIError):
    """IG error #10 — media has < 5 viewers, insights unavailable."""
    pass


class InstagramClient:
    def __init__(self, user_id: str, access_token: str):
        self.user_id = user_id
        self.access_token = access_token
        self.base_url = f"{settings.INSTAGRAM_BASE_URL}/{settings.INSTAGRAM_API_VERSION}"

    async def _get_session(self) -> aiohttp.ClientSession:
        return await _get_shared_session()

    async def _request(self, url: str, params: Optional[dict] = None) -> dict:
        if params is None:
            params = {}
        params["access_token"] = self.access_token
        session = await self._get_session()

        for attempt in range(3):
            try:
                async with session.get(url, params=params) as resp:
                    data = await resp.json(content_type=None)
                    if resp.status == 200:
                        return data
                    error = data.get("error", {})
                    code = error.get("code", 0)
                    msg = error.get("message", "Unknown")
                    logger.error(f"IG API [{code}]: {msg}")

                    if code == 10 and "not enough viewers" in msg.lower():
                        # Story/media has < 5 viewers — insights unavailable,
                        # this is not a failure, treat as empty data.
                        raise NotEnoughViewersError(msg)
                    if code == 4:  # application-level rate limit
                        await asyncio.sleep(2 ** attempt * 5)
                        continue
                    if code == 190:
                        raise TokenExpiredError("Token expired")
                    raise InstagramAPIError(f"[{code}]: {msg}")
            except aiohttp.ClientError as e:
                if attempt < 2:
                    await asyncio.sleep(2 ** attempt * 2)
                else:
                    raise InstagramAPIError(f"Network: {e}")

        raise InstagramAPIError("Max retries exceeded")

    async def get_user_info(self) -> dict:
        url = f"{self.base_url}/me"
        params = {"fields": "user_id,username,name,account_type,followers_count,follows_count,media_count"}
        return await self._request(url, params)


    async def get_account_insights(self, since: int, until: int) -> dict:
        """Old-format metrics (reach, follower_count) for the whole range."""
        url = f"{self.base_url}/{self.user_id}/insights"
        params = {
            "metric": ",".join(ACCOUNT_METRICS_OLD),
            "period": "day",
            "since": str(since),
            "until": str(until),
        }
        return await self._request(url, params)

    async def get_account_insights_new_metrics_day(self, day_start: int, day_end: int) -> dict:
        """New-format metrics (views, accounts_engaged) for a single day."""
        url = f"{self.base_url}/{self.user_id}/insights"
        params = {
            "metric": ",".join(ACCOUNT_METRICS_NEW),
            "period": "day",
            "since": str(day_start),
            "until": str(day_end),
            "metric_type": "total_value",
        }
        return await self._request(url, params)

    async def get_media_list(self, limit: int = 50) -> list[dict]:
        url = f"{self.base_url}/{self.user_id}/media"
        params = {
            "fields": "id,caption,media_type,media_product_type,permalink,timestamp,like_count,comments_count",
            "limit": str(limit),
        }
        all_media = []
        while url:
            data = await self._request(url, params)
            all_media.extend(data.get("data", []))
            url = data.get("paging", {}).get("next")
            params = None  # next URL already contains all params
        return all_media

    async def get_media_insights(self, media_id: str, media_type: str) -> dict:
        metrics = MEDIA_METRICS_MAP.get(media_type, MEDIA_METRICS_MAP["IMAGE"])
        url = f"{self.base_url}/{media_id}/insights"
        params = {"metric": ",".join(metrics)}
        try:
            data = await self._request(url, params)
        except NotEnoughViewersError:
            return {}
        result = {}
        for item in data.get("data", []):
            name = item.get("name")
            values = item.get("values", [])
            if values:
                result[name] = values[0].get("value", 0)
            elif "total_value" in item:
                result[name] = item.get("total_value", {}).get("value", 0)
        return result

    # ────────────────────── Stories ──────────────────────

    async def get_active_stories(self) -> list[dict]:
        """Currently active (live) stories. Only available while stories are up."""
        url = f"{self.base_url}/{self.user_id}/stories"
        params = {"fields": "id,media_type,media_product_type,permalink,timestamp"}
        data = await self._request(url, params)
        return data.get("data", [])

    async def get_story_insights(self, media_id: str) -> dict:
        """Insights for a story. Returns {} when there are not enough viewers
        (IG error #10) or when the story expired (>24h) — never raises for
        these expected cases."""
        result: dict = {}
        url = f"{self.base_url}/{media_id}/insights"

        # Plain metrics
        try:
            data = await self._request(url, {"metric": ",".join(STORY_METRICS)})
            for item in data.get("data", []):
                name = item.get("name")
                if "total_value" in item:
                    result[name] = item.get("total_value", {}).get("value", 0)
                else:
                    values = item.get("values", [])
                    if values:
                        result[name] = values[0].get("value", 0)
        except NotEnoughViewersError:
            logger.info(f"Story {media_id}: not enough viewers for insights")
            return {}
        except InstagramAPIError as e:
            logger.warning(f"Story {media_id} insights failed: {e}")
            return {}

        # Navigation breakdown (separate request — needs `breakdown` param)
        try:
            nav_data = await self._request(url, {
                "metric": STORY_NAVIGATION_METRIC,
                "breakdown": STORY_NAVIGATION_BREAKDOWN,
            })
            for item in nav_data.get("data", []):
                if item.get("name") != STORY_NAVIGATION_METRIC:
                    continue
                breakdowns = item.get("total_value", {}).get("breakdowns", [])
                for br in breakdowns:
                    for res in br.get("results", []):
                        dim = res.get("dimension_values", [""])[0]
                        if dim in ("tap_forward", "tap_back", "tap_exit", "swipe_forward"):
                            result[dim] = res.get("value", 0)
        except (NotEnoughViewersError, InstagramAPIError) as e:
            logger.info(f"Story {media_id} navigation unavailable: {e}")

        return result


    # ────────────────────── Snapshots ──────────────────────

    async def collect_full_snapshot(
        self, since: int, until: int, days_to_fetch: Optional[set] = None
    ) -> dict:
        """Account info + daily insights.

        `days_to_fetch` (set of `date`) limits the expensive per-day requests
        for new-format metrics (views, accounts_engaged). None = all days.
        """
        user_info = await self.get_user_info()
        insights_raw = await self.get_account_insights(since, until)

        insights: dict[str, dict] = {}

        # Parse old format metrics (reach, follower_count) — values[] array
        for item in insights_raw.get("data", []):
            name = item.get("name")
            for val in item.get("values", []):
                end_time = val.get("end_time", "")
                if not end_time:
                    continue
                dt = parse_ig_timestamp(end_time)
                day = dt.strftime("%Y-%m-%d")
                insights.setdefault(day, {})[name] = val.get("value", 0)

        # New-format metrics (views, accounts_engaged) — one request per day,
        # parallelized in small batches
        current = datetime.fromtimestamp(since, tz=timezone.utc)
        end = datetime.fromtimestamp(until, tz=timezone.utc)
        days = []
        while current <= end:
            if days_to_fetch is None or current.date() in days_to_fetch:
                days.append(current)
            current += timedelta(days=1)

        semaphore = asyncio.Semaphore(5)

        async def fetch_day(day_dt: datetime):
            day_str = day_dt.strftime("%Y-%m-%d")
            day_start = int(day_dt.timestamp())
            day_end = int((day_dt + timedelta(days=1)).timestamp())
            async with semaphore:
                try:
                    data = await self.get_account_insights_new_metrics_day(day_start, day_end)
                    for item in data.get("data", []):
                        name = item.get("name")
                        value = item.get("total_value", {}).get("value", 0)
                        insights.setdefault(day_str, {})[name] = value
                except Exception as e:
                    logger.warning(f"Failed to get new metrics for {day_str}: {e}")

        if days:
            await asyncio.gather(*(fetch_day(d) for d in days))

        return {"user_info": user_info, "insights": insights}

    async def collect_posts_with_insights(
        self, date_from: datetime, date_to: datetime,
        insights_filter=None,
    ) -> tuple[list[dict], bool]:
        """Collect posts in [date_from, date_to] (naive UTC) with insights.

        `insights_filter(media_id, ts) -> bool` — optional predicate deciding
        whether insight metrics (reach/saved/shares/views/total_interactions)
        should be re-requested for a post. Posts that fail the filter get
        "skip_insights": True — their insight fields must NOT be overwritten
        in the DB (likes/comments still update for free from the media list).

        Returns (posts, is_partial). is_partial=True means some media could
        not be fetched (API errors) and the report may be incomplete.
        """
        partial = False

        # Paginate through all media (with retry/error handling via _request)
        all_media = []
        url: Optional[str] = f"{self.base_url}/{self.user_id}/media"
        params: Optional[dict] = {
            "fields": "id,media_type,media_product_type,caption,permalink,timestamp,like_count,comments_count",
            "limit": "100",
        }
        while url:
            try:
                data = await self._request(url, params)
            except InstagramAPIError as e:
                logger.error(f"Media pagination failed, using partial data: {e}")
                partial = True
                break
            all_media.extend(data.get("data", []))
            url = data.get("paging", {}).get("next")
            params = None  # next URL already contains params

        posts = []
        for media in all_media:
            ts_str = media.get("timestamp", "")
            if not ts_str:
                continue
            ts = parse_ig_timestamp(ts_str)
            if not (date_from <= ts <= date_to):
                continue

            media_type = _resolve_media_type(
                media.get("media_type", "IMAGE"),
                media.get("media_product_type", "")
            )

            fetch_insights = (
                insights_filter is None or insights_filter(media["id"], ts)
            )
            insights = {}
            if fetch_insights:
                try:
                    insights = await self.get_media_insights(media["id"], media_type)
                except InstagramAPIError as e:
                    logger.warning(f"Insights failed for {media['id']}: {e}")
                    partial = True

            posts.append({
                "instagram_media_id": media["id"],
                "media_type": media_type,
                "caption": media.get("caption", ""),
                "permalink": media.get("permalink", ""),
                "timestamp": ts,
                "likes": media.get("like_count", 0),
                "comments": media.get("comments_count", 0),
                "saved": insights.get("saved", 0),
                "shares": insights.get("shares", 0),
                "reach": insights.get("reach", 0),
                "total_interactions": insights.get("total_interactions", 0),
                "views": insights.get("views", 0),
                "skip_insights": not fetch_insights,
            })
        return posts, partial

    async def close(self):
        """Deprecated: use close_shared_session() instead."""
        pass
