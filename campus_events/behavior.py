"""ブラウザーごとの閲覧・お気に入り。氏名やメールアドレスとは結び付けない。"""

import re
import secrets
from datetime import timedelta

import click
from flask import current_app, g, request, session
from itsdangerous import BadSignature, URLSafeTimedSerializer

from . import events
from .db import get_db

COOKIE_NAME = "campus_events_visitor"
_ID_PATTERN = re.compile(r"[0-9a-f]{48}\Z")


def _serializer():
    return URLSafeTimedSerializer(current_app.config["SECRET_KEY"], salt="campus-events-visitor-v1")


def get_visitor_id(*, create=True):
    """署名済みCookieを検証する。フォームからvisitor_idを受け取らない。"""
    if "ds_visitor_id" not in g:
        visitor_id = None
        cookie = request.cookies.get(COOKIE_NAME)
        if cookie:
            try:
                candidate = _serializer().loads(cookie, max_age=current_app.config["DS_COOKIE_MAX_AGE"])
                if isinstance(candidate, str) and _ID_PATTERN.fullmatch(candidate):
                    visitor_id = candidate
            except BadSignature:
                pass
        g.ds_visitor_id = visitor_id
    if g.ds_visitor_id is None and create:
        g.ds_visitor_id = secrets.token_hex(24)
        g.ds_write_cookie = True
    return g.ds_visitor_id


def purge_old_views(*, now=None, commit=True):
    now = now or events.now_jst()
    days = max(1, int(current_app.config.get("DS_HISTORY_DAYS", 90)))
    cutoff = (now.date() - timedelta(days=days - 1)).isoformat()
    cur = get_db().execute("DELETE FROM event_views WHERE view_date < ?", (cutoff,))
    if commit:
        get_db().commit()
    return cur.rowcount


def record_view(event_id):
    """公開詳細のGETから呼ぶ。管理者の閲覧は集計に含めない。"""
    if session.get("is_admin"):
        return
    now = events.now_jst()
    stamp = now.strftime(events.DATETIME_FORMAT)
    visitor_id = get_visitor_id()
    db = get_db()
    purge_old_views(now=now, commit=False)
    db.execute(
        """
        INSERT INTO event_views(visitor_id,event_id,view_date,first_viewed_at,last_viewed_at)
        SELECT ?, id, ?, ?, ? FROM events WHERE id = ? AND status = 'approved'
        ON CONFLICT(visitor_id,event_id,view_date)
        DO UPDATE SET last_viewed_at = excluded.last_viewed_at
        """,
        (visitor_id, now.date().isoformat(), stamp, stamp, event_id),
    )
    db.commit()


def favorite_ids(visitor_id=None):
    visitor_id = visitor_id or get_visitor_id(create=False)
    if not visitor_id:
        return set()
    return {row[0] for row in get_db().execute(
        "SELECT f.event_id FROM event_favorites f JOIN events e ON e.id=f.event_id "
        "WHERE f.visitor_id=? AND e.status='approved'", (visitor_id,)
    )}


def save_favorite(event_id):
    visitor_id = get_visitor_id()
    get_db().execute(
        "INSERT INTO event_favorites(visitor_id,event_id,created_at) "
        "SELECT ?,id,? FROM events WHERE id=? AND status='approved' "
        "ON CONFLICT(visitor_id,event_id) DO NOTHING",
        (visitor_id, events.now_jst().strftime(events.DATETIME_FORMAT), event_id),
    )
    get_db().commit()


def remove_favorite(event_id):
    visitor_id = get_visitor_id(create=False)
    if visitor_id:
        get_db().execute("DELETE FROM event_favorites WHERE visitor_id=? AND event_id=?", (visitor_id, event_id))
        get_db().commit()


def list_favorites(visitor_id):
    return get_db().execute(
        "SELECT e.*, f.created_at AS favorited_at, "
        "(SELECT COUNT(*) FROM registrations r WHERE r.event_id=e.id) AS registered "
        "FROM event_favorites f JOIN events e ON e.id=f.event_id "
        "WHERE f.visitor_id=? AND e.status='approved' ORDER BY e.start_at DESC,e.id DESC",
        (visitor_id,),
    ).fetchall()


def clear_preferences(*, include_favorites=False):
    visitor_id = get_visitor_id(create=False)
    if visitor_id:
        get_db().execute("DELETE FROM event_views WHERE visitor_id=?", (visitor_id,))
        if include_favorites:
            get_db().execute("DELETE FROM event_favorites WHERE visitor_id=?", (visitor_id,))
        get_db().commit()
    if include_favorites:
        g.ds_visitor_id = secrets.token_hex(24)
        g.ds_write_cookie = True


def init_app(app):
    @app.after_request
    def save_visitor_cookie(response):
        if g.get("ds_write_cookie"):
            response.set_cookie(
                COOKIE_NAME, _serializer().dumps(g.ds_visitor_id),
                max_age=app.config["DS_COOKIE_MAX_AGE"], httponly=True,
                secure=bool(app.config.get("SESSION_COOKIE_SECURE")) or request.is_secure,
                samesite="Lax", path="/",
            )
        if request.endpoint and (
            request.endpoint.startswith("ds.") or request.endpoint in
            {"public.detail", "public.index", "admin.analytics_dashboard", "admin.analytics_csv", "admin.analytics_api"}
        ):
            response.headers["Cache-Control"] = "private, no-store"
            response.vary.add("Cookie")
        return response

    @app.context_processor
    def ds_context():
        return {"ds_favorite_ids": favorite_ids(), "ds_history_days": app.config["DS_HISTORY_DAYS"]}

    @app.cli.command("purge-view-history")
    def purge_view_history_command():
        """保存期間を過ぎた閲覧履歴を削除する。イベント・申込は対象外。"""
        click.echo(f"古い閲覧履歴を{purge_old_views()}件削除しました。")
