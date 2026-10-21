"""推薦品質・公開範囲・期間境界を検証する、合成データだけのテスト。"""

from datetime import datetime, timedelta, timezone
import math

from campus_events.db import get_db
from campus_events.recommendations import get_recommendations
from tests.support import (
    AppTestCase,
    add_event,
    add_favorite,
    add_registration,
    add_view,
)


NOW = datetime(2030, 1, 15, 12, 0)
PAST = "2030-01-01T10:00"
FUTURE = "2030-02-01T10:00"
RECENT = "2030-01-14"
CREATED = "2030-01-14T12:00:00"


def text_event(text, **overrides):
    values = {
        "title": text,
        "description": text,
        "category": text,
        "start_at": FUTURE,
        "created_at": "2029-12-01T10:00",
        "updated_at": "2029-12-01T10:00",
    }
    values.update(overrides)
    return add_event(**values)


class RecommendationTests(AppTestCase):
    def test_weighted_cosine_uses_additive_favorite_and_capped_view_days(self):
        # a と z は文字2〜4-gramを共有しないため、文書ベクトルは直交する。
        source_a = text_event("aaaa", start_at=PAST)
        source_z = text_event("zzzz", start_at=PAST)
        target_a = text_event("aaaa")
        target_z = text_event("zzzz")
        add_favorite("student-a", source_a, CREATED)
        add_view("student-a", source_a, RECENT)
        for days_ago in range(1, 6):
            day = (NOW.date() - timedelta(days=days_ago)).isoformat()
            add_view("student-a", source_z, day)

        result = get_recommendations("student-a", now=NOW)
        self.assertEqual(result["mode"], "personalized")
        self.assertEqual(result["history_event_count"], 2)
        self.assertEqual(result["favorite_event_count"], 1)
        self.assertEqual(result["profile_event_count"], 2)
        self.assertEqual([item["event"]["id"] for item in result["items"]], [target_a, target_z])
        # a はお気に入り5+閲覧1=6、z は5日閲覧しても上限3。
        self.assertAlmostEqual(result["items"][0]["similarity"], 6 / math.sqrt(45), places=10)
        self.assertAlmostEqual(result["items"][1]["similarity"], 3 / math.sqrt(45), places=10)

    def test_japanese_and_fullwidth_english_are_normalized(self):
        source = text_event("ＰＹＴＨＯＮデータ分析勉強会", start_at=PAST)
        expected = text_event("pythonデータ分析勉強会")
        text_event("地域サッカー大会")
        add_favorite("student-a", source, CREATED)

        result = get_recommendations("student-a", now=NOW)
        self.assertEqual(result["mode"], "personalized")
        self.assertEqual(result["items"][0]["event"]["id"], expected)
        self.assertAlmostEqual(result["items"][0]["similarity"], 1.0, places=10)

    def test_only_unseen_future_approved_available_events_are_candidates(self):
        viewed = text_event("データ分析研究会")
        favorite = text_event("データ分析研究会")
        text_event("データ分析研究会", status="pending")
        text_event("データ分析研究会", status="rejected")
        text_event("データ分析研究会", start_at=PAST)
        text_event("データ分析研究会", start_at="2030-01-15T12:00")
        full = text_event("データ分析研究会", capacity=1)
        add_registration(full, created_at=CREATED)
        available = text_event("データ分析研究会", capacity=2)
        add_registration(available, created_at=CREATED)
        external = text_event("データ分析研究会", registration_mode="external", capacity=1)
        add_registration(external, created_at=CREATED)
        add_view("student-a", viewed, RECENT)
        add_favorite("student-a", favorite, CREATED)

        result = get_recommendations("student-a", now=NOW)
        candidates = {item["event"]["id"]: item["event"] for item in result["items"]}
        self.assertEqual(set(candidates), {available, external})
        self.assertEqual(candidates[available]["registered"], 1)

    def test_hidden_events_and_other_visitors_do_not_build_this_profile(self):
        pending = text_event("aaaa", start_at=PAST, status="pending")
        rejected = text_event("aaaa", start_at=PAST, status="rejected")
        other = text_event("aaaa", start_at=PAST)
        text_event("aaaa")
        add_view("student-a", pending, RECENT)
        add_favorite("student-a", rejected, CREATED)
        add_favorite("someone-else", other, CREATED)

        result = get_recommendations("student-a", now=NOW)
        self.assertEqual(result["mode"], "popular")
        self.assertEqual(result["profile_event_count"], 0)
        self.assertEqual(result["history_event_count"], 0)
        self.assertEqual(result["favorite_event_count"], 0)
        self.assertTrue(all(item["similarity"] is None for item in result["items"]))

    def test_last_n_calendar_days_include_boundary_but_exclude_future(self):
        self.app.config["DS_HISTORY_DAYS"] = 3
        boundary = text_event("aaaa", start_at=PAST)
        today = text_event("aaaa", start_at=PAST)
        expired = text_event("aaaa", start_at=PAST)
        future_day = text_event("aaaa", start_at=PAST)
        later_today = text_event("aaaa", start_at=PAST)
        future_favorite = text_event("aaaa", start_at=PAST)
        text_event("aaaa")
        add_view("student-a", boundary, "2030-01-13")
        add_view("student-a", today, "2030-01-15")
        add_view("student-a", expired, "2030-01-12")
        add_view("student-a", future_day, "2030-01-16")
        add_view(
            "student-a", later_today, "2030-01-15",
            first_viewed_at="2030-01-15T13:00:00",
            last_viewed_at="2030-01-15T13:00:00",
        )
        add_favorite("student-a", future_favorite, "2030-01-16T12:00:00")

        result = get_recommendations("student-a", now=NOW)
        self.assertEqual(result["profile_event_count"], 2)
        self.assertEqual(result["history_event_count"], 2)
        self.assertEqual(result["favorite_event_count"], 0)

    def test_cold_start_uses_observed_popularity_with_capped_repeated_views(self):
        no_signal = text_event("aaaa", start_at="2030-01-16T10:00")
        viewed = text_event("aaaa")
        favored = text_event("aaaa")
        registered = text_event("aaaa")
        external = text_event("aaaa", registration_mode="external")
        for days_ago in range(1, 11):
            day = (NOW.date() - timedelta(days=days_ago)).isoformat()
            add_view("one-repeated-visitor", viewed, day)
        add_favorite("someone-else", favored, CREATED)
        for _ in range(4):
            add_registration(registered, created_at=CREATED)
        for _ in range(10):
            add_registration(external, created_at=CREATED)
        add_favorite("external-interested-browser", external, CREATED)

        result = get_recommendations("new-student", now=NOW)
        self.assertEqual(result["mode"], "popular")
        ids = [item["event"]["id"] for item in result["items"]]
        self.assertEqual(ids, [favored, external, registered, viewed, no_signal])
        self.assertTrue(all(item["similarity"] is None for item in result["items"]))
        self.assertNotIn("サイト内申込", result["items"][1]["reason"])
        self.assertIn("開催日時の近い順", result["items"][4]["reason"])

    def test_no_overlap_uses_fallback_without_private_text_features(self):
        source = text_event("aaaa", start_at=PAST)
        candidate = text_event(
            "zzzz",
            contact_email="aaaa@example.invalid",
            note_to_office="aaaa",
            review_comment="aaaa",
            receipt_code="AAAA",
        )
        add_favorite("student-a", source, CREATED)

        result = get_recommendations("student-a", now=NOW)
        self.assertEqual(result["mode"], "popular")
        self.assertEqual(result["items"][0]["event"]["id"], candidate)
        self.assertIsNone(result["items"][0]["similarity"])
        self.assertIn("内容の近い", result["items"][0]["reason"])

    def test_empty_text_falls_back_and_empty_candidates_are_explicit(self):
        source = text_event("", start_at=PAST)
        candidate = text_event("")
        add_favorite("student-a", source, CREATED)
        result = get_recommendations("student-a", now=NOW)
        self.assertEqual(result["mode"], "popular")
        self.assertIsNone(result["items"][0]["similarity"])

        add_favorite("student-a", candidate, CREATED)
        empty = get_recommendations("student-a", now=NOW)
        self.assertEqual(empty["mode"], "empty")
        self.assertEqual(empty["items"], [])
        self.assertEqual(empty["profile_event_count"], 2)

    def test_aware_time_is_converted_to_jst_before_start_boundary(self):
        started = text_event("aaaa", start_at="2030-01-15T12:00")
        future = text_event("aaaa", start_at="2030-01-15T12:01")
        now_utc = datetime(2030, 1, 15, 3, 0, tzinfo=timezone.utc)
        result = get_recommendations(None, now=now_utc)
        self.assertEqual([item["event"]["id"] for item in result["items"]], [future])
        self.assertNotIn(started, [item["event"]["id"] for item in result["items"]])

    def test_limits_are_bounded_and_ties_are_deterministic(self):
        ids = [text_event("aaaa") for _ in range(55)]
        for limit, expected_count in (("bad", 6), (-2, 1), ("2", 2), (1000, 50)):
            with self.subTest(limit=limit):
                result = get_recommendations(None, limit=limit, now=NOW)
                self.assertEqual(len(result["items"]), expected_count)
                self.assertEqual(
                    [item["event"]["id"] for item in result["items"]], ids[:expected_count]
                )

    def test_removing_favorites_changes_next_result_without_stale_cache(self):
        source = text_event("aaaa", start_at=PAST)
        text_event("aaaa")
        add_favorite("student-a", source, CREATED)
        self.assertEqual(get_recommendations("student-a", now=NOW)["mode"], "personalized")
        get_db().execute("DELETE FROM event_favorites WHERE visitor_id = ?", ("student-a",))
        get_db().commit()
        result = get_recommendations("student-a", now=NOW)
        self.assertEqual(result["mode"], "popular")
        self.assertEqual(result["favorite_event_count"], 0)
