import json
from datetime import date as _date

from utils.timezones import day_is_unfinalized


def _row_field(row, name, default=None):
    value = getattr(row, name, None)
    if value is not None:
        return value
    try:
        return row[name]
    except (TypeError, KeyError, IndexError):
        return default


def _follower_zero_is_provisional(row) -> bool:
    """A zero follower delta from a day Instagram still fills means «unknown».

    Instagram fills daily follower deltas with a 24-48h delay; a zero read
    from a young day is «not counted yet», not a fact. Rows without a date
    (tests, legacy objects) and non-zero deltas are never provisional.
    """
    if _row_field(row, "follower_count", 0):
        return False
    day = _row_field(row, "date")
    if isinstance(day, str):
        try:
            day = _date.fromisoformat(day)
        except ValueError:
            return False
    return day is not None and day_is_unfinalized(day)


def metric_is_present(row, metric: str) -> bool:
    """Treat old test/data objects without provenance as legacy-compatible."""
    if not hasattr(row, "metrics_present"):
        return True
    raw = row.metrics_present
    if raw is None:
        return False
    try:
        values = json.loads(raw)
    except (TypeError, ValueError):
        return False
    present = isinstance(values, list) and metric in values
    if present and metric == "follower_count" and _follower_zero_is_provisional(row):
        return False
    return present


def _metrics_present(row) -> set[str] | None:
    raw = getattr(row, "metrics_present", None)
    if raw is None:
        return None
    try:
        values = json.loads(raw)
    except (TypeError, ValueError):
        return None
    if not isinstance(values, list) or not all(isinstance(value, str) for value in values):
        return None
    return set(values)


def calculate_period_summary(stats_list: list) -> dict:
    if not stats_list:
        return {
            "followers_start": None, "followers_end": None,
            "followers_growth": None, "followers_growth_pct": None,
            "followers_growth_known": False,
            # Missing data is «н/д», never a fake zero.
            "reach_total": None, "views_total": None, "accounts_engaged_total": None,
            "reach_avg_daily": None, "views_avg_daily": None, "accounts_engaged_avg_daily": None,
        }

    last = stats_list[-1]
    followers_end = last.followers

    growth_known = all(metric_is_present(row, "follower_count") for row in stats_list)
    growth = sum(s.follower_count for s in stats_list) if growth_known else None
    followers_start = (
        followers_end - growth
        if growth is not None and followers_end is not None and followers_end >= 0
        else None
    )
    if followers_start is not None and followers_start < 0:
        followers_start = None
    growth_pct = (
        round(growth / followers_start * 100, 1)
        if growth is not None and followers_start is not None and followers_start > 0
        else None
    )

    reach_total = sum(s.reach for s in stats_list)
    views_total = sum(s.views for s in stats_list)
    accounts_engaged_total = sum(s.accounts_engaged for s in stats_list)
    days = len(stats_list) or 1

    return {
        "followers_start": followers_start,
        "followers_end": followers_end,
        "followers_growth": growth,
        "followers_growth_pct": growth_pct,
        "followers_growth_known": growth_known,
        "reach_total": reach_total,
        "views_total": views_total,
        "accounts_engaged_total": accounts_engaged_total,
        "reach_avg_daily": round(reach_total / days),
        "views_avg_daily": round(views_total / days),
        "accounts_engaged_avg_daily": round(accounts_engaged_total / days),
    }


def calculate_content_summary(posts_list: list) -> dict:
    if not posts_list:
        return {
            "total_posts": 0, "total_reels": 0, "total_videos": 0, "total_carousels": 0, "total_images": 0,
            "total_likes": 0, "total_comments": 0, "total_saves": 0, "total_shares": 0,
            "total_interactions": 0, "total_reach": 0,
            "component_interactions": 0, "interaction_gap": 0,
            "avg_likes": 0, "avg_comments": 0, "avg_reach": 0,
            "avg_saves": 0, "avg_shares": 0,
            "engagement_rate": 0.0,
            "legacy_unknown_insights": 0, "partial_insights_posts": 0,
            "missing_insight_metrics": {},
        }

    total = len(posts_list)
    reels = sum(1 for p in posts_list if p.media_type == "REELS")
    videos = sum(1 for p in posts_list if p.media_type == "VIDEO")
    carousels = sum(1 for p in posts_list if p.media_type == "CAROUSEL_ALBUM")
    images = total - reels - videos - carousels

    total_likes = sum(p.likes for p in posts_list)
    total_comments = sum(p.comments for p in posts_list)
    total_saves = sum(p.saved for p in posts_list)
    total_shares = sum(p.shares for p in posts_list)
    total_reach = sum(p.reach for p in posts_list)
    total_interactions = sum(p.total_interactions for p in posts_list)
    component_interactions = total_likes + total_comments + total_saves + total_shares

    expected_metrics = {
        "IMAGE": {"reach", "views", "saved", "shares", "total_interactions"},
        "VIDEO": {"reach", "saved", "shares", "total_interactions", "views"},
        "REELS": {"reach", "saved", "shares", "total_interactions", "views"},
        "CAROUSEL_ALBUM": {"reach", "views", "saved", "shares", "total_interactions"},
    }
    known_posts = [p for p in posts_list if getattr(p, "insights_present", None) is not None]
    missing_insight_metrics = {name: 0 for name in {m for group in expected_metrics.values() for m in group}}
    partial_insights_posts = 0
    for post in known_posts:
        # Corrupted provenance metadata must never crash a report (same
        # handling as stories in calculate_stories_summary).
        try:
            decoded = json.loads(post.insights_present)
        except (TypeError, ValueError):
            decoded = None
        present = (
            {value for value in decoded if isinstance(value, str)}
            if isinstance(decoded, list)
            else set()
        )
        missing = expected_metrics.get(post.media_type, expected_metrics["IMAGE"]) - present
        if missing:
            partial_insights_posts += 1
            for metric in missing:
                missing_insight_metrics[metric] += 1

    er = (total_interactions / total_reach * 100) if total_reach > 0 else 0.0

    return {
        "total_posts": total,
        "total_reels": reels,
        "total_videos": videos,
        "total_carousels": carousels,
        "total_images": images,
        "total_likes": total_likes,
        "total_comments": total_comments,
        "total_saves": total_saves,
        "total_shares": total_shares,
        "total_interactions": total_interactions,
        "component_interactions": component_interactions,
        "interaction_gap": total_interactions - component_interactions,
        "total_reach": total_reach,
        "avg_likes": round(total_likes / total) if total else 0,
        "avg_comments": round(total_comments / total) if total else 0,
        "avg_reach": round(total_reach / total) if total else 0,
        "avg_saves": round(total_saves / total) if total else 0,
        "avg_shares": round(total_shares / total) if total else 0,
        "engagement_rate": round(er, 1),
        "legacy_unknown_insights": len(posts_list) - len(known_posts),
        "partial_insights_posts": partial_insights_posts,
        "missing_insight_metrics": {
            metric: count for metric, count in missing_insight_metrics.items() if count
        },
    }


def score_post(post) -> float:
    raw = (post.likes * 1) + (post.comments * 2) + (post.saved * 3) + (post.shares * 4)
    if post.reach > 0:
        return round(raw / post.reach * 100, 2)
    return 0.0


def get_best_post(posts: list, content_type: str = None):
    filtered = posts
    if content_type:
        filtered = [p for p in posts if p.media_type == content_type]
    if not filtered:
        return None
    return max(filtered, key=score_post)


_POST_METRICS = {
    "reach", "likes", "comments", "saved", "shares", "views", "total_interactions",
}


def top_posts_by_metric(posts: list, metric: str = "total_interactions", limit: int = 3) -> list[dict]:
    """Return compact, deterministic post facts for reports and AI context."""
    if metric not in _POST_METRICS or limit <= 0:
        return []
    ranked = sorted(posts, key=lambda post: getattr(post, metric, 0) or 0, reverse=True)
    return [{
        "media_type": post.media_type,
        "caption": (post.caption or "").replace("\n", " ").strip()[:100]
        + ("…" if len((post.caption or "").strip()) > 100 else ""),
        "metric": metric,
        "value": getattr(post, metric, 0) or 0,
        "reach": post.reach or 0,
        "interactions": post.total_interactions or 0,
        "permalink": post.permalink or "",
    } for post in ranked[:limit]]


def compare_content_formats(posts: list) -> dict[str, dict]:
    """Aggregate post performance by format without mixing raw totals."""
    groups: dict[str, list] = {}
    for post in posts:
        groups.setdefault(post.media_type, []).append(post)
    result = {}
    for media_type, items in groups.items():
        total_reach = sum((p.reach or 0) for p in items)
        total_interactions = sum((p.total_interactions or 0) for p in items)
        result[media_type] = {
            "posts": len(items),
            "reach_total": total_reach,
            "reach_avg": round(total_reach / len(items)) if items else 0,
            "interactions_total": total_interactions,
            "interactions_avg": round(total_interactions / len(items)) if items else 0,
            "engagement_rate": round(total_interactions / total_reach * 100, 1) if total_reach else 0.0,
        }
    return result


def daily_peaks(stats_list: list, metric: str = "reach", limit: int = 3) -> list[dict]:
    """Return highest days for an account metric."""
    allowed = {"reach", "views", "accounts_engaged", "follower_count"}
    if metric not in allowed or limit <= 0:
        return []
    ranked = sorted(stats_list, key=lambda row: getattr(row, metric, 0) or 0, reverse=True)
    return [{"date": row.date.isoformat(), "metric": metric, "value": getattr(row, metric, 0) or 0}
            for row in ranked[:limit]]


def data_quality_summary(stats_list: list, expected_days: int | None = None) -> dict:
    """Describe completeness without interpreting missing metrics as zero."""
    partial_days = sum(1 for row in stats_list if getattr(row, "is_partial", False))
    available_days = len(stats_list)
    missing_days = max(expected_days - available_days, 0) if expected_days is not None else None
    core_metric_names = ("reach", "follower_count", "views", "accounts_engaged")
    extended_metric_names = (
        "likes", "comments", "saves", "shares", "replies", "total_interactions",
        "profile_views", "website_clicks", "reposts", "follows", "unfollows",
        "profile_links_taps", "online_followers",
    )
    parsed_presence = {id(row): _metrics_present(row) for row in stats_list}
    presence = {key: value for key, value in parsed_presence.items() if value is not None}
    legacy_unknown_days = sum(1 for value in parsed_presence.values() if value is None)
    invalid_metadata_days = sum(
        1 for row in stats_list
        if getattr(row, "metrics_present", None) is not None
        and parsed_presence[id(row)] is None
    )
    # Extended metrics are optional (account type / API support). Check only
    # those measured at least once; a metric the API never returns must not be
    # reported as «missing» on every day. Partial days are disclosed separately
    # (partial_days), so they do not add extended-metric noise.
    metric_names = list(core_metric_names) + [
        metric for metric in extended_metric_names
        if any(metric in values for values in presence.values())
    ]
    metric_missing_days = {}
    for metric in metric_names:
        missing = 0
        for row in stats_list:
            values = presence.get(id(row))
            if values is None or metric in values:
                continue
            if metric not in core_metric_names and getattr(row, "is_partial", False):
                continue
            missing += 1
        metric_missing_days[metric] = missing
    return {
        "available_days": available_days,
        "expected_days": expected_days,
        "missing_days": missing_days,
        "partial_days": partial_days,
        "legacy_unknown_days": legacy_unknown_days,
        "invalid_metadata_days": invalid_metadata_days,
        "metric_missing_days": metric_missing_days,
        "complete": (
            available_days > 0
            and (missing_days in (0, None))
            and partial_days == 0
            and legacy_unknown_days == 0
            and not any(metric_missing_days.values())
        ),
    }


def _trend_points(values) -> list[tuple[int, float]]:
    """(day_index, value) pairs; plain lists keep their natural indices."""
    if values and isinstance(values[0], (tuple, list)):
        return [(int(point[0]), float(point[1])) for point in values]
    return [(index, float(value)) for index, value in enumerate(values)]


def _theil_sen_slope(values) -> float:
    """Median slope across ALL point pairs of the period (Theil–Sen).

    Robust to outliers: one viral day cannot flip the picture, and every day
    of the period participates — no window cherry-picking. Points carry the
    real day index, so a skipped day cannot compress the time axis.
    """
    points = _trend_points(values)
    slopes = []
    n = len(points)
    for i in range(n):
        for j in range(i + 1, n):
            dx = points[j][0] - points[i][0]
            if dx:
                slopes.append((points[j][1] - points[i][1]) / dx)
    if not slopes:
        return 0.0
    slopes.sort()
    return slopes[len(slopes) // 2]


def _mann_kendall(values: list[float]) -> bool:
    """True when the direction is consistent across the whole period.

    Standard Mann-Kendall criterion: |Z| >= 1.96 (p < 0.05) — the movement is
    systematic rather than random zigzag.
    """
    n = len(values)
    if n < 4:
        return False
    s = 0
    for i in range(n):
        for j in range(i + 1, n):
            s += 1 if values[j] > values[i] else (-1 if values[j] < values[i] else 0)
    var_s = n * (n - 1) * (2 * n + 5) / 18.0
    if not var_s:
        return False
    return abs(s / (var_s ** 0.5)) >= 1.96


def trend_series(stats_list: list) -> list[tuple[int, float]]:
    """Whole-period daily series for the follower trend as (day_index, value).

    Net daily change (follows − unfollows) where every day measured the split;
    otherwise gross follows («новые подписчики»). Days without a measured
    value are skipped, but their index is preserved so the slope stays honest.
    """
    if not stats_list:
        return []
    net_known = all(
        metric_is_present(row, "follows") and metric_is_present(row, "unfollows")
        for row in stats_list
    )
    series = []
    for index, row in enumerate(stats_list):
        if net_known:
            series.append((index, (getattr(row, "follows", 0) or 0) - (getattr(row, "unfollows", 0) or 0)))
        elif metric_is_present(row, "follower_count"):
            series.append((index, getattr(row, "follower_count", 0) or 0))
    return series


def detect_trend(values: list) -> dict:
    """Whole-period trend: direction + per-day pace.

    «Растёт»/«падает» only when the movement is systematic across the whole
    period (Mann–Kendall); otherwise «стабильный». Accepts either plain values
    or (day_index, value) pairs. Returns
    {"direction": "growing"|"declining"|"stable", "slope": per day,
    "known": False when the series is too short}.
    """
    points = _trend_points(values)
    raw = [value for _index, value in points]
    if len(raw) < 3:
        return {"direction": "stable", "slope": 0.0, "known": False}
    slope = _theil_sen_slope(points)
    direction = "stable"
    if _mann_kendall(raw):
        if slope > 0:
            direction = "growing"
        elif slope < 0:
            direction = "declining"
    return {"direction": direction, "slope": slope, "known": True}


def trend_text(values: list) -> str:
    """The trend line as shown in reports: «растёт (+0,8/день)», «стабильный»."""
    from utils.formatters import format_decimal

    info = detect_trend(values)
    if not info["known"]:
        return "н/д"
    if info["direction"] == "stable":
        return "стабильный"
    words = {"growing": "растёт", "declining": "падает"}
    sign = "+" if info["slope"] >= 0 else ""
    return f"{words[info['direction']]} ({sign}{format_decimal(info['slope'], 1)}/день)"


STORY_NAVIGATION_METRICS = {"tap_forward", "tap_back", "tap_exit", "swipe_forward"}


def calculate_stories_summary(stories_list: list) -> dict:
    """Aggregate story metrics for a period."""
    if not stories_list:
        return {
            "total_stories": 0, "active_stories": 0,
            "total_views": 0, "total_reach": 0, "total_replies": 0,
            "total_shares": 0, "total_interactions": 0,
            "total_profile_activity": 0, "total_follows": 0,
            "avg_views": 0, "avg_reach": 0,
            "exit_rate": 0.0, "tap_forward_total": 0, "tap_back_total": 0,
            "tap_exit_total": 0, "swipe_forward_total": 0,
            "legacy_unknown_insights": 0, "partial_insights_stories": 0,
            "missing_insight_metrics": {},
        }

    total = len(stories_list)
    total_views = sum(s.views for s in stories_list)
    total_reach = sum(s.reach for s in stories_list)
    total_exits = sum(s.tap_exit + s.swipe_forward for s in stories_list)
    exit_rate = round(total_exits / total_views * 100, 1) if total_views > 0 else 0.0
    known_stories = [s for s in stories_list if getattr(s, "metrics_present", None) is not None]
    story_metric_names = {
        "views", "reach", "replies", "shares", "total_interactions", "profile_activity",
        "follows", "tap_forward", "tap_back", "tap_exit", "swipe_forward",
    }
    missing_insight_metrics = {name: 0 for name in story_metric_names}
    partial_insights_stories = 0
    for story in known_stories:
        try:
            present_names = set(json.loads(story.metrics_present))
        except (TypeError, ValueError):
            present_names = set()
        missing = story_metric_names - present_names
        if present_names & STORY_NAVIGATION_METRICS:
            # Instagram omits zero-valued navigation actions; if the block was
            # fetched at all, the missing actions are known zeros.
            missing -= STORY_NAVIGATION_METRICS
        if missing:
            partial_insights_stories += 1
            for metric in missing:
                missing_insight_metrics[metric] += 1

    return {
        "total_stories": total,
        "active_stories": sum(1 for s in stories_list if s.is_active),
        "total_views": total_views,
        "total_reach": total_reach,
        "total_replies": sum(s.replies for s in stories_list),
        "total_shares": sum(s.shares for s in stories_list),
        "total_interactions": sum(s.total_interactions for s in stories_list),
        "total_profile_activity": sum(s.profile_activity for s in stories_list),
        "total_follows": sum(s.follows for s in stories_list),
        "avg_views": round(total_views / total),
        "avg_reach": round(total_reach / total),
        "exit_rate": exit_rate,
        "tap_forward_total": sum(s.tap_forward for s in stories_list),
        "tap_back_total": sum(s.tap_back for s in stories_list),
        "tap_exit_total": sum(s.tap_exit for s in stories_list),
        "swipe_forward_total": sum(s.swipe_forward for s in stories_list),
        "legacy_unknown_insights": len(stories_list) - len(known_stories),
        "partial_insights_stories": partial_insights_stories,
        "missing_insight_metrics": {
            metric: count for metric, count in missing_insight_metrics.items() if count
        },
    }


def get_best_story(stories_list: list):
    """Best story by views (fallback: reach)."""
    if not stories_list:
        return None
    return max(stories_list, key=lambda s: (s.views, s.reach))


# ────────────── Extended metrics (live-API probe 2026-10) ──────────────


def _row_breakdowns(row) -> dict:
    raw = getattr(row, "breakdowns", None)
    if not raw:
        return {}
    try:
        decoded = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return decoded if isinstance(decoded, dict) else {}


def aggregate_breakdowns(stats_list: list) -> dict:
    """Sum JSON breakdown sets over daily rows.

    Returns {metric: {breakdown: {"dim": value}}} — the client's per-day shape
    summed over the period.
    """
    totals: dict = {}
    for row in stats_list:
        for metric, breakdowns in _row_breakdowns(row).items():
            if not isinstance(breakdowns, dict):
                continue
            for breakdown, values in breakdowns.items():
                if not isinstance(values, dict):
                    continue
                bucket = totals.setdefault(metric, {}).setdefault(breakdown, {})
                for key, value in values.items():
                    if isinstance(value, (int, float)):
                        bucket[key] = bucket.get(key, 0) + value
    return totals


def breakdown_coverage(stats_list: list, metric: str, breakdown: str) -> tuple[int, int] | None:
    """(days with this breakdown, measured days) — None when nothing measured.

    Breakdowns are collected best-effort: a failed request must not look like a
    real zero, so reports disclose partial coverage instead of silently
    undercounting the split.
    """
    measured = [row for row in stats_list if _metrics_present(row) is not None]
    if not measured:
        return None
    covered = 0
    for row in measured:
        entry = _row_breakdowns(row).get(metric, {}).get(breakdown)
        if isinstance(entry, dict) and entry:
            covered += 1
    return covered, len(measured)


def audience_split(breakdowns: dict, metric: str = "views") -> dict | None:
    """Follower / non-follower split for a metric.

    Returns {"followers": n, "non_followers": n} or None when the breakdown was
    never collected in the period. Labels are matched case-insensitively.
    """
    entry = breakdowns.get(metric, {}).get("follow_type")
    if not isinstance(entry, dict) or not entry:
        return None
    followers = non_followers = 0
    seen = False
    for key, value in entry.items():
        if not isinstance(value, (int, float)):
            continue
        upper = str(key).upper()
        if upper in {"NON_FOLLOWER", "NON_FOLLOWERS"}:
            non_followers += value
            seen = True
        elif upper in {"FOLLOWER", "FOLLOWERS"}:
            followers += value
            seen = True
    if not seen:
        return None
    return {"followers": followers, "non_followers": non_followers}


def product_type_split(breakdowns: dict, metric: str = "views") -> dict | None:
    """Views/reach split by media product type (POST/REEL/STORY/...).

    The API returns up to five buckets; only non-zero values are kept so the
    report never prints a meaningless zero row.
    """
    entry = breakdowns.get(metric, {}).get("media_product_type")
    if not isinstance(entry, dict) or not entry:
        return None
    result = {
        str(key).upper(): value for key, value in entry.items()
        if isinstance(value, (int, float)) and value
    }
    return result or None


def profile_activity_summary(posts: list) -> dict | None:
    """Profile activity produced by publications (visits, actions, breakdown)."""
    measured = [
        post for post in posts
        if getattr(post, "profile_visits", None) is not None
        or (getattr(post, "profile_activity", None) or 0)
    ]
    if not measured:
        return None
    breakdown: dict = {}
    for post in measured:
        raw = getattr(post, "profile_activity_breakdown", None)
        if not raw:
            continue
        try:
            decoded = json.loads(raw)
        except (TypeError, ValueError):
            continue
        if not isinstance(decoded, dict):
            continue
        for key, value in decoded.items():
            if isinstance(value, (int, float)) and value:
                breakdown[key] = breakdown.get(key, 0) + value
    visits = sum((getattr(post, "profile_visits", 0) or 0) for post in measured)
    activity = sum((getattr(post, "profile_activity", 0) or 0) for post in measured)
    if not visits and not activity:
        return None
    return {"visits": visits, "activity": activity, "breakdown": breakdown}


def contact_facts(total: int | None, buttons: dict | None) -> dict | None:
    """Consistent {"total", "buttons"} from any source (see contact_actions).

    The scalar total and the breakdown come from different API requests: when
    they disagree (a partial fetch) the scalar wins and the split is hidden
    rather than shown next to contradictory numbers.
    """
    clean = {
        key: value for key, value in (buttons or {}).items()
        if isinstance(value, (int, float)) and value
    }
    buttons_sum = sum(clean.values())
    if not total:
        total = buttons_sum
    elif buttons_sum and buttons_sum != total:
        clean = {}
    if not total and not clean:
        return None
    return {"total": total, "buttons": clean}


def contact_actions(stats_list: list) -> dict | None:
    """Aggregated contact-button taps with presence semantics.

    Returns {"total": n, "buttons": {label: n}} or None when the metric was
    never measured in the period (fresh zero != real zero).
    """
    measured = [row for row in stats_list if metric_is_present(row, "profile_links_taps")]
    entry = aggregate_breakdowns(stats_list).get("profile_links_taps", {}).get(
        "contact_button_type"
    )
    if not measured and not entry:
        return None
    total = sum(getattr(row, "profile_links_taps", 0) or 0 for row in measured)
    return contact_facts(total, entry)


def follow_activity(stats_list: list) -> dict | None:
    """Period follows/unfollows; None when never measured."""
    measured = [row for row in stats_list if metric_is_present(row, "follows")]
    if not measured:
        return None
    return {
        "follows": sum(getattr(row, "follows", 0) or 0 for row in measured),
        "unfollows": sum(getattr(row, "unfollows", 0) or 0 for row in measured),
    }


def reposts_total(stats_list: list) -> int | None:
    """Period reposts; None when never measured."""
    measured = [row for row in stats_list if metric_is_present(row, "reposts")]
    if not measured:
        return None
    return sum(getattr(row, "reposts", 0) or 0 for row in measured)


def best_post_by_followers(posts: list):
    """Post that brought the most new followers (only where measured).

    Reels never report follows, so the winner is the best of the feed posts
    that actually carry the metric.
    """
    measured = [p for p in posts if (getattr(p, "follows", None) or 0) > 0]
    if not measured:
        return None
    return max(measured, key=lambda p: p.follows)


def reels_watch_summary(posts: list) -> dict | None:
    """Average watch time across the reels that report it (milliseconds)."""
    measured = [p for p in posts if getattr(p, "avg_watch_time", None) is not None]
    if not measured:
        return None
    return {
        "reels_measured": len(measured),
        "avg_watch_time": round(sum(p.avg_watch_time for p in measured) / len(measured)),
        "total_watch_time": sum((getattr(p, "total_watch_time", 0) or 0) for p in measured),
    }


def summarize_audience_snapshots(snapshots: list, limit: int = 5) -> dict:
    """Compact demographics: {scope: {dimension: [(key, value), ...]}} top-`limit`."""
    result: dict = {}
    for snap in snapshots:
        try:
            payload = json.loads(snap.payload)
        except (TypeError, ValueError):
            continue
        if not isinstance(payload, list):
            continue
        items = sorted(
            (item for item in payload
             if isinstance(item, dict) and isinstance(item.get("value"), (int, float)) and item["value"]),
            key=lambda item: item["value"],
            reverse=True,
        )[:limit]
        if items:
            result.setdefault(snap.scope, {})[snap.dimension] = [
                (str(item.get("key", "")), item["value"]) for item in items
            ]
    return result


def online_followers_peak(stats_list: list) -> dict | None:
    """Weekdays with the highest average online-followers count.

    online_followers is a daily series (the API has no hourly breakdown), so
    this supports «best day to post» — not «best hour».
    """
    by_weekday: dict[int, list[int]] = {}
    for row in stats_list:
        if not metric_is_present(row, "online_followers"):
            continue
        value = getattr(row, "online_followers", None)
        if value is None:
            continue
        by_weekday.setdefault(row.date.weekday(), []).append(value)
    if not by_weekday:
        return None
    averages = {
        weekday: round(sum(values) / len(values))
        for weekday, values in by_weekday.items()
    }
    best = max(averages.values())
    best_weekdays = sorted(w for w, avg in averages.items() if avg >= best * 0.95)
    return {"averages": averages, "best_weekdays": best_weekdays}
