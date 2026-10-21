import re
import time
from datetime import timedelta
from urllib.parse import parse_qs, urlparse

import pytest

from campus_events import accounts, create_app
from campus_events.db import get_db
from campus_events.events import DATETIME_FORMAT, now_jst
from tests.test_accounts import CLIENT_ID, fake_id_token

STAFF = "tanaka@kitasato-u.ac.jp"


@pytest.fixture
def app(tmp_path):
    return create_app({
        "TESTING": True,
        "DATABASE": str(tmp_path / "test.sqlite"),
        "UPLOAD_FOLDER": str(tmp_path),
        "ADMIN_PASSWORD": "secret",
        "SECRET_KEY": "test",
        "PLACE_SEARCH_URL": "",
        "GOOGLE_CLIENT_ID": CLIENT_ID,
        "GOOGLE_CLIENT_SECRET": "test-secret",
        "STAFF_EMAILS": f"{STAFF}, other@kitasato-u.ac.jp",
    })


@pytest.fixture
def client(app):
    return app.test_client()


def csrf(client, path="/submit"):
    html = client.get(path).get_data(as_text=True)
    return re.search(r'name="_csrf_token" value="([^"]+)"', html).group(1)


def password_login(client, name="学生課 北里"):
    return client.post("/admin/login", data={
        "password": "secret", "staff_name": name, "_csrf_token": csrf(client, "/admin/login"),
    })


def staff_google_login(client, monkeypatch, email=STAFF):
    start = client.get("/login/google?for=staff&next=/admin/")
    query = parse_qs(urlparse(start.headers["Location"]).query)
    assert "hd" not in query  # 職員のアドレスは学生のドメインとは限らない
    with client.session_transaction() as sess:
        nonce = sess["oauth"]["nonce"]
    claims = {
        "iss": "https://accounts.google.com", "aud": CLIENT_ID, "exp": time.time() + 600,
        "nonce": nonce, "email": email, "email_verified": True, "sub": "staff-" + email, "name": "田中 花子",
    }
    monkeypatch.setattr(accounts, "_fetch_token", lambda code: {"id_token": fake_id_token(claims)})
    return client.get(f"/login/google/callback?state={query['state'][0]}&code=abc")


def submit_event(client, title="ボードゲーム会"):
    start = now_jst() + timedelta(days=5)
    client.post("/submit", data={
        "title": title, "category": "部活動・サークル", "description": "誰でも",
        "start_at": start.strftime(DATETIME_FORMAT), "location": "部室棟", "organizer_name": "同好会",
        "organizer_type": "部活動・サークル", "contact_email": "club@example.com",
        "registration_mode": "none", "agree": "1", "_csrf_token": csrf(client),
    })


def logs(app):
    with app.app_context():
        return [dict(r) for r in get_db().execute("SELECT * FROM audit_logs ORDER BY id")]


def test_password_login_requires_staff_name(client):
    res = client.post("/admin/login", data={"password": "secret", "_csrf_token": csrf(client, "/admin/login")})
    assert "担当者名" in res.get_data(as_text=True)
    assert client.get("/admin/").status_code == 302


def test_approval_records_who_did_it(client, app):
    submit_event(client)
    password_login(client, "学生課 北里")
    client.post("/admin/events/1/status", data={
        "status": "approved", "review_comment": "問題ありません", "_csrf_token": csrf(client, "/admin/"),
    })
    entries = logs(app)
    assert [e["action"] for e in entries] == ["login", "approve"]
    approve = entries[1]
    assert approve["actor_name"] == "学生課 北里" and approve["auth_method"] == "password"
    assert approve["event_id"] == 1 and approve["detail"] == "問題ありません"

    review = client.get("/admin/events/1").get_data(as_text=True)
    assert "最終審査" in review and "学生課 北里" in review and "自己申告" in review
    dashboard = client.get("/admin/?tab=approved").get_data(as_text=True)
    assert "学生課 北里" in dashboard
    assert "学生課 北里" in client.get("/admin/audit").get_data(as_text=True)


def test_reviewer_name_is_not_shown_to_students(client, app):
    submit_event(client)
    password_login(client, "学生課 北里")
    client.post("/admin/events/1/status", data={
        "status": "rejected", "review_comment": "場所を具体的に", "_csrf_token": csrf(client, "/admin/"),
    })
    client.post("/admin/logout", data={"_csrf_token": csrf(client)})
    with app.app_context():
        code = get_db().execute("SELECT receipt_code FROM events").fetchone()[0]
    status_page = client.get(f"/status?code={code}").get_data(as_text=True)
    assert "場所を具体的に" in status_page and "学生課 北里" not in status_page
    assert client.get("/admin/audit").status_code == 302  # ログインしていなければ見られない


def test_staff_google_login_uses_allowlist(client, app, monkeypatch):
    res = staff_google_login(client, monkeypatch)
    assert res.headers["Location"] == "/admin/"
    submit_event(client)
    client.post("/admin/events/1/status", data={"status": "approved", "_csrf_token": csrf(client, "/admin/")})
    approve = logs(app)[-1]
    assert approve["actor_email"] == STAFF and approve["auth_method"] == "google"
    assert approve["actor_name"] == "田中 花子"


def test_staff_google_login_rejects_unlisted_account(client, monkeypatch):
    res = staff_google_login(client, monkeypatch, email="intruder@kitasato-u.ac.jp")
    assert res.headers["Location"] == "/admin/login"
    assert client.get("/admin/").status_code == 302


def test_student_account_cannot_become_staff(client, monkeypatch):
    res = staff_google_login(client, monkeypatch, email="taro@st.kitasato-u.ac.jp")
    assert res.headers["Location"] == "/admin/login"


def test_edit_and_delete_are_logged_and_survive_deletion(client, app):
    password_login(client)
    start = now_jst() + timedelta(days=3)
    form = {
        "title": "就活講座", "category": "就職・キャリア", "description": "面接練習",
        "start_at": start.strftime(DATETIME_FORMAT), "location": "L1号館", "organizer_name": "学生課",
        "organizer_type": "学生課・大学", "registration_mode": "none",
    }
    client.post("/admin/events/new", data={**form, "_csrf_token": csrf(client, "/admin/events/new")})
    client.post("/admin/events/1/edit", data={**form, "capacity": "50", "_csrf_token": csrf(client, "/admin/events/1/edit")})
    client.post("/admin/events/1/delete", data={"_csrf_token": csrf(client, "/admin/")})
    entries = logs(app)
    assert [e["action"] for e in entries] == ["login", "create", "edit", "delete"]
    assert entries[2]["detail"] == "変更: 定員"
    assert entries[3]["event_id"] is None and entries[3]["event_title"] == "就活講座"
    assert "(削除済み)" in client.get("/admin/audit").get_data(as_text=True)


def test_registration_cancel_by_staff_is_logged(client, app):
    password_login(client)
    start = now_jst() + timedelta(days=3)
    client.post("/admin/events/new", data={
        "title": "就活講座", "category": "就職・キャリア", "description": "面接練習",
        "start_at": start.strftime(DATETIME_FORMAT), "location": "L1号館", "organizer_name": "学生課",
        "organizer_type": "学生課・大学", "registration_mode": "site",
        "_csrf_token": csrf(client, "/admin/events/new"),
    })
    client.post("/events/1/register", data={
        "name": "北里 太郎", "email": "t@example.com", "_csrf_token": csrf(client, "/events/1"),
    })
    client.post("/admin/registrations/1/delete", data={"event_id": 1, "_csrf_token": csrf(client, "/admin/")})
    cancel = logs(app)[-1]
    assert cancel["action"] == "cancel_registration" and cancel["detail"] == "申込者: 北里 太郎"
