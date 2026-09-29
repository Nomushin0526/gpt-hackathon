"""イベントデータの定数・入力検証・DB 操作。"""

import os
import secrets
import uuid
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from flask import current_app
from werkzeug.utils import secure_filename

from .db import get_db

JST = ZoneInfo("Asia/Tokyo")
DATETIME_FORMAT = "%Y-%m-%dT%H:%M"

CATEGORIES = [
    "講演会・セミナー",
    "学術・研究",
    "就職・キャリア",
    "部活動・サークル",
    "学園祭・学内行事",
    "ボランティア・地域",
    "健康・医療",
    "国際交流",
    "その他",
]

ORGANIZER_TYPES = [
    "学生課・大学",
    "学部・学科",
    "部活動・サークル",
    "学生個人・有志",
    "学外団体",
]

STATUS_LABELS = {
    "pending": "審査待ち",
    "approved": "公開中",
    "rejected": "差し戻し",
}

SOURCE_LABELS = {
    "office": "学生課掲載",
    "student": "学生企画",
}

ALLOWED_IMAGE_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "webp"}

# 受付番号は読み間違えやすい文字(0/O, 1/I/L)を除いた英数字で構成する
_RECEIPT_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"


def now_jst():
    return datetime.now(JST).replace(tzinfo=None)


def parse_dt(value):
    return datetime.strptime(value, DATETIME_FORMAT)


def generate_receipt_code():
    return "".join(secrets.choice(_RECEIPT_ALPHABET) for _ in range(8))


def validate_event_form(form, *, require_contact):
    """フォーム入力を検証し、(データ, エラー一覧) を返す。"""
    errors = []
    data = {}

    def text(name, label, *, required=True, max_len=200):
        value = (form.get(name) or "").strip()
        if required and not value:
            errors.append(f"{label}を入力してください。")
        elif len(value) > max_len:
            errors.append(f"{label}は{max_len}文字以内で入力してください。")
        data[name] = value or None

    text("title", "イベント名", max_len=100)
    text("description", "イベント内容", max_len=4000)
    text("location", "開催場所", max_len=100)
    text("organizer_name", "主催者・団体名", max_len=100)
    text("contact_email", "連絡先メールアドレス", required=require_contact, max_len=200)
    text("registration_info", "申込方法", required=False, max_len=500)
    text("note_to_office", "学生課への連絡事項", required=False, max_len=1000)

    email = data["contact_email"]
    if email and ("@" not in email or " " in email):
        errors.append("連絡先メールアドレスの形式が正しくありません。")

    category = form.get("category")
    if category not in CATEGORIES:
        errors.append("カテゴリを選択してください。")
    data["category"] = category

    organizer_type = form.get("organizer_type")
    if organizer_type not in ORGANIZER_TYPES:
        errors.append("主催者の種別を選択してください。")
    data["organizer_type"] = organizer_type

    start_raw = (form.get("start_at") or "").strip()
    end_raw = (form.get("end_at") or "").strip()
    start = end = None
    try:
        start = parse_dt(start_raw)
    except ValueError:
        errors.append("開始日時を正しく入力してください。")
    if end_raw:
        try:
            end = parse_dt(end_raw)
        except ValueError:
            errors.append("終了日時を正しく入力してください。")
    if start and end and end <= start:
        errors.append("終了日時は開始日時より後にしてください。")
    data["start_at"] = start.strftime(DATETIME_FORMAT) if start else start_raw
    data["end_at"] = end.strftime(DATETIME_FORMAT) if end else (end_raw or None)

    capacity_raw = (form.get("capacity") or "").strip()
    data["capacity"] = None
    if capacity_raw:
        if capacity_raw.isdigit() and 0 < int(capacity_raw) <= 100000:
            data["capacity"] = int(capacity_raw)
        else:
            errors.append("定員は1以上の整数で入力してください。")

    data["requires_registration"] = 1 if form.get("requires_registration") else 0
    return data, errors


def save_poster(file_storage):
    """ポスター画像を保存してファイル名を返す。不正な場合は ValueError。"""
    if not file_storage or not file_storage.filename:
        return None
    filename = secure_filename(file_storage.filename)
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext not in ALLOWED_IMAGE_EXTENSIONS:
        raise ValueError("ポスター画像は PNG / JPEG / GIF / WebP 形式でアップロードしてください。")
    stored = f"{uuid.uuid4().hex}.{ext}"
    file_storage.save(os.path.join(current_app.config["UPLOAD_FOLDER"], stored))
    return stored


def delete_poster(filename):
    if not filename:
        return
    path = os.path.join(current_app.config["UPLOAD_FOLDER"], filename)
    if os.path.exists(path):
        os.remove(path)


_EVENT_COLUMNS = [
    "title", "description", "category", "start_at", "end_at", "location",
    "organizer_name", "organizer_type", "contact_email", "capacity",
    "requires_registration", "registration_info", "note_to_office",
]


def create_event(data, *, source, status, poster_filename=None):
    db = get_db()
    ts = now_jst().strftime(DATETIME_FORMAT)
    receipt = generate_receipt_code()
    while db.execute("SELECT 1 FROM events WHERE receipt_code = ?", (receipt,)).fetchone():
        receipt = generate_receipt_code()
    columns = _EVENT_COLUMNS + [
        "poster_filename", "source", "status", "receipt_code", "created_at", "updated_at",
    ]
    values = [data.get(c) for c in _EVENT_COLUMNS] + [
        poster_filename, source, status, receipt, ts, ts,
    ]
    cur = db.execute(
        f"INSERT INTO events ({', '.join(columns)}) VALUES ({', '.join('?' * len(columns))})",
        values,
    )
    db.commit()
    return cur.lastrowid, receipt


def update_event(event_id, data, *, poster_filename):
    db = get_db()
    assignments = ", ".join(f"{c} = ?" for c in _EVENT_COLUMNS)
    db.execute(
        f"UPDATE events SET {assignments}, poster_filename = ?, updated_at = ? WHERE id = ?",
        [data.get(c) for c in _EVENT_COLUMNS]
        + [poster_filename, now_jst().strftime(DATETIME_FORMAT), event_id],
    )
    db.commit()


def set_status(event_id, status, comment):
    db = get_db()
    db.execute(
        "UPDATE events SET status = ?, review_comment = ?, updated_at = ? WHERE id = ?",
        (status, comment or None, now_jst().strftime(DATETIME_FORMAT), event_id),
    )
    db.commit()


def delete_event(event_id):
    db = get_db()
    db.execute("DELETE FROM events WHERE id = ?", (event_id,))
    db.commit()


def get_event(event_id):
    return get_db().execute("SELECT * FROM events WHERE id = ?", (event_id,)).fetchone()


def get_by_receipt(code):
    return get_db().execute(
        "SELECT * FROM events WHERE receipt_code = ?", (code.strip().upper(),)
    ).fetchone()


def search_public_events(*, keyword="", category="", organizer_type="", period="upcoming"):
    """公開中のイベントを検索する。period: upcoming / week / month / past"""
    now = now_jst()
    now_s = now.strftime(DATETIME_FORMAT)
    # 終了日時がないイベントは開始日時を終了とみなす
    end_expr = "COALESCE(end_at, start_at)"
    sql = ["SELECT * FROM events WHERE status = 'approved'"]
    params = []

    if period == "past":
        sql.append(f"AND {end_expr} < ?")
        params.append(now_s)
    else:
        sql.append(f"AND {end_expr} >= ?")
        params.append(now_s)
        if period in ("week", "month"):
            days = 7 if period == "week" else 31
            sql.append("AND start_at < ?")
            params.append((now + timedelta(days=days)).strftime(DATETIME_FORMAT))

    if keyword:
        like = f"%{keyword}%"
        sql.append("AND (title LIKE ? OR description LIKE ? OR location LIKE ? OR organizer_name LIKE ?)")
        params += [like, like, like, like]
    if category in CATEGORIES:
        sql.append("AND category = ?")
        params.append(category)
    if organizer_type in ORGANIZER_TYPES:
        sql.append("AND organizer_type = ?")
        params.append(organizer_type)

    sql.append("ORDER BY start_at DESC" if period == "past" else "ORDER BY start_at ASC")
    return get_db().execute(" ".join(sql), params).fetchall()


def list_for_admin(status):
    order = "created_at ASC" if status == "pending" else "start_at DESC"
    return get_db().execute(
        f"SELECT * FROM events WHERE status = ? ORDER BY {order}", (status,)
    ).fetchall()


def count_by_status():
    rows = get_db().execute("SELECT status, COUNT(*) AS n FROM events GROUP BY status").fetchall()
    counts = {s: 0 for s in STATUS_LABELS}
    counts.update({r["status"]: r["n"] for r in rows})
    return counts


def to_ics(event):
    """iCalendar 形式の文字列を生成する(スマホのカレンダーに追加する用)。"""

    def esc(s):
        return (s or "").replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")

    start = parse_dt(event["start_at"])
    end = parse_dt(event["end_at"]) if event["end_at"] else start + timedelta(hours=1)
    fmt = "%Y%m%dT%H%M%S"
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Sagamihara Campus Events//JA",
        "BEGIN:VEVENT",
        f"UID:event-{event['id']}@campus-events",
        f"DTSTAMP:{datetime.now(ZoneInfo('UTC')).strftime(fmt)}Z",
        f"DTSTART;TZID=Asia/Tokyo:{start.strftime(fmt)}",
        f"DTEND;TZID=Asia/Tokyo:{end.strftime(fmt)}",
        f"SUMMARY:{esc(event['title'])}",
        f"LOCATION:{esc(event['location'])}",
        f"DESCRIPTION:{esc(event['description'])}",
        "END:VEVENT",
        "END:VCALENDAR",
    ]
    return "\r\n".join(lines) + "\r\n"
