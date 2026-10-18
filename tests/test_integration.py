"""HTTP, privacy, migration and existing-registration regression checks.

Run from the package root with ``python -m pytest``.
Set CAMPUS_EVENTS_REFERENCE_DB to the supplied original database to enable its
optional migration check. That check reads the source through SQLite read-only
mode and migrates a temporary copy; user data is never included in this bundle.
"""

from datetime import timedelta
import csv
import hashlib
from html.parser import HTMLParser
import io
import os
from pathlib import Path
import shutil
import sqlite3
import tempfile
import unittest

from campus_events import db, events, places

from tests.support import (
    AppTestCase,
    add_event,
    add_favorite,
    add_registration,
    add_view,
    make_test_app,
)


_REFERENCE_DATABASE_PATH = os.environ.get("CAMPUS_EVENTS_REFERENCE_DB")
REFERENCE_DATABASE = (
    Path(_REFERENCE_DATABASE_PATH).expanduser().resolve()
    if _REFERENCE_DATABASE_PATH else None
)
VISITOR_COOKIE = "campus_events_visitor"


class HTMLProbe(HTMLParser):
    """Inspect rendered form boundaries and navigation without extra packages."""

    VOID_ELEMENTS = {
        "area", "base", "br", "col", "embed", "hr", "img", "input", "link",
        "meta", "param", "source", "track", "wbr",
    }

    def __init__(self, html):
        super().__init__(convert_charrefs=True)
        self.nodes = []
        self.stack = []
        self.feed(html)
        self.close()

    def handle_starttag(self, tag, attrs):
        index = len(self.nodes)
        self.nodes.append({
            "index": index,
            "tag": tag,
            "attrs": dict(attrs),
            "ancestors": tuple(self.stack),
            "text": [],
        })
        if tag not in self.VOID_ELEMENTS:
            self.stack.append(index)

    def handle_endtag(self, tag):
        for position in range(len(self.stack) - 1, -1, -1):
            if self.nodes[self.stack[position]]["tag"] == tag:
                del self.stack[position:]
                break

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in self.VOID_ELEMENTS:
            self.handle_endtag(tag)

    def handle_data(self, data):
        for index in self.stack:
            self.nodes[index]["text"].append(data)

    def find(self, tag=None, css_class=None, within=None, **attrs):
        return [
            node for node in self.nodes
            if (tag is None or node["tag"] == tag)
            and (css_class is None or css_class in node["attrs"].get("class", "").split())
            and (within is None or within["index"] in node["ancestors"])
            and all(node["attrs"].get(key) == value for key, value in attrs.items())
        ]

    @staticmethod
    def text(node):
        return " ".join("".join(node["text"]).split())


class DatabaseIntegrationTests(AppTestCase):
    def test_migration_is_idempotent_and_retains_existing_records(self):
        event_id = add_event(title="移行後にも残すイベント")
        add_registration(event_id, email="migration-test@example.invalid")
        add_view("migration-browser", event_id)
        add_favorite("migration-browser", event_id)
        before = {
            name: self.count_rows(name)
            for name in ("events", "registrations", "event_views", "event_favorites")
        }
        db.migrate_db()
        db.migrate_db()
        after = {name: self.count_rows(name) for name in before}
        self.assertEqual(before, after)
        self.assertEqual(events.get_event(event_id)["title"], "移行後にも残すイベント")

    def test_deleting_event_cascades_activity_and_registrations(self):
        event_id = add_event()
        add_registration(event_id)
        add_view("delete-browser", event_id)
        add_favorite("delete-browser", event_id)
        events.delete_event(event_id)
        for table in ("events", "registrations", "event_views", "event_favorites"):
            with self.subTest(table=table):
                self.assertEqual(self.count_rows(table), 0)

    @unittest.skipUnless(
        REFERENCE_DATABASE is not None and REFERENCE_DATABASE.is_file(),
        "Set CAMPUS_EVENTS_REFERENCE_DB to the supplied original database to enable this check",
    )
    def test_uploaded_database_copy_preserves_all_original_rows(self):
        source_hash = hashlib.sha256(REFERENCE_DATABASE.read_bytes()).hexdigest()
        source_uri = REFERENCE_DATABASE.as_uri() + "?mode=ro"
        with sqlite3.connect(source_uri, uri=True) as source:
            source.row_factory = sqlite3.Row
            snapshots = {
                table: [dict(row) for row in source.execute(f"SELECT * FROM {table} ORDER BY rowid")]
                for table in ("events", "registrations", "campus_pins")
            }
        self.assertEqual(len(snapshots["events"]), 6)
        self.assertEqual(len(snapshots["registrations"]), 105)

        with tempfile.TemporaryDirectory(prefix="campus-migration-tests-") as directory:
            copied_path = Path(directory) / "campus_events.sqlite"
            shutil.copy2(REFERENCE_DATABASE, copied_path)
            migrated_app = make_test_app(directory)
            with migrated_app.app_context():
                db.migrate_db()
                db.migrate_db()
                for table, rows in snapshots.items():
                    with self.subTest(table=table):
                        migrated = [
                            dict(row)
                            for row in db.get_db().execute(f"SELECT * FROM {table} ORDER BY rowid")
                        ]
                        self.assertEqual(migrated, rows)
                for table in ("event_views", "event_favorites"):
                    self.assertEqual(db.get_db().execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0], 0)
        self.assertEqual(hashlib.sha256(REFERENCE_DATABASE.read_bytes()).hexdigest(), source_hash)


class StudentBehaviorTests(AppTestCase):
    def test_detail_refresh_records_one_browser_event_day(self):
        event_id = add_event()
        for _ in range(3):
            self.assertEqual(self.client.get(f"/events/{event_id}").status_code, 200)
        self.assertEqual(self.count_rows("event_views"), 1)
        row = db.get_db().execute("SELECT * FROM event_views").fetchone()
        self.assertEqual(row["event_id"], event_id)
        self.assertEqual(row["view_date"], events.now_jst().date().isoformat())

    def test_two_browsers_are_isolated(self):
        event_id = add_event()
        self.client.get(f"/events/{event_id}")
        another_client = self.app.test_client()
        another_client.get(f"/events/{event_id}")
        self.assertEqual(self.count_rows("event_views"), 2)
        visitors = {
            row[0] for row in db.get_db().execute("SELECT visitor_id FROM event_views")
        }
        self.assertEqual(len(visitors), 2)

    def test_missing_private_and_head_requests_do_not_record_views(self):
        hidden_id = add_event(status="pending")
        public_id = add_event()
        self.assertEqual(self.client.get(f"/events/{hidden_id}").status_code, 404)
        self.assertEqual(self.client.get("/events/999999").status_code, 404)
        self.assertEqual(self.client.head(f"/events/{public_id}").status_code, 200)
        self.assertEqual(self.count_rows("event_views"), 0)

    def test_administrator_preview_does_not_record_student_views(self):
        event_id = add_event()
        self.assertIn(self.login_admin().status_code, (302, 303))
        self.assertEqual(self.client.get(f"/events/{event_id}").status_code, 200)
        self.assertEqual(self.count_rows("event_views"), 0)

    def test_past_event_is_still_a_valid_interest_signal(self):
        past = events.now_jst() - timedelta(days=10)
        event_id = add_event(
            start_at=past.strftime("%Y-%m-%dT%H:%M"),
            end_at=(past + timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M"),
        )
        self.assertEqual(self.client.get(f"/events/{event_id}").status_code, 200)
        self.assertEqual(self.count_rows("event_views"), 1)

    def test_invalid_visitor_cookie_is_replaced_without_adopting_raw_id(self):
        event_id = add_event()
        self.client.set_cookie(VISITOR_COOKIE, "forged-browser-id")
        response = self.client.get(f"/events/{event_id}")
        self.assertEqual(response.status_code, 200)
        visitor = db.get_db().execute("SELECT visitor_id FROM event_views").fetchone()[0]
        self.assertNotEqual(visitor, "forged-browser-id")
        cookie = self.client.get_cookie(VISITOR_COOKIE)
        self.assertIsNotNone(cookie)
        self.assertNotEqual(cookie.value, "forged-browser-id")
        self.assertIn("HttpOnly", "; ".join(response.headers.getlist("Set-Cookie")))
        self.assertIn("SameSite=Lax", "; ".join(response.headers.getlist("Set-Cookie")))

    def test_favorite_add_and_remove_are_idempotent(self):
        event_id = add_event()
        for _ in range(2):
            response = self.csrf_post(f"/events/{event_id}/favorite", {"action": "add"})
            self.assertIn(response.status_code, (200, 302, 303))
        self.assertEqual(self.count_rows("event_favorites"), 1)
        for _ in range(2):
            response = self.csrf_post(f"/events/{event_id}/favorite", {"action": "remove"})
            self.assertIn(response.status_code, (200, 302, 303))
        self.assertEqual(self.count_rows("event_favorites"), 0)

    def test_favorites_remain_scoped_to_each_browser(self):
        first_event = add_event(title="ブラウザーA専用のお気に入り")
        second_event = add_event(title="ブラウザーB専用のお気に入り")
        another_client = self.app.test_client()
        self.csrf_post(f"/events/{first_event}/favorite", {"action": "add"})
        self.csrf_post(f"/events/{second_event}/favorite", {"action": "add"}, client=another_client)
        first_body = self.client.get("/favorites").get_data(as_text=True)
        second_body = another_client.get("/favorites").get_data(as_text=True)
        self.assertIn("ブラウザーA専用のお気に入り", first_body)
        self.assertNotIn("ブラウザーB専用のお気に入り", first_body)
        self.assertIn("ブラウザーB専用のお気に入り", second_body)
        self.assertNotIn("ブラウザーA専用のお気に入り", second_body)

    def test_private_events_and_unknown_actions_cannot_be_favorited(self):
        hidden_id = add_event(status="pending")
        public_id = add_event()
        self.assertEqual(
            self.csrf_post(f"/events/{hidden_id}/favorite", {"action": "add"}).status_code,
            404,
        )
        self.assertEqual(
            self.csrf_post(f"/events/{public_id}/favorite", {"action": "unknown"}).status_code,
            400,
        )
        self.assertEqual(self.count_rows("event_favorites"), 0)

    def test_all_behavior_mutations_require_csrf(self):
        event_id = add_event()
        self.client.get(f"/events/{event_id}")
        urls = (
            f"/events/{event_id}/favorite",
            "/preferences/clear-history",
            "/preferences/clear-all",
        )
        for url in urls:
            with self.subTest(url=url):
                self.assertEqual(self.client.post(url, data={"action": "add"}).status_code, 400)
                self.assertEqual(
                    self.client.post(url, data={"action": "add", "_csrf_token": "wrong"}).status_code,
                    400,
                )
                self.assertEqual(self.client.get(url).status_code, 405)
        self.assertEqual(self.count_rows("event_views"), 1)
        self.assertEqual(self.count_rows("event_favorites"), 0)

    def test_clear_history_preserves_favorites_and_other_browsers(self):
        event_id = add_event()
        self.client.get(f"/events/{event_id}")
        self.csrf_post(f"/events/{event_id}/favorite", {"action": "add"})
        another_client = self.app.test_client()
        another_client.get(f"/events/{event_id}")
        before_cookie = self.client.get_cookie(VISITOR_COOKIE).value
        response = self.csrf_post("/preferences/clear-history")
        self.assertIn(response.status_code, (200, 302, 303))
        self.assertEqual(self.count_rows("event_views"), 1)
        self.assertEqual(self.count_rows("event_favorites"), 1)
        self.assertEqual(self.client.get_cookie(VISITOR_COOKIE).value, before_cookie)

    def test_clear_all_erases_only_own_records_and_rotates_cookie(self):
        event_id = add_event()
        self.client.get(f"/events/{event_id}")
        self.csrf_post(f"/events/{event_id}/favorite", {"action": "add"})
        first_visitor = db.get_db().execute("SELECT visitor_id FROM event_views").fetchone()[0]
        before_cookie = self.client.get_cookie(VISITOR_COOKIE).value

        another_client = self.app.test_client()
        another_client.get(f"/events/{event_id}")
        self.csrf_post(f"/events/{event_id}/favorite", {"action": "add"}, client=another_client)
        response = self.csrf_post("/preferences/clear-all")
        self.assertIn(response.status_code, (200, 302, 303))
        self.assertEqual(self.count_rows("event_views"), 1)
        self.assertEqual(self.count_rows("event_favorites"), 1)
        for table in ("event_views", "event_favorites"):
            self.assertEqual(
                db.get_db().execute(f"SELECT COUNT(*) FROM {table} WHERE visitor_id = ?", (first_visitor,)).fetchone()[0],
                0,
            )
        new_cookie = self.client.get_cookie(VISITOR_COOKIE)
        self.assertTrue(new_cookie is None or new_cookie.value != before_cookie)


class PagesAndAdminTests(AppTestCase):
    def test_student_pages_render_real_templates_without_private_metadata(self):
        event_id = add_event(
            title="公開してよいイベントタイトル",
            contact_email="private-contact-sentinel@example.invalid",
            note_to_office="private-office-note-sentinel",
            review_comment="private-review-sentinel",
            receipt_code="PRIVATE-RECEIPT-SENTINEL",
        )
        add_registration(
            event_id,
            name="private-registration-name-sentinel",
            email="private-registration-email-sentinel@example.invalid",
            student_number="private-student-sentinel",
        )
        for path in ("/recommendations", "/favorites"):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)
                body = response.get_data(as_text=True)
                self.assertIn("<html", body.lower())
                for token in (
                    "private-contact-sentinel", "private-office-note-sentinel", "private-review-sentinel",
                    "PRIVATE-RECEIPT-SENTINEL", "private-registration-name-sentinel",
                    "private-registration-email-sentinel", "private-student-sentinel",
                ):
                    self.assertNotIn(token, body)

    def test_analytics_and_aggregate_csv_require_admin_authentication(self):
        for path in ("/admin/analytics", "/admin/analytics.csv", "/admin/analytics.json"):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertIn(response.status_code, (302, 303))
                self.assertTrue(response.headers["Location"].startswith("/admin/login"))

    def test_administrator_can_render_dashboard_and_download_aggregate_csv(self):
        event_id = add_event(title="申込数集計テスト")
        add_registration(
            event_id,
            name="secret-applicant-name",
            email="secret-applicant-email@example.invalid",
            student_number="secret-student-number",
        )
        add_view("secret-visitor-token", event_id)
        self.login_admin()
        today = events.now_jst().date()
        query = f"?start={today.isoformat()}&end={(today + timedelta(days=45)).isoformat()}"
        dashboard = self.client.get("/admin/analytics" + query)
        self.assertEqual(dashboard.status_code, 200)
        self.assertIn("申込数集計テスト", dashboard.get_data(as_text=True))
        response = self.client.get("/admin/analytics.csv" + query)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, "text/csv")
        self.assertIn("attachment", response.headers.get("Content-Disposition", ""))
        content = response.get_data(as_text=True)
        self.assertTrue(content.startswith("\ufeff"))
        rows = list(csv.reader(io.StringIO(content.lstrip("\ufeff"))))
        self.assertGreaterEqual(len(rows), 2)
        self.assertIn("申込数集計テスト", content)
        api_response = self.client.get("/admin/analytics.json" + query)
        self.assertEqual(api_response.status_code, 200)
        api_report = api_response.get_json()
        self.assertEqual(api_report["summary"]["registration_count"], 1)
        self.assertEqual(api_report["summary"]["view_count"], 1)
        for private in (
            "secret-applicant-name", "secret-applicant-email", "secret-student-number", "secret-visitor-token",
        ):
            self.assertNotIn(private, content)
            self.assertNotIn(private, dashboard.get_data(as_text=True))
            self.assertNotIn(private, api_response.get_data(as_text=True))

    def test_analytics_rejects_bad_calendar_dates_and_reversed_ranges(self):
        self.login_admin()
        queries = (
            "?start=2026-02-30&end=2026-03-31",
            "?start=2026-12-31&end=2026-01-01",
            "?start=not-a-date&end=2026-12-31",
        )
        for path in ("/admin/analytics", "/admin/analytics.csv", "/admin/analytics.json"):
            for query in queries:
                with self.subTest(path=path, query=query):
                    self.assertEqual(self.client.get(path + query).status_code, 400)

    def test_csv_escapes_spreadsheet_formula_prefix_in_title(self):
        today = events.now_jst().date()
        title = '=HYPERLINK("https://example.invalid","test")'
        add_event(title=title)
        self.login_admin()
        response = self.client.get(
            f"/admin/analytics.csv?start={today.isoformat()}&end={(today + timedelta(days=45)).isoformat()}"
        )
        self.assertEqual(response.status_code, 200)
        rows = list(csv.reader(io.StringIO(response.get_data(as_text=True).lstrip("\ufeff"))))
        cells = [cell for row in rows for cell in row]
        self.assertNotIn(title, cells)
        self.assertIn("'" + title, cells)

    def test_existing_registration_capacity_and_counts_still_work(self):
        event_id = add_event(capacity=1)
        first = self.csrf_post(
            f"/events/{event_id}/register",
            {"name": "テストA", "email": "first@example.invalid", "student_number": "T001"},
        )
        self.assertIn(first.status_code, (302, 303))
        second = self.csrf_post(
            f"/events/{event_id}/register",
            {"name": "テストB", "email": "second@example.invalid", "student_number": "T002"},
        )
        self.assertEqual(second.status_code, 400)
        self.assertEqual(self.count_rows("registrations"), 1)
        response = self.client.get(f"/api/registration-counts?ids={event_id}")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()[str(event_id)]["registered"], 1)
        self.assertTrue(response.get_json()[str(event_id)]["full"])


class OriginalUIIntegrationTests(AppTestCase):
    def test_original_and_ds_pages_share_brand_navigation_and_assets(self):
        event_id = add_event()
        public_paths = (
            "/", f"/events/{event_id}", "/recommendations", "/favorites", "/admin/login",
        )
        for path in public_paths:
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)
                page = HTMLProbe(response.get_data(as_text=True))
                headers = page.find("header", css_class="site-header")
                self.assertEqual(len(headers), 1)
                self.assertIn("相模原キャンパス イベント掲示板", page.text(headers[0]))
                for target in ("/", "/submit", "/status", "/recommendations", "/favorites"):
                    self.assertTrue(page.find("a", within=headers[0], href=target), target)
                self.assertTrue(page.find("link", href="/static/style.css"))
                self.assertTrue(page.find("script", src="/static/app.js"))

        self.login_admin()
        for path in ("/admin/", "/admin/analytics"):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)
                page = HTMLProbe(response.get_data(as_text=True))
                header = page.find("header", css_class="site-header")[0]
                self.assertIn("相模原キャンパス イベント掲示板", page.text(header))
                self.assertTrue(page.find("a", within=header, href="/admin/analytics"))
                self.assertTrue(page.find("a", within=header, href="/admin/"))
                if path == "/admin/":
                    self.assertTrue(page.find("a", css_class="ds-admin-analytics", href="/admin/analytics"))

    def test_homepage_preview_is_separate_from_event_list_and_total(self):
        for index in range(4):
            add_event(title=f"公開イベント {index + 1}")
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        page = HTMLProbe(response.get_data(as_text=True))
        previews = page.find("section", css_class="ds-home-recommendations")
        totals = page.find(css_class="result-count")
        self.assertEqual(len(previews), 1)
        lists = [
            node for node in page.find(css_class="event-list")
            if previews[0]["index"] not in node["ancestors"]
        ]
        self.assertEqual(len(lists), 1)
        self.assertEqual(len(totals), 1)
        self.assertEqual(page.text(totals[0]), "4 件のイベント")
        self.assertEqual(len(page.find(css_class="ds-home-recommendation-card", within=previews[0])), 3)
        self.assertEqual(len(page.find("a", css_class="event-card", within=lists[0])), 4)
        self.assertNotIn(previews[0]["index"], lists[0]["ancestors"])
        self.assertNotIn(lists[0]["index"], previews[0]["ancestors"])
        for card in page.find("a", css_class="event-card", within=previews[0]):
            self.assertFalse(page.find("form", within=card))
            self.assertFalse(page.find("a", within=card))

    def test_search_and_period_filters_keep_only_matching_original_results(self):
        add_event(title="Pythonデータ分析の体験会", category="学術・研究", organizer_type="学部・学科")
        add_event(title="国際交流カフェ", category="国際交流", organizer_type="学外団体")
        past = events.now_jst() - timedelta(days=10)
        add_event(
            title="終了した交流会",
            start_at=past.strftime("%Y-%m-%dT%H:%M"),
            end_at=(past + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M"),
        )
        cases = (
            ({"q": "Python"}, "Pythonデータ分析の体験会", 1),
            ({"category": "国際交流"}, "国際交流カフェ", 1),
            ({"organizer_type": "学外団体"}, "国際交流カフェ", 1),
            ({"period": "past"}, "終了した交流会", 1),
            ({"period": "week"}, None, 0),
        )
        for query, expected_title, expected_count in cases:
            with self.subTest(query=query):
                response = self.client.get("/", query_string=query)
                self.assertEqual(response.status_code, 200)
                page = HTMLProbe(response.get_data(as_text=True))
                self.assertFalse(page.find(css_class="ds-home-recommendations"))
                self.assertEqual(page.text(page.find(css_class="result-count")[0]), f"{expected_count} 件のイベント")
                cards = page.find("a", css_class="event-card")
                self.assertEqual(len(cards), expected_count)
                if expected_title:
                    self.assertIn(expected_title, page.text(cards[0]))

    def test_detail_favorite_and_original_registration_forms_are_independent(self):
        event_id = add_event(title="フォーム分離確認イベント")
        page_response = self.client.get(f"/events/{event_id}")
        self.assertEqual(page_response.status_code, 200)
        page = HTMLProbe(page_response.get_data(as_text=True))
        favorite_forms = page.find("form", action=f"/events/{event_id}/favorite")
        registration_forms = page.find("form", action=f"/events/{event_id}/register")
        self.assertEqual(len(favorite_forms), 1)
        self.assertEqual(len(registration_forms), 1)
        for form in page.find("form"):
            ancestor_tags = [page.nodes[index]["tag"] for index in form["ancestors"]]
            self.assertNotIn("form", ancestor_tags)

        tokens = []
        for form in (favorite_forms[0], registration_forms[0]):
            self.assertEqual(form["attrs"].get("method", "").lower(), "post")
            csrf_inputs = page.find("input", within=form, name="_csrf_token")
            self.assertEqual(len(csrf_inputs), 1)
            self.assertTrue(csrf_inputs[0]["attrs"].get("value"))
            tokens.append(csrf_inputs[0]["attrs"]["value"])
        for field in ("name", "email", "student_number"):
            self.assertTrue(page.find("input", within=registration_forms[0], name=field))
            self.assertFalse(page.find("input", within=favorite_forms[0], name=field))

        saved = self.client.post(
            f"/events/{event_id}/favorite",
            data={"_csrf_token": tokens[0], "action": "add", "back": "detail"},
        )
        self.assertIn(saved.status_code, (302, 303))
        self.assertTrue(saved.headers["Location"].endswith(f"/events/{event_id}"))
        self.assertEqual(self.count_rows("event_favorites"), 1)
        self.assertEqual(self.count_rows("registrations"), 0)
        registered = self.client.post(
            f"/events/{event_id}/register",
            data={
                "_csrf_token": tokens[1], "name": "フォーム確認用", "email": "form-test@example.invalid",
                "student_number": "T-FORM",
            },
        )
        self.assertIn(registered.status_code, (302, 303))
        self.assertEqual(self.count_rows("registrations"), 1)
        self.assertEqual(self.count_rows("event_favorites"), 1)

    def test_original_campus_map_asset_is_served_at_the_expected_dimensions(self):
        response = self.client.get("/static/campus_map.webp")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, "image/webp")
        source = Path(self.app.static_folder) / "campus_map.webp"
        self.assertEqual(response.data, source.read_bytes())
        self.assertEqual(response.data[:4], b"RIFF")
        self.assertEqual(response.data[8:12], b"WEBP")
        # The supplied map is a VP8 WebP.  Read its frame size without adding an
        # imaging package to the application's test requirements.
        self.assertEqual(response.data[12:16], b"VP8 ")
        self.assertEqual(response.data[23:26], b"\x9d\x01\x2a")
        width = int.from_bytes(response.data[26:28], "little") & 0x3FFF
        height = int.from_bytes(response.data[28:30], "little") & 0x3FFF
        self.assertEqual((width, height), (1470, 830))
        self.assertEqual(places.CAMPUS_MAP_SIZE, (width, height))


if __name__ == "__main__":
    unittest.main()
