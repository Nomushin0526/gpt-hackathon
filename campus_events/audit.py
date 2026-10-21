"""学生課の操作履歴(誰が・いつ・何をしたか)。学生課の画面だけで表示する。"""

from flask import session

from . import events
from .db import get_db

ACTION_LABELS = {
    "login": "ログイン",
    "approve": "承認して公開",
    "reject": "差し戻し",
    "to_pending": "審査待ちに戻す",
    "create": "イベントを登録",
    "edit": "イベントを編集",
    "delete": "イベントを削除",
    "cancel_registration": "参加申込を取消",
    "set_pin": "ピン位置を設定",
    "reset_pin": "ピン位置を初期化",
}

STATUS_ACTIONS = {"approved": "approve", "rejected": "reject", "pending": "to_pending"}

METHOD_LABELS = {
    "google": "Google",
    "password": "共通パスワード(自己申告)",
}


def current_staff():
    """ログイン中の学生課職員。{name, email, method} か None。"""
    if not session.get("is_admin"):
        return None
    return {
        "name": session.get("staff_name") or "(名前未記録)",
        "email": session.get("staff_email"),
        "method": session.get("staff_method") or "password",
    }


def record(action, *, event=None, event_id=None, event_title=None, detail=None):
    staff = current_staff() or {"name": "(不明)", "email": None, "method": None}
    if event is not None:
        event_id, event_title = event["id"], event["title"]
    db = get_db()
    db.execute(
        "INSERT INTO audit_logs (created_at, actor_name, actor_email, auth_method, action, "
        "event_id, event_title, detail) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            events.now_jst().strftime("%Y-%m-%dT%H:%M:%S"), staff["name"], staff["email"],
            staff["method"], action, event_id, event_title, detail,
        ),
    )
    db.commit()


def list_logs(*, event_id=None, limit=200):
    if event_id is not None:
        return get_db().execute(
            "SELECT * FROM audit_logs WHERE event_id = ? ORDER BY id DESC LIMIT ?", (event_id, limit)
        ).fetchall()
    return get_db().execute("SELECT * FROM audit_logs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()


def last_review(event_id):
    """そのイベントで最後に行われた審査(承認・差し戻し・審査待ちに戻す)。"""
    return get_db().execute(
        "SELECT * FROM audit_logs WHERE event_id = ? AND action IN ('approve', 'reject', 'to_pending') "
        "ORDER BY id DESC LIMIT 1",
        (event_id,),
    ).fetchone()


def last_reviewers(event_ids):
    """管理画面の一覧用: イベントごとの最後の審査者。"""
    if not event_ids:
        return {}
    placeholders = ",".join("?" * len(event_ids))
    rows = get_db().execute(
        f"SELECT a.* FROM audit_logs a JOIN ("
        f"  SELECT event_id, MAX(id) AS id FROM audit_logs"
        f"  WHERE action IN ('approve', 'reject', 'to_pending') AND event_id IN ({placeholders})"
        f"  GROUP BY event_id) last ON last.id = a.id",
        list(event_ids),
    ).fetchall()
    return {r["event_id"]: r for r in rows}
