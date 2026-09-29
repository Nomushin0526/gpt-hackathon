import io
import re
from datetime import timedelta

import pytest

from campus_events import create_app
from campus_events.db import init_db
from campus_events.events import DATETIME_FORMAT, now_jst


@pytest.fixture
def app(tmp_path):
    app = create_app({
        "TESTING": True,
        "DATABASE": str(tmp_path / "test.sqlite"),
        "UPLOAD_FOLDER": str(tmp_path),
        "ADMIN_PASSWORD": "secret",
        "SECRET_KEY": "test",
    })
    with app.app_context():
        init_db()
    return app


@pytest.fixture
def client(app):
    return app.test_client()


def csrf(client, path="/submit"):
    html = client.get(path).get_data(as_text=True)
    return re.search(r'name="_csrf_token" value="([^"]+)"', html).group(1)


def fmt(dt):
    return dt.strftime(DATETIME_FORMAT)


def event_form(**overrides):
    start = now_jst() + timedelta(days=3)
    form = {
        "title": "ボードゲーム交流会",
        "category": "部活動・サークル",
        "description": "誰でも参加できます",
        "start_at": fmt(start),
        "end_at": fmt(start + timedelta(hours=2)),
        "location": "サークル棟",
        "organizer_name": "ボドゲ同好会",
        "organizer_type": "部活動・サークル",
        "contact_email": "club@example.com",
        "agree": "1",
    }
    form.update(overrides)
    return form


def login(client):
    token = csrf(client, "/admin/login")
    return client.post("/admin/login", data={"password": "secret", "_csrf_token": token})


def submit(client, **overrides):
    data = event_form(**overrides)
    data["_csrf_token"] = csrf(client)
    return client.post("/submit", data=data)


def test_submission_is_hidden_until_approved(client):
    res = submit(client)
    assert res.status_code == 302
    code = res.headers["Location"].rsplit("/", 1)[-1]

    assert "ボードゲーム交流会" not in client.get("/").get_data(as_text=True)
    status_page = client.get(f"/status?code={code}").get_data(as_text=True)
    assert "審査待ち" in status_page

    login(client)
    token = csrf(client, "/admin/")
    client.post("/admin/events/1/status", data={"status": "approved", "_csrf_token": token})

    assert "ボードゲーム交流会" in client.get("/").get_data(as_text=True)
    assert "公開中" in client.get(f"/status?code={code.lower()}").get_data(as_text=True)


def test_reject_requires_comment_and_is_shown_to_applicant(client):
    code = submit(client).headers["Location"].rsplit("/", 1)[-1]
    login(client)
    token = csrf(client, "/admin/")
    client.post("/admin/events/1/status", data={"status": "rejected", "_csrf_token": token})
    assert "審査待ち" in client.get(f"/status?code={code}").get_data(as_text=True)

    client.post("/admin/events/1/status",
                data={"status": "rejected", "review_comment": "場所を具体的に", "_csrf_token": token})
    page = client.get(f"/status?code={code}").get_data(as_text=True)
    assert "差し戻し" in page and "場所を具体的に" in page


def test_submission_validation(client):
    start = now_jst() + timedelta(days=1)
    res = submit(client, title="", end_at=fmt(start - timedelta(hours=1)), start_at=fmt(start))
    assert res.status_code == 400
    body = res.get_data(as_text=True)
    assert "イベント名を入力してください" in body
    assert "終了日時は開始日時より後" in body


def test_submission_without_agreement_is_rejected(client):
    res = submit(client, agree="")
    assert res.status_code == 400


def test_csrf_token_required(client):
    res = client.post("/submit", data=event_form())
    assert res.status_code == 400


def test_poster_upload_rejects_non_images(client):
    data = event_form()
    data["_csrf_token"] = csrf(client)
    data["poster"] = (io.BytesIO(b"x"), "evil.html")
    res = client.post("/submit", data=data, content_type="multipart/form-data")
    assert res.status_code == 400


def test_admin_pages_require_login(client):
    assert client.get("/admin/").status_code == 302
    token = csrf(client, "/admin/login")
    res = client.post("/admin/login", data={"password": "wrong", "_csrf_token": token})
    assert "パスワードが違います" in res.get_data(as_text=True)


def test_admin_created_event_is_published_immediately_with_poster(client, app):
    login(client)
    data = event_form(title="就活講座", organizer_type="学生課・大学", contact_email="")
    data["_csrf_token"] = csrf(client, "/admin/events/new")
    data["poster"] = (io.BytesIO(b"\x89PNG fake"), "poster.png")
    res = client.post("/admin/events/new", data=data, content_type="multipart/form-data")
    assert res.status_code == 302

    page = client.get("/").get_data(as_text=True)
    assert "就活講座" in page and "学生課掲載" in page
    detail = client.get("/events/1").get_data(as_text=True)
    poster_url = re.search(r'src="(/posters/[^"]+)"', detail).group(1)
    assert client.get(poster_url).status_code == 200


def test_period_filter_and_search(client):
    login(client)
    now = now_jst()
    for title, start in [("来週の講演", now + timedelta(days=3)),
                         ("来月の講演", now + timedelta(days=20)),
                         ("先月の講演", now - timedelta(days=20))]:
        data = event_form(title=title, start_at=fmt(start), end_at="")
        data["_csrf_token"] = csrf(client, "/admin/events/new")
        client.post("/admin/events/new", data=data)

    upcoming = client.get("/").get_data(as_text=True)
    assert "来週の講演" in upcoming and "来月の講演" in upcoming and "先月の講演" not in upcoming
    week = client.get("/?period=week").get_data(as_text=True)
    assert "来週の講演" in week and "来月の講演" not in week
    past = client.get("/?period=past").get_data(as_text=True)
    assert "先月の講演" in past and "来週の講演" not in past
    found = client.get("/?q=来月").get_data(as_text=True)
    assert "来月の講演" in found and "来週の講演" not in found


def test_ics_download_and_pending_event_not_public(client):
    submit(client)
    assert client.get("/events/1").status_code == 404
    assert client.get("/events/1/calendar.ics").status_code == 404

    login(client)
    token = csrf(client, "/admin/")
    client.post("/admin/events/1/status", data={"status": "approved", "_csrf_token": token})
    res = client.get("/events/1/calendar.ics")
    assert res.status_code == 200
    body = res.get_data(as_text=True)
    assert "BEGIN:VEVENT" in body and "SUMMARY:ボードゲーム交流会" in body


def test_delete_event(client):
    submit(client)
    login(client)
    token = csrf(client, "/admin/")
    client.post("/admin/events/1/delete", data={"_csrf_token": token})
    assert client.get("/admin/events/1").status_code == 404


def test_login_next_does_not_redirect_offsite(client):
    token = csrf(client, "/admin/login")
    res = client.post("/admin/login?next=https://evil.example",
                      data={"password": "secret", "_csrf_token": token})
    assert res.headers["Location"] == "/admin/"
