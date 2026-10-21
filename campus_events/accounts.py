"""学生のログイン(大学の Google アカウント)とマイページ。

Google の OpenID Connect(認可コードフロー)で本人確認し、許可したドメイン
(初期値 st.kitasato-u.ac.jp)のメールアドレスだけを受け付ける。
ログインは任意で、ログインしなくても閲覧・申込・応募はこれまでどおり使える。

ID トークンは Google のトークンエンドポイントから TLS で直接受け取るため、
OpenID Connect Core 3.1.3.7 に従い署名検証の代わりに TLS による発行元の確認を使い、
発行元・宛先・有効期限・nonce・メール確認済み・ドメインをすべて検証する。
"""

import base64
import functools
import json
import secrets
import time
import urllib.parse
import urllib.request

from flask import (
    Blueprint, abort, current_app, flash, g, redirect, render_template, request,
    session, url_for,
)

from . import behavior, events
from .db import get_db

bp = Blueprint("accounts", __name__)

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_ISSUERS = ("https://accounts.google.com", "accounts.google.com")


class LoginError(Exception):
    pass


# --- 設定 -----------------------------------------------------------------

def allowed_domain():
    return current_app.config["ALLOWED_EMAIL_DOMAIN"].lower().lstrip("@")


def google_enabled():
    return bool(current_app.config.get("GOOGLE_CLIENT_ID") and current_app.config.get("GOOGLE_CLIENT_SECRET"))


def staff_emails():
    """学生課として Google ログインできるメールアドレスの一覧(環境変数 STAFF_EMAILS)。"""
    raw = current_app.config.get("STAFF_EMAILS") or ""
    return {e.strip().lower() for e in raw.replace("\n", ",").split(",") if e.strip()}


def staff_google_enabled():
    return google_enabled() and bool(staff_emails())


def dev_login_enabled():
    return bool(current_app.config.get("ALLOW_DEV_LOGIN"))


def _redirect_uri():
    return current_app.config.get("GOOGLE_REDIRECT_URI") or url_for(
        "accounts.google_callback", _external=True
    )


def _safe_next(value):
    """ログイン後の戻り先。サイト内のパスだけを許可する。"""
    if value and value.startswith("/") and not value.startswith("//") and "\\" not in value:
        return value
    return url_for("public.index")


# --- ログイン中のユーザー --------------------------------------------------

def current_user():
    if "current_user" not in g:
        user_id = session.get("user_id")
        g.current_user = (
            get_db().execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
            if user_id else None
        )
        if user_id and g.current_user is None:
            session.pop("user_id", None)
    return g.current_user


def _merge_browser_history(from_visitor, to_visitor):
    """ログイン前にこのブラウザーで記録した閲覧・お気に入りをアカウントへ移す。"""
    db = get_db()
    db.execute(
        "INSERT INTO event_views (visitor_id, event_id, view_date, first_viewed_at, last_viewed_at) "
        "SELECT ?, event_id, view_date, first_viewed_at, last_viewed_at FROM event_views "
        "WHERE visitor_id = ? ON CONFLICT (visitor_id, event_id, view_date) DO UPDATE SET "
        "first_viewed_at = MIN(event_views.first_viewed_at, excluded.first_viewed_at), "
        "last_viewed_at = MAX(event_views.last_viewed_at, excluded.last_viewed_at)",
        (to_visitor, from_visitor),
    )
    db.execute(
        "INSERT INTO event_favorites (visitor_id, event_id, created_at) "
        "SELECT ?, event_id, created_at FROM event_favorites WHERE visitor_id = ? "
        "ON CONFLICT (visitor_id, event_id) DO NOTHING",
        (to_visitor, from_visitor),
    )
    db.execute("DELETE FROM event_views WHERE visitor_id = ?", (from_visitor,))
    db.execute("DELETE FROM event_favorites WHERE visitor_id = ?", (from_visitor,))


def login_user(*, email, name, google_sub):
    """アカウントを作成または更新し、ログイン状態にする。"""
    email = email.strip().lower()
    db = get_db()
    now = events.now_jst().strftime(events.DATETIME_FORMAT)
    browser_visitor = behavior.get_visitor_id(create=False)
    # 別のアカウントがこのブラウザーで使っていた ID は引き継がない(共有端末で他人の履歴が混ざるのを防ぐ)
    if browser_visitor and db.execute(
        "SELECT 1 FROM users WHERE visitor_id = ?", (browser_visitor,)
    ).fetchone():
        browser_visitor = None

    user = db.execute(
        "SELECT * FROM users WHERE google_sub = ? OR email = ? ORDER BY google_sub = ? DESC",
        (google_sub, email, google_sub),
    ).fetchone()
    if user is None:
        visitor_id = browser_visitor or secrets.token_hex(24)
        cur = db.execute(
            "INSERT INTO users (email, name, google_sub, visitor_id, created_at, last_login_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (email, name, google_sub, visitor_id, now, now),
        )
        user_id = cur.lastrowid
    else:
        user_id, visitor_id = user["id"], user["visitor_id"]
        db.execute(
            "UPDATE users SET email = ?, name = ?, google_sub = ?, last_login_at = ? WHERE id = ?",
            (email, name, google_sub, now, user_id),
        )
        if browser_visitor and browser_visitor != visitor_id:
            _merge_browser_history(browser_visitor, visitor_id)

    # ログイン前に同じメールアドレスで申し込んだ分も、このアカウントの申込として扱う
    db.execute(
        "UPDATE registrations SET user_id = ? WHERE user_id IS NULL AND email = ?",
        (user_id, email),
    )
    db.commit()

    csrf = session.get("_csrf_token")
    session.clear()  # ログイン前のセッションを引き継がない(セッション固定攻撃対策)
    if csrf:
        session["_csrf_token"] = csrf
    session["user_id"] = user_id
    session.permanent = True
    g.pop("current_user", None)
    g.pop("ds_visitor_id", None)


def logout_user():
    session.pop("user_id", None)
    g.pop("current_user", None)
    # 共有の端末で次の人に履歴が見えないよう、このブラウザーの匿名 ID を新しくする
    g.ds_visitor_id = secrets.token_hex(24)
    g.ds_write_cookie = True


# --- Google との通信 --------------------------------------------------------

def _fetch_token(code):
    """認可コードを Google のトークンエンドポイントで ID トークンに交換する。"""
    body = urllib.parse.urlencode({
        "code": code,
        "client_id": current_app.config["GOOGLE_CLIENT_ID"],
        "client_secret": current_app.config["GOOGLE_CLIENT_SECRET"],
        "redirect_uri": _redirect_uri(),
        "grant_type": "authorization_code",
    }).encode()
    req = urllib.request.Request(
        GOOGLE_TOKEN_URL, data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=10) as res:
        return json.load(res)


def _decode_id_token(id_token):
    try:
        payload = id_token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return json.loads(base64.urlsafe_b64decode(payload))
    except (IndexError, ValueError):
        raise LoginError("Google から受け取った情報を読み取れませんでした。") from None


def validate_claims(claims, *, nonce, staff=False):
    """ID トークンの中身を検証し、(email, name, sub) を返す。

    staff=True のときは、学生のドメインの代わりに学生課の登録メールアドレス(STAFF_EMAILS)で判定する。
    """
    if claims.get("iss") not in GOOGLE_ISSUERS:
        raise LoginError("ログイン情報の発行元が正しくありません。")
    if claims.get("aud") != current_app.config["GOOGLE_CLIENT_ID"]:
        raise LoginError("ログイン情報の宛先が正しくありません。")
    if not isinstance(claims.get("exp"), (int, float)) or claims["exp"] < time.time():
        raise LoginError("ログイン情報の有効期限が切れています。もう一度ログインしてください。")
    if not nonce or claims.get("nonce") != nonce:
        raise LoginError("ログイン情報の確認に失敗しました。もう一度ログインしてください。")
    email = (claims.get("email") or "").lower()
    if claims.get("email_verified") is not True or not email:
        raise LoginError("メールアドレスが確認されていない Google アカウントです。")
    if staff:
        if email not in staff_emails():
            raise LoginError("このアカウントは学生課の担当者として登録されていません。")
        if not claims.get("sub"):
            raise LoginError("Google アカウントの情報が不足しています。")
        return email, (claims.get("name") or email.split("@")[0])[:50], str(claims["sub"])
    domain = allowed_domain()
    # hd は Google Workspace(大学)が管理するアカウントにだけ付く。個人の Google アカウントは拒否する
    if not email.endswith("@" + domain) or not claims.get("hd"):
        raise LoginError(f"大学のアカウント(@{domain})でログインしてください。")
    if not claims.get("sub"):
        raise LoginError("Google アカウントの情報が不足しています。")
    return email, (claims.get("name") or email.split("@")[0])[:50], str(claims["sub"])


# --- 画面 -----------------------------------------------------------------

@bp.route("/login")
def login():
    if current_user():
        return redirect(_safe_next(request.args.get("next")))
    return render_template(
        "accounts/login.html",
        next=_safe_next(request.args.get("next")),
        google_enabled=google_enabled(),
        dev_login=dev_login_enabled(),
        domain=allowed_domain(),
    )


@bp.route("/login/google")
def google_start():
    staff = request.args.get("for") == "staff"
    if not (staff_google_enabled() if staff else google_enabled()):
        abort(404)
    state, nonce = secrets.token_urlsafe(24), secrets.token_urlsafe(24)
    session["oauth"] = {
        "state": state, "nonce": nonce, "staff": staff,
        "next": (request.args.get("next") if staff else None) or _safe_next(request.args.get("next")),
    }
    params = {
        "client_id": current_app.config["GOOGLE_CLIENT_ID"],
        "redirect_uri": _redirect_uri(),
        "response_type": "code",
        "scope": "openid email profile",
        "state": state,
        "nonce": nonce,
        "prompt": "select_account",
    }
    if not staff:
        params["hd"] = allowed_domain()  # 大学のアカウントを選びやすくするヒント(検証は validate_claims で行う)
    return redirect(f"{GOOGLE_AUTH_URL}?{urllib.parse.urlencode(params)}")


@bp.route("/login/google/callback")
def google_callback():
    oauth = session.pop("oauth", None)
    if not google_enabled():
        abort(404)
    try:
        if request.args.get("error"):
            raise LoginError("ログインがキャンセルされました。")
        if not oauth or not secrets.compare_digest(request.args.get("state", ""), oauth["state"]):
            raise LoginError("ログインの有効期限が切れました。もう一度お試しください。")
        code = request.args.get("code")
        if not code:
            raise LoginError("ログインに失敗しました。もう一度お試しください。")
        try:
            token = _fetch_token(code)
        except Exception:
            current_app.logger.warning("Google のトークン取得に失敗しました", exc_info=True)
            raise LoginError("Google との通信に失敗しました。時間をおいてお試しください。") from None
        email, name, sub = validate_claims(
            _decode_id_token(token.get("id_token", "")), nonce=oauth["nonce"], staff=oauth.get("staff", False)
        )
    except LoginError as e:
        flash(str(e), "error")
        return redirect(url_for("admin.login" if oauth and oauth.get("staff") else "accounts.login"))
    if oauth.get("staff"):
        from .admin import _safe_admin_next, start_staff_session

        start_staff_session(name=name, email=email, method="google")
        flash(f"学生課の担当者 {name} さんとしてログインしました。", "info")
        return redirect(_safe_admin_next(oauth["next"]))
    login_user(email=email, name=name, google_sub=sub)
    flash(f"{name} さんとしてログインしました。", "info")
    return redirect(oauth["next"])


@bp.route("/login/dev", methods=["POST"])
def dev_login():
    """Google の設定なしで動作確認するための開発用ログイン。ALLOW_DEV_LOGIN のときだけ有効。"""
    if not dev_login_enabled():
        abort(404)
    email = (request.form.get("email") or "").strip().lower()
    domain = allowed_domain()
    if not email.endswith("@" + domain) or len(email) > 200 or " " in email:
        flash(f"@{domain} のメールアドレスを入力してください。", "error")
        return redirect(url_for("accounts.login"))
    login_user(email=email, name=email.split("@")[0], google_sub=f"dev:{email}")
    flash(f"開発用ログイン: {email} としてログインしました。", "info")
    return redirect(_safe_next(request.form.get("next")))


@bp.route("/logout", methods=["POST"])
def logout():
    logout_user()
    flash("ログアウトしました。", "info")
    return redirect(url_for("public.index"))


def login_required(view):
    @functools.wraps(view)
    def wrapped(**kwargs):
        if not current_user():
            return redirect(url_for("accounts.login", next=request.path))
        return view(**kwargs)

    return wrapped


@bp.route("/mypage")
@login_required
def mypage():
    user = current_user()
    db = get_db()
    registrations = db.execute(
        "SELECT r.id AS registration_id, r.created_at AS registered_at, e.* "
        "FROM registrations r JOIN events e ON e.id = r.event_id "
        "WHERE r.user_id = ? ORDER BY e.start_at DESC",
        (user["id"],),
    ).fetchall()
    submissions = db.execute(
        "SELECT * FROM events WHERE submitted_by_user_id = ? ORDER BY created_at DESC",
        (user["id"],),
    ).fetchall()
    now = events.now_jst().strftime(events.DATETIME_FORMAT)
    return render_template(
        "accounts/mypage.html", user=user, registrations=registrations,
        submissions=submissions, now=now,
    )


@bp.route("/mypage/registrations/<int:registration_id>/cancel", methods=["POST"])
@login_required
def cancel_registration(registration_id):
    user = current_user()
    db = get_db()
    row = db.execute(
        "SELECT r.id, e.title, e.start_at FROM registrations r JOIN events e ON e.id = r.event_id "
        "WHERE r.id = ? AND r.user_id = ?",
        (registration_id, user["id"]),
    ).fetchone()
    if row is None:
        abort(404)
    if events.parse_dt(row["start_at"]) <= events.now_jst():
        flash("開始したイベントの申込は取り消せません。主催者に連絡してください。", "error")
    else:
        events.delete_registration(registration_id)
        flash(f"「{row['title']}」の参加申込を取り消しました。", "info")
    return redirect(url_for("accounts.mypage"))


def init_app(app):
    app.register_blueprint(bp)

    @app.context_processor
    def account_context():
        return {"current_user": current_user(), "login_available": google_enabled() or dev_login_enabled()}
