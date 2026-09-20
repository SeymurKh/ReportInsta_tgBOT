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


def _resolve_media_type(raw_type: str, product_type: str) -> str:
    """Determine actual media type using media_product_type from Instagram API."""
    if raw_type == "VIDEO" and product_type == "REELS":
        return "REELS"
    return raw_type  # IMAGE, VIDEO, CAROUSEL_ALBUM

ACCOUNT_METRICS_OLD = ["reach", "follower_count"]
ACCOUNT_METRICS_NEW = ["views", "accounts_engaged"]


_shared_session: aiohttp.ClientSession | None = None


async def _get_shared_session() -> aiohttp.ClientSession:
    global _shared_session
    if _shared_session is None or _shared_session.closed:
        _shared_session = aiohttp.ClientSession()
    return _shared_session


async def close_shared_session():
    global _shared_session
    if _shared_session and not _shared_session.closed:
        await _shared_session.close()
        _shared_session = None


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
                    data = await resp.json()
                    if resp.status == 200:
                        return data
                    error = data.get("error", {})
                    code = error.get("code", 0)
                    msg = error.get("message", "Unknown")
                    logger.error(f"IG API [{code}]: {msg}")

                    if code == 4:
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
        url = f"{self.base_url}/{self.user_id}/insights"

        # Request 1: Old format metrics (reach, follower_count)
        params_old = {
            "metric": ",".join(ACCOUNT_METRICS_OLD),
            "period": "day",
            "since": str(since),
            "until": str(until),
        }

        # Request 2: New format metrics (views, accounts_engaged)
        params_new = {
            "metric": ",".join(ACCOUNT_METRICS_NEW),
            "period": "day",
            "since": str(since),
            "until": str(until),
            "metric_type": "total_value",
        }

        old_data, new_data = await asyncio.gather(
            self._request(url, params_old),
            self._request(url, params_new),
        )

        # Merge results - combine both data arrays
        merged = list(old_data.get("data", [])) + list(new_data.get("data", []))
        return {"data": merged}

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
            paging = data.get("paging", {})
            url = paging.get("next")
            params = None
        return all_media

    async def get_media_insights(self, media_id: str, media_type: str) -> dict:
        metrics = MEDIA_METRICS_MAP.get(media_type, MEDIA_METRICS_MAP["IMAGE"])
        url = f"{self.base_url}/{media_id}/insights"
        params = {"metric": ",".join(metrics)}
        data = await self._request(url, params)
        result = {}
        for item in data.get("data", []):
            name = item.get("name")
            values = item.get("values", [])
            if values:
                result[name] = values[0].get("value", 0)
        return result

    async def collect_full_snapshot(self, since: int, until: int) -> dict:
        user_info = await self.get_user_info()
        insights_raw = await self.get_account_insights(since, until)

        insights = {}

        # Parse old format metrics (reach, follower_count) - values[] array
        for item in insights_raw.get("data", []):
            name = item.get("name")
            # Old format: values[] array
            if "values" in item:
                for val in item.get("values", []):
                    end_time = val.get("end_time", "")
                    if end_time:
                        dt = datetime.fromisoformat(end_time.replace("+0000", "+00:00"))
                        day = dt.strftime("%Y-%m-%d")
                        if day not in insights:
                            insights[day] = {}
                        insights[day][name] = val.get("value", 0)
            # New format: total_value.value (single day value)
            elif "total_value" in item:
                total = item.get("total_value", {})
                value = total.get("value", 0)
                # For period=day, we need to determine which day this is
                # Since we request per-day for new metrics, we use the since date
                # But this is aggregated for the whole period
                # We'll handle per-day separately below

        # For new format metrics (views, accounts_engaged), request per-day
        current = datetime.fromtimestamp(since, tz=timezone.utc)
        end = datetime.fromtimestamp(until, tz=timezone.utc)
        while current <= end:
            day_str = current.strftime("%Y-%m-%d")
            day_start = int(current.timestamp())
            day_end = int((current + timedelta(days=1)).timestamp())

            try:
                url = f"{self.base_url}/{self.user_id}/insights"
                params = {
                    "metric": ",".join(ACCOUNT_METRICS_NEW),
                    "period": "day",
                    "since": str(day_start),
                    "until": str(day_end),
                    "metric_type": "total_value",
                }
                data = await self._request(url, params)
                for item in data.get("data", []):
                    name = item.get("name")
                    value = item.get("total_value", {}).get("value", 0)
                    if day_str not in insights:
                        insights[day_str] = {}
                    insights[day_str][name] = value
            except Exception as e:
                logger.warning(f"Failed to get new metrics for {day_str}: {e}")

            current += timedelta(days=1)

        return {"user_info": user_info, "insights": insights}

    async def collect_posts_with_insights(
        self, date_from: datetime, date_to: datetime
    ) -> list[dict]:
        # Paginate through all media
        all_media = []
        url = f"{self.base_url}/{self.user_id}/media"
        params = {
            "fields": "id,media_type,media_product_type,caption,permalink,timestamp,like_count,comments_count",
            "limit": "100",
        }
        while url:
            params["access_token"] = self.access_token
            session = await self._get_session()
            async with session.get(url, params=params) as resp:
                data = await resp.json()
                if resp.status != 200:
                    break
                all_media.extend(data.get("data", []))
                url = data.get("paging", {}).get("next")
                params = {}  # next URL already contains params

        posts = []
        for media in all_media:
            ts_str = media.get("timestamp", "")
            if not ts_str:
                continue
            ts = datetime.fromisoformat(ts_str.replace("+0000", "+00:00"))
            if not (date_from <= ts <= date_to):
                continue

            media_type = _resolve_media_type(
                media.get("media_type", "IMAGE"),
                media.get("media_product_type", "")
            )
            insights = {}
            try:
                insights = await self.get_media_insights(media["id"], media_type)
            except InstagramAPIError as e:
                logger.warning(f"Insights failed for {media['id']}: {e}")

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
            })
        return posts

    async def close(self):
        """Deprecated: use close_shared_session() instead."""
        pass


class InstagramAPIError(Exception):
    pass


class TokenExpiredError(InstagramAPIError):
    pass