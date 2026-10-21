"""Isolated fixtures for the extension's regression tests.

All rows created here are synthetic.  All HTTP tests use the shipped original
and data-science templates; no template fallback hides missing integration.
"""

from datetime import timedelta
from pathlib import Path
import secrets
import tempfile
import unittest
import uuid

from flask.testing import FlaskClient

from campus_events import create_app
from campus_events.db import get_db
from campus_events.events import now_jst


class IsolatedRequestClient(FlaskClient):
    """Give each HTTP call its own Flask ``g``, like a real server request.

    Tests keep a separate app context pushed for convenient direct DB fixtures.
    Reusing that context for HTTP calls would incorrectly reuse browser IDs
    cached in ``g`` across otherwise independent simulated browsers.
    """

    def open(self, *args, **kwargs):
        with self.application.app_context():
            return super().open(*args, **kwargs)


def make_test_app(temp_path, **overrides):
    """Create an isolated application under an existing temporary directory."""
    temp_path = Path(temp_path)
    config = {
        "TESTING": True,
        "SECRET_KEY": "isolated-tests-not-for-deployment",
        "ADMIN_PASSWORD": "isolated-test-password",
        "DATABASE": str(temp_path / "campus_events.sqlite"),
        "UPLOAD_FOLDER": str(temp_path / "uploads"),
        "PLACE_SEARCH_URL": "",
    }
    config.update(overrides)
    app = create_app(config)
    app.test_client_class = IsolatedRequestClient
    return app


def _insert(table, values):
    """Insert synthetic test rows, validating identifiers against the schema."""
    db = get_db()
    available = {row["name"] for row in db.execute(f"PRAGMA table_info({table})")}
    unknown = set(values).difference(available)
    if unknown:
        raise ValueError(f"Unknown fixture columns for {table}: {sorted(unknown)}")
    columns = ", ".join(values)
    placeholders = ", ".join("?" for _ in values)
    cursor = db.execute(
        f"INSERT INTO {table} ({columns}) VALUES ({placeholders})",
        tuple(values.values()),
    )
    db.commit()
    return cursor.lastrowid


def add_event(**overrides):
    """Create a future approved site-registration event and return its ID."""
    now = now_jst()
    future = now + timedelta(days=30)
    values = {
        "title": f"テストイベント {uuid.uuid4().hex[:8]}",
        "description": "データ分析と可視化を体験する学生向けの勉強会です。",
        "category": "学術・研究",
        "start_at": future.strftime("%Y-%m-%dT%H:%M"),
        "end_at": (future + timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M"),
        "location": "テスト教室",
        "organizer_name": "テスト実行用の団体",
        "organizer_type": "学部・学科",
        "registration_mode": "site",
        "status": "approved",
        "source": "office",
        "capacity": 30,
        "receipt_code": uuid.uuid4().hex[:8].upper(),
        "created_at": now.strftime("%Y-%m-%dT%H:%M"),
        "updated_at": now.strftime("%Y-%m-%dT%H:%M"),
    }
    values.update(overrides)
    return _insert("events", values)


def add_registration(event_id, **overrides):
    """Create a synthetic application; its email is unique by default."""
    values = {
        "event_id": event_id,
        "name": "テスト参加者",
        "email": f"test-{uuid.uuid4().hex}@example.invalid",
        "student_number": "TEST-ONLY",
        "created_at": now_jst().strftime("%Y-%m-%dT%H:%M"),
    }
    values.update(overrides)
    return _insert("registrations", values)


def add_view(visitor_id, event_id, view_date=None, **overrides):
    """Add one browser/event/day row; duplicates remain a database error."""
    view_date = view_date or now_jst().date().isoformat()
    values = {
        "visitor_id": visitor_id,
        "event_id": event_id,
        "view_date": view_date,
        "first_viewed_at": f"{view_date}T12:00:00",
        "last_viewed_at": f"{view_date}T12:00:00",
    }
    values.update(overrides)
    return _insert("event_views", values)


def add_favorite(visitor_id, event_id, created_at=None):
    """Add a browser's current favorite entry."""
    return _insert(
        "event_favorites",
        {
            "visitor_id": visitor_id,
            "event_id": event_id,
            "created_at": created_at or now_jst().isoformat(timespec="seconds"),
        },
    )


class AppTestCase(unittest.TestCase):
    """Each test gets its own database, uploads directory, app and browser."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory(prefix="campus-events-tests-")
        self.addCleanup(self.tmpdir.cleanup)
        self.app = make_test_app(self.tmpdir.name)
        self.app_context = self.app.app_context()
        self.app_context.push()
        self.addCleanup(self.app_context.pop)
        self.client = self.app.test_client()

    def csrf_token(self, client=None):
        client = client or self.client
        with client.session_transaction() as session:
            token = session.setdefault("_csrf_token", secrets.token_urlsafe(32))
        return token

    def csrf_post(self, path, data=None, client=None, **kwargs):
        client = client or self.client
        form = dict(data or {})
        form["_csrf_token"] = self.csrf_token(client)
        return client.post(path, data=form, **kwargs)

    def login_admin(self, client=None):
        client = client or self.client
        return self.csrf_post(
            "/admin/login",
            {"password": self.app.config["ADMIN_PASSWORD"], "staff_name": "テスト担当"},
            client=client,
        )

    def count_rows(self, table):
        if table not in {"events", "registrations", "event_views", "event_favorites"}:
            raise ValueError("Unsupported fixture table")
        return get_db().execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
