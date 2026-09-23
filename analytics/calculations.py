from datetime import date
from typing import Optional


def calculate_growth(current: int, previous: int) -> tuple[int, Optional[float]]:
    absolute = current - previous
    if previous == 0:
        return absolute, None
    pct = ((current - previous) / previous) * 100
    return absolute, round(pct, 1)


def calculate_period_summary(stats_list: list) -> dict:
    if not stats_list:
        return {
            "followers_start": 0, "followers_end": 0,
            "followers_growth": 0, "followers_growth_pct": 0.0,
            "reach_total": 0, "views_total": 0, "accounts_engaged_total": 0,
            "reach_avg_daily": 0, "views_avg_daily": 0, "accounts_engaged_avg_daily": 0,
        }

    last = stats_list[-1]
    followers_end = last.followers

    # Growth = sum of daily follower_count changes from insights
    growth = sum(s.follower_count for s in stats_list)
    followers_start = followers_end - growth
    growth_pct = round(growth / followers_start * 100, 1) if followers_start != 0 else 0.0

    reach_total = sum(s.reach for s in stats_list)
    views_total = sum(s.views for s in stats_list)
    accounts_engaged_total = sum(s.accounts_engaged for s in stats_list)
    days = len(stats_list) or 1

    return {
        "followers_start": followers_start,
        "followers_end": followers_end,
        "followers_growth": growth,
        "followers_growth_pct": growth_pct,
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
            "avg_likes": 0, "avg_comments": 0, "avg_reach": 0,
            "engagement_rate": 0.0,
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
        "avg_likes": round(total_likes / total) if total else 0,
        "avg_comments": round(total_comments / total) if total else 0,
        "avg_reach": round(total_reach / total) if total else 0,
        "engagement_rate": round(er, 1),
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


def get_worst_post(posts: list):
    if not posts:
        return None
    return min(posts, key=score_post)


def detect_trend(stats_list: list, metric: str = "followers", window: int = 7) -> str:
    if len(stats_list) < window:
        return "stable"
    values = [getattr(s, metric, 0) for s in stats_list]
    recent_avg = sum(values[-window:]) / window
    overall_avg = sum(values) / len(values)
    diff_pct = ((recent_avg - overall_avg) / overall_avg * 100) if overall_avg else 0

    if diff_pct > 5:
        return "growing"
    elif diff_pct < -5:
        return "declining"
    return "stable"


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
        }

    total = len(stories_list)
    total_views = sum(s.views for s in stories_list)
    total_reach = sum(s.reach for s in stories_list)
    total_exits = sum(s.tap_exit + s.swipe_forward for s in stories_list)
    exit_rate = round(total_exits / total_views * 100, 1) if total_views > 0 else 0.0

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
    }


def get_best_story(stories_list: list):
    """Best story by views (fallback: reach)."""
    if not stories_list:
        return None
    return max(stories_list, key=lambda s: (s.views, s.reach))


def compare_accounts(accounts_data: list) -> dict:
    if not accounts_data:
        return {}

    best_growth = max(accounts_data, key=lambda a: a.get("growth_pct", 0))
    best_er = max(accounts_data, key=lambda a: a.get("engagement_rate", 0))
    best_reach = max(accounts_data, key=lambda a: a.get("reach_total", 0))

    return {
        "best_growth": {"account": best_growth["name"], "value": best_growth.get("growth_pct", 0)},
        "best_engagement": {"account": best_er["name"], "value": best_er.get("engagement_rate", 0)},
        "best_reach": {"account": best_reach["name"], "value": best_reach.get("reach_total", 0)},
    }