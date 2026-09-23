"""Unit tests for analytics/calculations.py — pure functions, no DB."""
from datetime import date, datetime

from analytics.calculations import (
    calculate_growth, calculate_period_summary, calculate_content_summary,
    calculate_stories_summary, get_best_story, detect_trend, score_post,
    top_posts_by_metric, compare_content_formats, daily_peaks, data_quality_summary,
)


class FakeStats:
    def __init__(self, d, followers=100, follower_count=5, reach=1000, views=2000, accounts_engaged=300):
        self.date = d
        self.followers = followers
        self.follower_count = follower_count
        self.reach = reach
        self.views = views
        self.accounts_engaged = accounts_engaged


class FakePost:
    def __init__(self, media_type="IMAGE", likes=10, comments=2, saved=3, shares=1,
                 reach=100, total_interactions=16):
        self.media_type = media_type
        self.likes = likes
        self.comments = comments
        self.saved = saved
        self.shares = shares
        self.reach = reach
        self.total_interactions = total_interactions
        self.caption = "test"
        self.permalink = "https://example.com"
        self.timestamp = datetime(2026, 9, 1)


class FakeStory:
    def __init__(self, views=100, reach=80, replies=2, shares=1, tap_exit=10,
                 swipe_forward=5, tap_forward=40, tap_back=8, is_active=False,
                 profile_activity=3, follows=1, total_interactions=4):
        self.views = views
        self.reach = reach
        self.replies = replies
        self.shares = shares
        self.tap_exit = tap_exit
        self.swipe_forward = swipe_forward
        self.tap_forward = tap_forward
        self.tap_back = tap_back
        self.is_active = is_active
        self.profile_activity = profile_activity
        self.follows = follows
        self.total_interactions = total_interactions
        self.media_type = "IMAGE"
        self.permalink = ""
        self.timestamp = datetime(2026, 9, 1, 12, 0)


# ── calculate_growth ──

def test_growth_normal():
    assert calculate_growth(110, 100) == (10, 10.0)


def test_growth_zero_previous():
    assert calculate_growth(50, 0) == (50, None)


# ── calculate_period_summary ──

def test_period_summary_empty():
    s = calculate_period_summary([])
    assert s["followers_growth"] == 0
    assert s["reach_total"] == 0


def test_period_summary_values():
    stats = [FakeStats(date(2026, 9, 1) , followers=100, follower_count=10, reach=500),
             FakeStats(date(2026, 9, 2), followers=110, follower_count=10, reach=700)]
    s = calculate_period_summary(stats)
    assert s["followers_growth"] == 20
    assert s["followers_end"] == 110
    assert s["followers_start"] == 90
    assert s["reach_total"] == 1200
    assert s["reach_avg_daily"] == 600


# ── calculate_content_summary ──

def test_content_summary_empty():
    c = calculate_content_summary([])
    assert c["total_posts"] == 0
    assert c["engagement_rate"] == 0.0


def test_content_summary_counts_types():
    posts = [FakePost("REELS"), FakePost("VIDEO"), FakePost("CAROUSEL_ALBUM"), FakePost("IMAGE")]
    c = calculate_content_summary(posts)
    assert c["total_posts"] == 4
    assert c["total_reels"] == 1
    assert c["total_videos"] == 1
    assert c["total_carousels"] == 1
    assert c["total_images"] == 1
    # ER = total_interactions / reach * 100 = 64 / 400 * 100 = 16.0
    assert c["engagement_rate"] == 16.0


def test_content_summary_er_zero_reach():
    posts = [FakePost(reach=0)]
    c = calculate_content_summary(posts)
    assert c["engagement_rate"] == 0.0


# ── stories ──

def test_stories_summary_empty():
    s = calculate_stories_summary([])
    assert s["total_stories"] == 0
    assert s["exit_rate"] == 0.0


def test_stories_summary_values():
    stories = [FakeStory(views=100, tap_exit=10, swipe_forward=5),
               FakeStory(views=200, tap_exit=20, swipe_forward=10, is_active=True)]
    s = calculate_stories_summary(stories)
    assert s["total_stories"] == 2
    assert s["active_stories"] == 1
    assert s["total_views"] == 300
    assert s["avg_views"] == 150
    # exits = 15 + 30 = 45; 45/300 = 15%
    assert s["exit_rate"] == 15.0


def test_get_best_story():
    stories = [FakeStory(views=100), FakeStory(views=500), FakeStory(views=300)]
    best = get_best_story(stories)
    assert best.views == 500


def test_get_best_story_empty():
    assert get_best_story([]) is None


# ── score_post / detect_trend ──

def test_score_post():
    p = FakePost(likes=10, comments=2, saved=3, shares=1, reach=100)
    # raw = 10 + 4 + 9 + 4 = 27; 27/100*100 = 27.0
    assert score_post(p) == 27.0


def test_detect_trend_short_list():
    assert detect_trend([FakeStats(date(2026, 9, 1))], "followers") == "stable"


def test_detect_trend_uses_two_halves_for_week():
    stats = [
        FakeStats(date(2026, 9, day), follower_count=1)
        for day in range(1, 5)
    ] + [
        FakeStats(date(2026, 9, day), follower_count=10)
        for day in range(5, 8)
    ]
    assert detect_trend(stats, "follower_count") == "growing"


def test_detect_trend_detects_decline_for_short_period():
    stats = [
        FakeStats(date(2026, 9, day), follower_count=10)
        for day in range(1, 5)
    ] + [
        FakeStats(date(2026, 9, day), follower_count=1)
        for day in range(5, 8)
    ]
    assert detect_trend(stats, "follower_count") == "declining"


def test_top_posts_and_format_comparison_are_data_grounded():
    posts = [
        FakePost("REELS", reach=200, total_interactions=30),
        FakePost("IMAGE", reach=100, total_interactions=10),
    ]
    assert top_posts_by_metric(posts)[0]["media_type"] == "REELS"
    formats = compare_content_formats(posts)
    assert formats["REELS"]["reach_avg"] == 200
    assert formats["IMAGE"]["engagement_rate"] == 10.0


def test_daily_peaks_and_quality_summary():
    stats = [
        FakeStats(date(2026, 9, 1), reach=100),
        FakeStats(date(2026, 9, 2), reach=300),
    ]
    stats[0].is_partial = False
    stats[1].is_partial = True
    assert daily_peaks(stats)[0] == {
        "date": "2026-09-02", "metric": "reach", "value": 300,
    }
    quality = data_quality_summary(stats, expected_days=3)
    assert quality == {
        "available_days": 2, "expected_days": 3, "missing_days": 1,
        "partial_days": 1, "complete": False,
    }
