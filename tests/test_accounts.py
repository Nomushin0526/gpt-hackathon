import base64
import json
import re
import time
from datetime import timedelta
from urllib.parse import parse_qs, urlparse

import pytest

from campus_events import accounts, create_app
from campus_events.db import get_db
from campus_events.events import DATETIME_FORMAT, now_jst

DOMAIN = "st.kitasato-u.ac.jp"
CLIENT_ID = "test-client.apps.googleusercontent.com"


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
    })


@pytest.fixture
def client(app):
    return app.test_client()


def csrf(client, path="/submit"):
    html = client.get(path).get_data(as_text=True)
    return re.search(r'name="_csrf_token" value="([^"]+)"', html).group(1)


def fake_id_token(claims):
    def part(obj):
        return base64.urlsafe_b64encode(json.dumps(obj).encode()).rstrip(b"=").decode()
    return f"{part({'alg': 'RS256'})}.{part(claims)}.signature"


def google_login(client, monkeypatch, email="taro@" + DOMAIN, **overrides):
    """Google のログイン画面を通ったことにして、コールバックまで進める。"""
    start = client.get("/login/google?next=/mypage")
    state = parse_qs(urlparse(start.headers["Location"]).query)["state"][0]
    with client.session_transaction() as sess:
        nonce = sess["oauth"]["nonce"]
    claims = {
        "iss": "https://accounts.google.com", "aud": CLIENT_ID, "exp": time.time() + 600,
        "nonce": nonce, "email": email, "email_verified": True, "hd": DOMAIN,
        "sub": "sub-" + email, "name": "北里 太郎",
    }
    claims.update(overrides)
    monkeypatch.setattr(accounts, "_fetch_token", lambda code: {"id_token": fake_id_token(claims)})
    return client.get(f"/login/google/callback?state={state}&code=abc")


def admin_event(client, title, description, *, mode="site", days=3, capacity=""):
    token = csrf(client, "/admin/login")
    client.post("/admin/login", data={"password": "secret", "staff_name": "テスト担当", "_csrf_token": token})
    start = now_jst() + timedelta(days=days)
    res = client.post("/admin/events/new", data={
        "title": title, "category": "学術・研究", "description": description,
        "start_at": start.strftime(DATETIME_FORMAT), "end_at": "",
        "location": "L1号館", "organizer_name": "学生課", "organizer_type": "学生課・大学",
        "registration_mode": mode, "capacity": capacity,
        "_csrf_token": csrf(client, "/admin/events/new"),
    })
    client.post("/admin/logout", data={"_csrf_token": csrf(client, "/")})
    return int(res.headers["Location"].rsplit("/", 1)[-1])


def apply(client, event_id, email="someone@example.com", name="北里 太郎"):
    token = csrf(client, f"/events/{event_id}")
    return client.post(f"/events/{event_id}/register",
                       data={"name": name, "email": email, "_csrf_token": token})


# --- ログイン -------------------------------------------------------------

def test_login_redirects_to_google_with_university_hint(client):
    page = client.get("/login").get_data(as_text=True)
    assert "Google でログイン" in page and "開発用ログイン" not in page
    res = client.get("/login/google")
    url = urlparse(res.headers["Location"])
    query = parse_qs(url.query)
    assert url.netloc == "accounts.google.com"
    assert query["hd"] == [DOMAIN] and query["scope"] == ["openid email profile"]
    assert query["client_id"] == [CLIENT_ID]


def test_google_login_creates_account(client, app, monkeypatch):
    res = google_login(client, monkeypatch)
    assert res.headers["Location"] == "/mypage"
    page = client.get("/mypage").get_data(as_text=True)
    assert "taro@" + DOMAIN in page and "マイページ" in page
    with app.app_context():
        assert get_db().execute("SELECT COUNT(*) FROM users").fetchone()[0] == 1


@pytest.mark.parametrize("overrides, message", [
    ({"email": "taro@gmail.com", "hd": None}, "大学のアカウント"),
    ({"hd": None}, "大学のアカウント"),
    ({"email": "taro@kitasato-u.ac.jp"}, "大学のアカウント"),
    ({"email_verified": False}, "確認されていない"),
    ({"aud": "other-client"}, "宛先"),
    ({"iss": "https://evil.example"}, "発行元"),
    ({"nonce": "wrong"}, "確認に失敗"),
    ({"exp": time.time() - 10}, "有効期限"),
])
def test_google_login_rejects_invalid_accounts(client, app, monkeypatch, overrides, message):
    res = google_login(client, monkeypatch, **overrides)
    assert res.headers["Location"] == "/login"
    assert message in client.get("/login").get_data(as_text=True)
    with app.app_context():
        assert get_db().execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0


def test_google_callback_rejects_wrong_state(client, monkeypatch):
    client.get("/login/google")
    monkeypatch.setattr(accounts, "_fetch_token", lambda code: pytest.fail("token must not be fetched"))
    res = client.get("/login/google/callback?state=forged&code=abc")
    assert res.headers["Location"] == "/login"


def test_login_next_stays_on_site(client, monkeypatch):
    start = client.get("/login/google?next=//evil.example")
    with client.session_transaction() as sess:
        assert sess["oauth"]["next"] == "/"


def test_dev_login_is_disabled_by_default(client):
    assert client.post("/login/dev", data={"email": "a@" + DOMAIN, "_csrf_token": csrf(client)}).status_code == 404


def test_dev_login_checks_domain(tmp_path):
    app = create_app({"TESTING": True, "DATABASE": str(tmp_path / "d.sqlite"), "UPLOAD_FOLDER": str(tmp_path),
                      "SECRET_KEY": "t", "PLACE_SEARCH_URL": "", "ALLOW_DEV_LOGIN": True})
    client = app.test_client()
    assert "開発用ログイン" in client.get("/login").get_data(as_text=True)
    bad = client.post("/login/dev", data={"email": "a@gmail.com", "_csrf_token": csrf(client)})
    assert bad.headers["Location"] == "/login"
    ok = client.post("/login/dev", data={"email": "hanako@" + DOMAIN, "next": "/mypage", "_csrf_token": csrf(client)})
    assert ok.headers["Location"] == "/mypage"
    assert "hanako@" + DOMAIN in client.get("/mypage").get_data(as_text=True)


def test_login_link_hidden_when_not_configured(tmp_path):
    app = create_app({"TESTING": True, "DATABASE": str(tmp_path / "d.sqlite"), "UPLOAD_FOLDER": str(tmp_path),
                      "SECRET_KEY": "t", "PLACE_SEARCH_URL": ""})
    client = app.test_client()
    assert 'class="nav-login"' not in client.get("/").get_data(as_text=True)
    assert client.get("/login/google").status_code == 404


# --- 端末をまたいだ記録 ------------------------------------------------------

def test_favorites_follow_the_account_across_devices(app, monkeypatch):
    phone, laptop = app.test_client(), app.test_client()
    event_id = admin_event(phone, "Python 勉強会", "Python の入門")
    phone.get(f"/events/{event_id}")
    phone.post(f"/events/{event_id}/favorite",
               data={"action": "add", "back": "detail", "_csrf_token": csrf(phone, f"/events/{event_id}")})

    google_login(phone, monkeypatch)  # ログイン前のお気に入りをアカウントへ引き継ぐ
    google_login(laptop, monkeypatch)
    assert "Python 勉強会" in laptop.get("/favorites").get_data(as_text=True)


def test_logout_hides_history_from_next_person(app, monkeypatch):
    client = app.test_client()
    event_id = admin_event(client, "Python 勉強会", "Python の入門")
    google_login(client, monkeypatch)
    client.post(f"/events/{event_id}/favorite",
                data={"action": "add", "back": "detail", "_csrf_token": csrf(client, f"/events/{event_id}")})
    client.post("/logout", data={"_csrf_token": csrf(client)})
    assert "Python 勉強会" not in client.get("/favorites").get_data(as_text=True)


def test_new_account_does_not_take_over_another_accounts_browser_id(app, monkeypatch):
    client = app.test_client()
    google_login(client, monkeypatch, email="a@" + DOMAIN)
    client.get("/recommendations")  # このブラウザーに Cookie を発行
    with client.session_transaction() as sess:  # ログアウトせずにセッションだけ切れた状態
        sess.pop("user_id")
    google_login(client, monkeypatch, email="b@" + DOMAIN)
    with app.app_context():
        ids = [r[0] for r in get_db().execute("SELECT visitor_id FROM users")]
    assert len(ids) == 2 and len(set(ids)) == 2


# --- 参加申込・応募 ---------------------------------------------------------

def test_logged_in_registration_uses_account_email_and_can_be_cancelled(app, monkeypatch):
    client = app.test_client()
    event_id = admin_event(client, "就活講座", "面接練習", capacity="1")
    google_login(client, monkeypatch)
    apply(client, event_id, email="someone-else@example.com")
    with app.app_context():
        row = get_db().execute("SELECT email, user_id FROM registrations").fetchone()
    assert row["email"] == "taro@" + DOMAIN and row["user_id"] is not None

    assert "申込済み" in client.get(f"/events/{event_id}").get_data(as_text=True)
    page = client.get("/mypage").get_data(as_text=True)
    assert "就活講座" in page
    reg_id = re.search(r"/mypage/registrations/(\d+)/cancel", page).group(1)
    client.post(f"/mypage/registrations/{reg_id}/cancel", data={"_csrf_token": csrf(client, "/mypage")})
    assert client.get("/api/registration-counts?ids=1").get_json()["1"]["registered"] == 0


def test_cannot_cancel_someone_elses_registration(app, monkeypatch):
    other = app.test_client()
    event_id = admin_event(other, "就活講座", "面接練習")
    apply(other, event_id, email="x@example.com")
    client = app.test_client()
    google_login(client, monkeypatch)
    res = client.post("/mypage/registrations/1/cancel", data={"_csrf_token": csrf(client, "/mypage")})
    assert res.status_code == 404


def test_earlier_registrations_with_same_email_are_linked(app, monkeypatch):
    client = app.test_client()
    event_id = admin_event(client, "就活講座", "面接練習")
    apply(client, event_id, email="Taro@" + DOMAIN)
    google_login(client, monkeypatch)
    assert "就活講座" in client.get("/mypage").get_data(as_text=True)


def test_submission_appears_on_mypage(app, monkeypatch):
    client = app.test_client()
    google_login(client, monkeypatch)
    assert f'value="taro@{DOMAIN}"' in client.get("/submit").get_data(as_text=True)
    start = now_jst() + timedelta(days=5)
    client.post("/submit", data={
        "title": "ボードゲーム会", "category": "部活動・サークル", "description": "誰でも",
        "start_at": start.strftime(DATETIME_FORMAT), "location": "部室棟", "organizer_name": "同好会",
        "organizer_type": "部活動・サークル", "contact_email": "taro@" + DOMAIN,
        "registration_mode": "none", "agree": "1", "_csrf_token": csrf(client),
    })
    page = client.get("/mypage").get_data(as_text=True)
    assert "ボードゲーム会" in page and "審査待ち" in page


def test_mypage_requires_login(client):
    res = client.get("/mypage")
    assert res.headers["Location"].startswith("/login")


# --- おすすめ ---------------------------------------------------------------

def test_registrations_feed_recommendations(app, monkeypatch):
    client = app.test_client()
    applied = admin_event(client, "Python データ分析 入門", "Python と pandas でデータ分析")
    admin_event(client, "Python データ可視化 実践", "Python でデータ分析とグラフ作成")
    admin_event(client, "音楽ライブ", "バンドの演奏会")
    google_login(client, monkeypatch)
    apply(client, applied)
    client.get("/status")  # 申込完了のお知らせを表示し終える
    with app.app_context():  # 申込のときに開いた詳細ページの閲覧記録を消し、申込だけで判定する
        get_db().execute("DELETE FROM event_views")
        get_db().commit()
    page = client.get("/recommendations").get_data(as_text=True)
    assert "申込 <strong>1</strong>" in page and "最近見たイベント <strong>0</strong>" in page
    assert "閲覧・お気に入り・申込した公開イベントと" in page  # 内容の近さによるおすすめ
    assert "Python データ可視化 実践" in page
    assert "Python データ分析 入門" not in page.split("あなたへのおすすめ", 1)[-1]  # 申込済みは候補から外す


def test_registrations_ignored_without_login(app):
    client = app.test_client()
    applied = admin_event(client, "Python データ分析 入門", "Python と pandas でデータ分析")
    admin_event(client, "Python データ可視化 実践", "Python でデータ分析とグラフ作成")
    apply(client, applied)
    with app.app_context():
        get_db().execute("DELETE FROM event_views")
        get_db().commit()
    page = client.get("/recommendations").get_data(as_text=True)
    assert "閲覧・お気に入り・申込した公開イベントと" not in page
