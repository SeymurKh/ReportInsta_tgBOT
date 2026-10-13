"""Unit tests for analytics/calculations.py — pure functions, no DB."""
from datetime import date, datetime

from analytics.calculations import (
    calculate_period_summary, calculate_content_summary,
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


def test_follower_trend_is_unknown_without_data():
    assert detect_trend([])["known"] is False


# ── calculate_period_summary ──

def test_period_summary_empty():
    s = calculate_period_summary([])
    assert s["followers_growth"] is None
    assert s["followers_growth_known"] is False
    # Empty period is «н/д» — a missing metric must never look like a real zero
    assert s["reach_total"] is None
    assert s["reach_avg_daily"] is None


def test_period_summary_values():
    stats = [FakeStats(date(2026, 9, 1) , followers=100, follower_count=10, reach=500),
             FakeStats(date(2026, 9, 2), followers=110, follower_count=10, reach=700)]
    s = calculate_period_summary(stats)
    assert s["followers_growth"] == 20
    assert s["followers_end"] == 110
    assert s["followers_start"] == 90
    assert s["reach_total"] == 1200
    assert s["reach_avg_daily"] == 600


def test_period_summary_does_not_report_percent_from_impossible_follower_baseline():
    stats = [FakeStats(date(2026, 9, 29), followers=0, follower_count=23)]
    s = calculate_period_summary(stats)
    assert s["followers_growth"] == 23
    assert s["followers_start"] is None
    assert s["followers_growth_pct"] is None


def test_period_summary_calculates_percent_from_valid_baseline():
    stats = [FakeStats(date(2026, 9, 29), followers=208, follower_count=23)]
    s = calculate_period_summary(stats)
    assert s["followers_start"] == 185
    assert s["followers_growth_pct"] == 12.4


# ── calculate_content_summary ──

def test_content_summary_empty():
    c = calculate_content_summary([])
    assert c["total_posts"] == 0
    assert c["engagement_rate"] == 0.0


def test_zero_follower_delta_on_unfinalized_day_is_not_a_fact():
    """Regression: Instagram fills daily follower deltas with a 24-48h delay —
    a zero read from a young day was trusted as a real «+0», producing false
    «growth slowing» signals and wrong daily digests."""
    from datetime import date, datetime, timedelta, timezone
    from types import SimpleNamespace

    from analytics.calculations import _follower_zero_is_provisional, metric_is_present

    today = datetime.now(timezone.utc).date()
    young_day = today - timedelta(days=1)  # closed less than 24h ago
    old_day = date(2026, 9, 1)
    provenance = '["follower_count", "reach"]'

    young_zero = SimpleNamespace(date=young_day, follower_count=0, metrics_present=provenance)
    assert not metric_is_present(young_zero, "follower_count")

    young_nonzero = SimpleNamespace(date=young_day, follower_count=5, metrics_present=provenance)
    assert metric_is_present(young_nonzero, "follower_count")

    old_zero = SimpleNamespace(date=old_day, follower_count=0, metrics_present=provenance)
    assert metric_is_present(old_zero, "follower_count")

    # dict rows (audit harness style, ISO date strings) follow the same rule
    assert _follower_zero_is_provisional({"date": young_day.isoformat(), "follower_count": 0})
    assert not _follower_zero_is_provisional({"date": old_day.isoformat(), "follower_count": 0})
    assert not _follower_zero_is_provisional({"date": young_day.isoformat(), "follower_count": 7})


def test_story_and_post_summaries_expose_exit_components_and_post_averages():
    from types import SimpleNamespace

    from analytics.calculations import calculate_content_summary, calculate_stories_summary

    posts = [
        SimpleNamespace(media_type="REELS", likes=10, comments=2, saved=4, shares=2,
                        reach=100, total_interactions=18, views=50),
        SimpleNamespace(media_type="IMAGE", likes=0, comments=0, saved=0, shares=0,
                        reach=50, total_interactions=0, views=0),
    ]
    content = calculate_content_summary(posts)
    assert content["avg_comments"] == 1
    assert content["avg_saves"] == 2
    assert content["avg_shares"] == 1

    story = SimpleNamespace(
        views=100, reach=80, replies=2, shares=1, total_interactions=3,
        profile_activity=1, follows=1,
        tap_forward=10, tap_back=2, tap_exit=3, swipe_forward=1, is_active=True,
    )
    stories = calculate_stories_summary([story])
    # «Доля выходов» must be backed by visible components: exits and swipes away
    assert stories["tap_exit_total"] == 3
    assert stories["swipe_forward_total"] == 1
    assert stories["exit_rate"] == 4.0  # (3 + 1) / 100 views


def test_content_summary_survives_corrupted_insights_metadata():
    """Regression: unguarded json.loads on insights_present crashed report
    generation when provenance metadata was corrupted (mirrors the stories
    fix from the previous audit)."""
    broken = FakePost("IMAGE")
    broken.insights_present = "{broken json"
    not_a_list = FakePost("REELS")
    not_a_list.insights_present = '"reach"'
    c = calculate_content_summary([broken, not_a_list])
    assert c["total_posts"] == 2
    assert c["partial_insights_posts"] == 2
    assert c["missing_insight_metrics"]["reach"] == 2


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


def test_content_summary_reports_interaction_source_difference():
    post = FakePost(likes=10, comments=2, saved=3, shares=1, total_interactions=20)
    summary = calculate_content_summary([post])

    assert summary["component_interactions"] == 16
    assert summary["interaction_gap"] == 4


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
    assert detect_trend([3])["known"] is False


def test_detect_trend_uses_the_whole_period():
    # strictly growing series -> растёт, with the per-day pace
    info = detect_trend([1, 2, 3, 4, 5, 6, 7, 8, 9, 10])
    assert info["direction"] == "growing"
    assert abs(info["slope"] - 1.0) < 0.01


def test_detect_trend_detects_decline():
    info = detect_trend([10, 9, 8, 7, 6, 5, 4, 3, 2, 1])
    assert info["direction"] == "declining"
    assert info["slope"] < 0


def test_detect_trend_flat_noise_is_stable():
    # zigzag in both directions across the period -> стабильный, no windows
    info = detect_trend([3, -1, 2, -2, 1, 0, -1, 2, -2, 1, 0, -1])
    assert info["direction"] == "stable"


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
    stats[0].metrics_present = '["follower_count", "reach", "views", "accounts_engaged"]'
    stats[1].metrics_present = '["follower_count", "reach", "views", "accounts_engaged"]'
    assert daily_peaks(stats)[0] == {
        "date": "2026-09-02", "metric": "reach", "value": 300,
    }
    quality = data_quality_summary(stats, expected_days=3)
    assert quality == {
        "available_days": 2, "expected_days": 3, "missing_days": 1,
        "partial_days": 1, "legacy_unknown_days": 0, "invalid_metadata_days": 0,
        "metric_missing_days": {
            "reach": 0, "follower_count": 0, "views": 0, "accounts_engaged": 0,
        },
        "complete": False,
    }


def test_contact_actions_agree_and_coverage_is_reported():
    from types import SimpleNamespace
    import json as _json
    from analytics.calculations import contact_actions, contact_facts

    def row(d, scalar, breakdown, present):
        return SimpleNamespace(
            date=d, metrics_present=_json.dumps(present),
            profile_links_taps=scalar, follows=0, unfollows=0, reposts=0,
            breakdowns=_json.dumps(breakdown) if breakdown else None,
        )

    ok = row(date(2026, 10, 1), 5,
             {"profile_links_taps": {"contact_button_type": {"DIRECTION": 3, "CALL": 2}}},
             ["profile_links_taps", "follows"])
    assert contact_actions([ok]) == {"total": 5, "buttons": {"DIRECTION": 3, "CALL": 2}}

    # Contradictory sources (partial fetch): scalar wins, split is hidden
    bad = row(date(2026, 10, 2), 5,
              {"profile_links_taps": {"contact_button_type": {"DIRECTION": 50}}},
              ["profile_links_taps", "follows"])
    assert contact_actions([bad]) == {"total": 5, "buttons": {}}

    # Breakdown-only day: the split becomes the source of truth
    only_bd = row(date(2026, 10, 3), 0,
                  {"profile_links_taps": {"contact_button_type": {"EMAIL": 4}}}, [])
    assert contact_actions([only_bd]) == {"total": 4, "buttons": {"EMAIL": 4}}

    unmeasured = row(date(2026, 10, 4), 0, None, [])
    assert contact_actions([unmeasured]) is None

    # contact_facts is the shared constructor for period totals (API windows)
    assert contact_facts(28, {"DIRECTION": 24, "CALL": 4}) == {
        "total": 28, "buttons": {"DIRECTION": 24, "CALL": 4},
    }
    assert contact_facts(28, {"DIRECTION": 800}) == {"total": 28, "buttons": {}}
    assert contact_facts(None, {"EMAIL": 3}) == {"total": 3, "buttons": {"EMAIL": 3}}
    assert contact_facts(None, {}) is None


def test_corrupt_metric_metadata_is_reported_as_unknown_not_crashed():
    row = FakeStats(date(2026, 9, 1))
    row.metrics_present = "{invalid-json"
    quality = data_quality_summary([row], expected_days=1)
    assert quality["legacy_unknown_days"] == 1
    assert quality["invalid_metadata_days"] == 1
    assert quality["complete"] is False


def test_extended_metric_aggregates_respect_presence_and_labels():
    from types import SimpleNamespace
    import json as _json
    from analytics.calculations import (
        aggregate_breakdowns, audience_split, contact_actions, follow_activity,
        reposts_total, best_post_by_followers, reels_watch_summary,
        online_followers_peak, summarize_audience_snapshots,
    )

    measured = SimpleNamespace(
        date=date(2026, 10, 1),
        metrics_present=_json.dumps([
            "profile_links_taps", "follows", "unfollows", "reposts", "online_followers",
        ]),
        profile_links_taps=9, follows=5, unfollows=2, reposts=3, online_followers=1500,
        breakdowns=_json.dumps({
            "views": {"follow_type": {"FOLLOWER": 30, "NON_FOLLOWER": 70}},
            "profile_links_taps": {"contact_button_type": {"DIRECTION": 6, "CALL": 3}},
            "follows_and_unfollows": {"follow_type": {"FOLLOWS": 5, "UNFOLLOWS": 2}},
        }),
    )
    unmeasured = SimpleNamespace(
        date=date(2026, 10, 2),
        metrics_present=_json.dumps(["reach"]),
        profile_links_taps=0, follows=0, unfollows=0, reposts=0, online_followers=0,
        breakdowns=None,
    )

    breakdowns = aggregate_breakdowns([measured, unmeasured])
    assert audience_split(breakdowns) == {"followers": 30, "non_followers": 70}
    assert audience_split({}, "reach") is None

    assert contact_actions([measured, unmeasured]) == {
        "total": 9, "buttons": {"DIRECTION": 6, "CALL": 3},
    }
    # Unmeasured-only periods must report «unknown», never a fresh zero.
    assert contact_actions([unmeasured]) is None
    assert follow_activity([unmeasured]) is None
    assert reposts_total([unmeasured]) is None

    assert follow_activity([measured, unmeasured]) == {"follows": 5, "unfollows": 2}
    assert reposts_total([measured, unmeasured]) == 3

    posts = [
        SimpleNamespace(caption="a", follows=4, avg_watch_time=5000, total_watch_time=100),
        SimpleNamespace(caption="b", follows=7, avg_watch_time=None, total_watch_time=None),
        SimpleNamespace(caption="c", follows=None, avg_watch_time=2000, total_watch_time=50),
    ]
    assert best_post_by_followers(posts).caption == "b"
    assert best_post_by_followers([posts[2]]) is None
    assert reels_watch_summary(posts) == {
        "reels_measured": 2, "avg_watch_time": 3500, "total_watch_time": 150,
    }

    # 2026-10-01 is a Thursday (weekday 3)
    peak = online_followers_peak([measured, unmeasured])
    assert peak["best_weekdays"] == [3]

    snap = SimpleNamespace(
        scope="follower", dimension="country",
        payload=_json.dumps([
            {"key": "AZ", "value": 40}, {"key": "RU", "value": 10}, {"key": "US", "value": 0},
        ]),
    )
    summary = summarize_audience_snapshots([snap])
    assert summary["follower"]["country"] == [("AZ", 40), ("RU", 10)]
