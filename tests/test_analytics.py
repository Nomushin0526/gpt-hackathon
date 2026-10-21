"""集計で起こりやすい重複計上、母数、欠測、期間の境界を検証する。"""

from datetime import date
import unittest

from campus_events.analytics import build_dashboard, csv_safe
from campus_events.events import CATEGORIES
from tests.support import (
    AppTestCase, add_event, add_favorite, add_registration, add_view,
)


class AnalyticsTests(AppTestCase):
    start = date(2026, 10, 1)
    end = date(2026, 10, 31)

    def dashboard(self):
        return build_dashboard(self.start, self.end)

    def test_independent_aggregates_do_not_multiply_rows(self):
        event_id = add_event(start_at="2026-10-05T10:00", capacity=20)
        add_registration(event_id)
        add_registration(event_id)
        add_view("browser-a", event_id, view_date="2026-09-20")
        add_view("browser-a", event_id, view_date="2026-09-21")
        add_view("browser-b", event_id, view_date="2026-09-21")
        add_favorite("browser-a", event_id)
        add_favorite("browser-b", event_id)

        result = self.dashboard()
        self.assertEqual(result["summary"]["registration_count"], 2)
        self.assertEqual(result["summary"]["view_count"], 3)
        self.assertEqual(result["summary"]["unique_visitor_count"], 2)
        self.assertEqual(result["summary"]["favorite_count"], 2)
        self.assertEqual(result["ranking"][0]["occupancy_percent"], 10.0)

    def test_zero_events_are_in_denominator_and_external_counts_are_unknown(self):
        site_popular = add_event(start_at="2026-10-05T10:00", capacity=10, category=CATEGORIES[0])
        site_empty = add_event(start_at="2026-10-06T10:00", capacity=90, category=CATEGORIES[0])
        site_unlimited = add_event(start_at="2026-10-07T10:00", capacity=None, category=CATEGORIES[0])
        external = add_event(
            start_at="2026-10-08T10:00", registration_mode="external", capacity=100,
            category=CATEGORIES[1],
        )
        free = add_event(
            start_at="2026-10-09T10:00", registration_mode="none", capacity=300,
            category=CATEGORIES[1],
        )
        for _ in range(5):
            add_registration(site_popular)
        for _ in range(4):
            add_registration(site_unlimited)
        # 申込方式を途中で変更した過去データがあっても、現在 external は除外する。
        add_registration(external)

        result = self.dashboard()
        summary = result["summary"]
        self.assertEqual(summary["event_count"], 5)
        self.assertEqual(summary["site_event_count"], 3)
        self.assertEqual(summary["registration_count"], 9)
        self.assertEqual(summary["average_registrations"], 3.0)
        # (5 + 0) / (10 + 90) = 5%。50% と 0% の単純平均や無定員を混ぜない。
        self.assertEqual(summary["capacity_utilization_percent"], 5.0)
        self.assertEqual(summary["capacity_event_count"], 2)
        rows = {row["id"]: row for row in result["ranking"]}
        self.assertEqual(rows[site_empty]["registered"], 0)
        self.assertEqual(rows[site_empty]["occupancy_percent"], 0.0)
        self.assertIsNone(rows[site_unlimited]["occupancy_percent"])
        for event_id in (external, free):
            self.assertIsNone(rows[event_id]["registered"])
            self.assertIsNone(rows[event_id]["occupancy_percent"])
        self.assertEqual(
            [row["id"] for row in result["ranking"][:3]],
            [site_popular, site_unlimited, site_empty],
        )
        category = next(r for r in result["categories"] if r["label"] == CATEGORIES[1])
        self.assertIsNone(category["registration_count"])
        self.assertIsNone(category["average_registrations"])

    def test_cohort_filters_event_start_dates_status_but_not_application_date(self):
        first = add_event(start_at="2026-10-01T00:00")
        last = add_event(start_at="2026-10-31T23:59")
        outside_before = add_event(start_at="2026-09-30T23:59", end_at="2026-10-01T12:00")
        outside_after = add_event(start_at="2026-11-01T00:00")
        pending = add_event(start_at="2026-10-10T10:00", status="pending")
        rejected = add_event(start_at="2026-10-10T10:00", status="rejected")
        add_registration(first, created_at="2026-09-01T12:00")
        add_registration(last, created_at="2026-10-30T12:00")
        for event_id in (outside_before, outside_after, pending, rejected):
            add_registration(event_id)
            add_view("excluded-browser", event_id, view_date="2026-10-01")
            add_favorite("excluded-browser", event_id)

        result = self.dashboard()
        self.assertEqual({r["id"] for r in result["ranking"]}, {first, last})
        self.assertEqual(result["summary"]["registration_count"], 2)
        self.assertEqual(result["summary"]["view_count"], 0)
        self.assertEqual(result["summary"]["favorite_count"], 0)
        self.assertEqual(result["summary"]["unique_visitor_count"], 0)
        # 単一日の指定でも、その日の終端まで含む。
        single_day = build_dashboard(self.end, self.end)
        self.assertEqual([r["id"] for r in single_day["ranking"]], [last])

    def test_visitors_are_distinct_across_events_and_categories_weekdays_are_complete(self):
        monday = add_event(start_at="2026-10-05T10:00", category=CATEGORIES[0])
        sunday = add_event(start_at="2026-10-11T10:00", category="以前のカテゴリ")
        add_view("same-browser", monday, view_date="2026-10-01")
        add_view("same-browser", sunday, view_date="2026-10-01")
        result = self.dashboard()
        self.assertEqual(result["summary"]["view_count"], 2)
        self.assertEqual(result["summary"]["unique_visitor_count"], 1)
        self.assertEqual([r["label"] for r in result["categories"][:len(CATEGORIES)]], CATEGORIES)
        self.assertIn("以前のカテゴリ", [r["label"] for r in result["categories"]])
        self.assertEqual([r["weekday_index"] for r in result["weekdays"]], list(range(7)))
        self.assertEqual([r["event_count"] for r in result["weekdays"]], [1, 0, 0, 0, 0, 0, 1])
        self.assertEqual(sum(r["event_count"] for r in result["categories"]), 2)

    def test_empty_cohort_and_unobserved_registration_metrics(self):
        empty = self.dashboard()
        self.assertEqual(empty["summary"]["event_count"], 0)
        self.assertEqual(empty["summary"]["view_count"], 0)
        self.assertIsNone(empty["summary"]["registration_count"])
        self.assertIsNone(empty["summary"]["average_registrations"])
        self.assertIsNone(empty["summary"]["capacity_utilization_percent"])
        self.assertEqual(empty["ranking"], [])
        self.assertEqual(len(empty["weekdays"]), 7)
        add_event(start_at="2026-10-01T10:00", registration_mode="external")
        external_only = self.dashboard()
        self.assertEqual(external_only["summary"]["event_count"], 1)
        self.assertEqual(external_only["summary"]["site_event_count"], 0)
        self.assertIsNone(external_only["summary"]["registration_count"])
        self.assertIsNone(external_only["summary"]["average_registrations"])

    def test_invalid_dates_and_capacity_are_handled_without_division_by_zero(self):
        with self.assertRaises(ValueError):
            build_dashboard(self.end, self.start)
        with self.assertRaises(TypeError):
            build_dashboard("2026-10-01", self.end)
        for capacity in (None, 0, -1, "不明", float("inf")):
            add_event(start_at="2026-10-01T10:00", capacity=capacity)
        result = self.dashboard()
        self.assertEqual(result["summary"]["registration_count"], 0)
        self.assertIsNone(result["summary"]["capacity_utilization_percent"])
        self.assertTrue(all(r["occupancy_percent"] is None for r in result["ranking"]))

    def test_dashboard_contains_no_registration_or_anonymous_identifier_fields(self):
        event_id = add_event(start_at="2026-10-01T10:00")
        add_registration(
            event_id, name="PRIVATE NAME", email="private@example.test", student_number="PRIVATE-ID"
        )
        add_view("PRIVATE-VISITOR", event_id, view_date="2026-10-01")
        content = repr(self.dashboard())
        for private_value in ("PRIVATE NAME", "private@example.test", "PRIVATE-ID", "PRIVATE-VISITOR"):
            self.assertNotIn(private_value, content)


class CsvSafetyTests(unittest.TestCase):
    def test_formula_cells_and_leading_control_characters_are_escaped(self):
        for value in (
            "=1+1", "+SUM(A1:A2)", "-1+2", "@SUM(A1:A2)",
            "   =1+1", "\t=1+1", "\r=1+1", "\n=1+1", " \ttext", "\ttext", "\u3000=1+1",
        ):
            with self.subTest(value=value):
                self.assertEqual(csv_safe(value), "'" + value)

    def test_ordinary_values_and_missing_values_remain_readable(self):
        self.assertEqual(csv_safe("データサイエンス講演会"), "データサイエンス講演会")
        self.assertEqual(csv_safe("  通常の文字列"), "  通常の文字列")
        self.assertEqual(csv_safe('a,b,"c"'), 'a,b,"c"')
        self.assertEqual(csv_safe(12), "12")
        self.assertEqual(csv_safe(None), "")


if __name__ == "__main__":
    unittest.main()
