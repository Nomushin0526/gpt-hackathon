import io
import re
from datetime import timedelta
from urllib.parse import quote

import pytest

from campus_events import create_app
from campus_events.db import init_db
from campus_events import places
from campus_events.events import DATETIME_FORMAT, now_jst


@pytest.fixture
def app(tmp_path):
    app = create_app({
        "TESTING": True,
        "DATABASE": str(tmp_path / "test.sqlite"),
        "UPLOAD_FOLDER": str(tmp_path),
        "ADMIN_PASSWORD": "secret",
        "SECRET_KEY": "test",
        "PLACE_SEARCH_URL": "",
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
    return client.post("/admin/login", data={"password": "secret", "staff_name": "テスト担当", "_csrf_token": token})


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
    res = client.post("/admin/login", data={"password": "wrong", "staff_name": "テスト担当", "_csrf_token": token})
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
                      data={"password": "secret", "staff_name": "テスト担当", "_csrf_token": token})
    assert res.headers["Location"] == "/admin/"


def create_site_event(client, **overrides):
    """ログイン済みのクライアントで、サイト申込のイベントを公開する。"""
    data = event_form(registration_mode="site", **overrides)
    data["_csrf_token"] = csrf(client, "/admin/events/new")
    res = client.post("/admin/events/new", data=data)
    assert res.status_code == 302
    return int(res.headers["Location"].rsplit("/", 1)[-1])


def apply(client, event_id, email, name="北里 太郎"):
    token = csrf(client, f"/events/{event_id}")
    return client.post(f"/events/{event_id}/register",
                       data={"name": name, "email": email, "_csrf_token": token})


def test_registration_counts_and_capacity(client):
    login(client)
    event_id = create_site_event(client, capacity="2")
    page = client.get("/").get_data(as_text=True)
    assert "要申込" in page and "定員 2 名" in page

    assert apply(client, event_id, "a@example.com").status_code == 302
    assert client.get("/api/registration-counts?ids=1").get_json() == {
        "1": {"registered": 1, "capacity": 2, "full": False}
    }

    dup = apply(client, event_id, "A@example.com")
    assert dup.status_code == 400 and "すでに申込済み" in dup.get_data(as_text=True)

    assert apply(client, event_id, "b@example.com").status_code == 302
    assert client.get("/api/registration-counts?ids=1").get_json()["1"]["full"] is True
    assert "満員" in client.get("/").get_data(as_text=True)

    full = apply(client, event_id, "c@example.com")
    assert full.status_code == 400 and "定員に達した" in full.get_data(as_text=True)

    csv_body = client.get(f"/admin/events/{event_id}/registrations.csv").get_data(as_text=True)
    assert "a@example.com" in csv_body and "b@example.com" in csv_body


def test_registration_rejected_for_non_site_events(client):
    login(client)
    data = event_form(registration_mode="none")
    data["_csrf_token"] = csrf(client, "/admin/events/new")
    client.post("/admin/events/new", data=data)
    res = apply(client, 1, "a@example.com")
    assert res.status_code == 302
    assert client.get("/api/registration-counts?ids=1").get_json()["1"]["registered"] == 0


def test_registration_closed_after_event_starts(client):
    login(client)
    start = now_jst() - timedelta(minutes=10)
    event_id = create_site_event(client, start_at=fmt(start), end_at=fmt(start + timedelta(hours=2)))
    apply(client, event_id, "a@example.com")
    assert client.get("/api/registration-counts?ids=1").get_json()["1"]["registered"] == 0


def test_external_registration_requires_info(client):
    res = submit(client, registration_mode="external", registration_info="")
    assert res.status_code == 400
    assert "申込方法・申込先" in res.get_data(as_text=True)
    assert submit(client, registration_mode="external", registration_info="https://forms.gle/x").status_code == 302


def test_admin_can_cancel_registration(client):
    login(client)
    event_id = create_site_event(client)
    apply(client, event_id, "a@example.com")
    token = csrf(client, f"/admin/events/{event_id}")
    client.post("/admin/registrations/1/delete", data={"event_id": event_id, "_csrf_token": token})
    assert client.get("/api/registration-counts?ids=1").get_json()["1"]["registered"] == 0


def test_migrates_database_from_previous_version(tmp_path):
    import sqlite3

    path = tmp_path / "old.sqlite"
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE events (
            id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL, description TEXT NOT NULL,
            category TEXT NOT NULL, start_at TEXT NOT NULL, end_at TEXT, location TEXT NOT NULL,
            organizer_name TEXT NOT NULL, organizer_type TEXT NOT NULL, contact_email TEXT,
            capacity INTEGER, requires_registration INTEGER NOT NULL DEFAULT 0,
            registration_info TEXT, note_to_office TEXT, poster_filename TEXT,
            source TEXT NOT NULL DEFAULT 'student', status TEXT NOT NULL DEFAULT 'pending',
            review_comment TEXT, receipt_code TEXT UNIQUE, created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL);
        INSERT INTO events (title, description, category, start_at, location, organizer_name,
            organizer_type, requires_registration, status, created_at, updated_at)
        VALUES ('旧イベント', '説明', 'その他', '2099-01-01T10:00', '学生ホール', '学生課',
            '学生課・大学', 1, 'approved', '2026-01-01T00:00', '2026-01-01T00:00');
    """)
    conn.commit()
    conn.close()

    app = create_app({"TESTING": True, "DATABASE": str(path), "UPLOAD_FOLDER": str(tmp_path)})
    page = app.test_client().get("/").get_data(as_text=True)
    assert "旧イベント" in page and "要申込" in page


def test_database_is_created_automatically(tmp_path):
    app = create_app({"TESTING": True, "DATABASE": str(tmp_path / "new.sqlite"),
                      "UPLOAD_FOLDER": str(tmp_path)})
    assert app.test_client().get("/").status_code == 200


def test_seed_data_uses_registration_modes(app):
    from campus_events.seed import seed

    with app.app_context():
        seed()
    page = app.test_client().get("/").get_data(as_text=True)
    assert "L1号館 6階 大講義室" in page and "北里大学中央図書館" in page
    assert "申込 <strong" in page


def test_campus_place_matching(app):
    with app.app_context():
        assert places.find_campus_place("L1号館 6階 大講義室")["name"] == "L1号館(一般教育棟)"
        assert places.find_campus_place("Ｌ１号館")["name"] == "L1号館(一般教育棟)"  # 全角でも一致
        assert places.find_campus_place("IPE棟図書館")["name"] == "IPE棟図書館"  # より長い名前を優先
        assert places.find_campus_place("相模原市民会館") is None
        assert places.map_info("オンライン") is None
        assert places.map_info("Zoom(URL は申込者に連絡)") is None


def test_pin_to_latlng_inverts_campus_map_projection():
    for p in places.CAMPUS_PLACES:
        if p["lat"] is not None and p["name"] not in ("第一総合グラウンド",):
            lat, lng = places._pin_to_latlng(*p["pin"])
            assert abs(lat - p["lat"]) < 0.0003 and abs(lng - p["lng"]) < 0.0003, p["name"]


def add_event(client, **overrides):
    data = event_form(organizer_type="学生課・大学", contact_email="", **overrides)
    data["_csrf_token"] = csrf(client, "/admin/events/new")
    return client.post("/admin/events/new", data=data)


def test_campus_event_shows_campus_map_pin_and_google_map(client):
    login(client)
    add_event(client, location="L1号館 6階 大講義室", location_lat="1", location_lng="2")
    page = client.get("/events/1").get_data(as_text=True)
    assert "campus_map.webp" in page
    assert 'class="campus-pin is-active"' in page and "L1号館(一般教育棟)" in page
    # 学内施設は登録された施設の位置を使う(送られた緯度経度は無視する)
    assert "q=35.540359%2C139.394931" in page


def test_campus_place_without_pin_shows_only_google_map(client):
    login(client)
    add_event(client, location="S号館 101教室")
    page = client.get("/events/1").get_data(as_text=True)
    assert "campus-pin" not in page
    assert "maps.google.com/maps?q=" in page


def test_external_place_uses_selected_coordinates(client):
    login(client)
    add_event(client, location="相模原市民会館", location_lat="35.5713", location_lng="139.3732")
    page = client.get("/events/1").get_data(as_text=True)
    assert "campus-pin" not in page
    assert "q=35.5713%2C139.3732" in page

    # 候補から選ばずに入力した場所は、名称でそのまま検索する(キャンパス名を付けない)
    add_event(client, location="横浜アリーナ", location_lat="abc")
    page = client.get("/events/2").get_data(as_text=True)
    assert "q=%E6%A8%AA%E6%B5%9C" in page and "検索した結果" in page

    add_event(client, location="オンライン(Zoom)")
    assert "map-embed" not in client.get("/events/3").get_data(as_text=True)


def test_place_search_api(client, app, monkeypatch):
    calls = []

    def fake_fetch(query):
        calls.append(query)
        return [{"name": "相模原市民会館", "address": "神奈川県相模原市中央区",
                 "lat": 35.5713, "lng": 139.3732, "campus": False}]

    monkeypatch.setattr(places, "_fetch_external", fake_fetch)
    data = client.get("/api/places?q=号館").get_json()
    names = [r["name"] for r in data["results"]]
    assert names[0] == "L1号館(一般教育棟)" and "相模原市民会館" in names
    assert all(r["campus"] for r in data["results"][:-1])

    # 1 文字では学外検索をしない
    calls.clear()
    client.get("/api/places?q=L")
    assert calls == []

    def failing_fetch(query):
        raise OSError("network down")

    monkeypatch.setattr(places, "_fetch_external", failing_fetch)
    data = client.get("/api/places?q=体育館").get_json()
    assert data["error"] is True and data["results"][0]["name"] == "総合体育館"


def test_admin_can_move_campus_pin(client):
    assert client.get("/admin/campus-map").status_code == 302
    login(client)
    token = csrf(client, "/admin/campus-map")
    client.post("/admin/campus-map", data={"name": "S号館(理学部校舎)", "x": "735", "y": "415",
                                          "_csrf_token": token})
    add_event(client, location="S号館")
    page = client.get("/events/1").get_data(as_text=True)
    assert "left: 50.0%; top: 50.0%;" in page
    # ピン位置から求めた概算の緯度経度で Google マップを表示する
    assert "maps.google.com/maps?q=35.5" in page

    res = client.post("/admin/campus-map", data={"name": "存在しない施設", "x": "1", "y": "1",
                                                "_csrf_token": token}, follow_redirects=True)
    assert "施設が見つかりません" in res.get_data(as_text=True)

    client.post("/admin/campus-map", data={"name": "S号館(理学部校舎)", "reset": "1",
                                          "_csrf_token": token})
    assert "campus-pin" not in client.get("/events/1").get_data(as_text=True)
