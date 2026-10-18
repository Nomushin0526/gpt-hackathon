"""学生課向け管理画面: 応募の審査、イベントの登録(紙掲示物の転載)・編集・削除。"""

import csv
import functools
import hmac
import io
import re
from datetime import date, timedelta

from flask import (
    Blueprint, Response, abort, current_app, flash, redirect, render_template,
    request, session, url_for, jsonify,
)

from . import analytics, behavior, events, places

bp = Blueprint("admin", __name__, url_prefix="/admin")


def login_required(view):
    @functools.wraps(view)
    def wrapped(**kwargs):
        if not session.get("is_admin"):
            return redirect(url_for("admin.login", next=request.path))
        return view(**kwargs)

    return wrapped


@bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        password = request.form.get("password", "")
        if hmac.compare_digest(password.encode(), current_app.config["ADMIN_PASSWORD"].encode()):
            session["is_admin"] = True
            next_url = request.args.get("next", "")
            # オープンリダイレクト対策: 管理画面内のパスのみ許可
            if not next_url.startswith("/admin"):
                next_url = url_for("admin.dashboard")
            return redirect(next_url)
        flash("パスワードが違います。", "error")
    return render_template("admin/login.html")


@bp.route("/logout", methods=["POST"])
def logout():
    session.pop("is_admin", None)
    flash("ログアウトしました。", "info")
    return redirect(url_for("public.index"))


@bp.route("/")
@login_required
def dashboard():
    tab = request.args.get("tab", "pending")
    if tab not in events.STATUS_LABELS:
        tab = "pending"
    return render_template(
        "admin/dashboard.html",
        tab=tab,
        events=events.list_for_admin(tab),
        counts=events.count_by_status(),
    )


def _analytics_data():
    """期間はイベントの開催開始日。空欄なら前90日〜後30日を使う。"""
    today = events.now_jst().date()
    start = request.args.get("start") or (today - timedelta(days=90)).isoformat()
    end = request.args.get("end") or (today + timedelta(days=30)).isoformat()
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", start) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", end):
        raise ValueError("日付を YYYY-MM-DD 形式で入力してください。")
    try:
        start_date, end_date = date.fromisoformat(start), date.fromisoformat(end)
    except ValueError:
        raise ValueError("存在する日付を入力してください。") from None
    if start_date > end_date:
        raise ValueError("開始日は終了日以前にしてください。")
    behavior.purge_old_views()
    return analytics.build_dashboard(start_date, end_date)


@bp.get("/analytics")
@login_required
def analytics_dashboard():
    try:
        report = _analytics_data()
    except ValueError as error:
        return render_template("ds/error.html", message=str(error)), 400
    return render_template("ds/analytics.html", report=report)


@bp.get("/analytics.json")
@login_required
def analytics_api():
    try:
        return jsonify(_analytics_data())
    except ValueError as error:
        return jsonify(error=str(error)), 400


@bp.get("/analytics.csv")
@login_required
def analytics_csv():
    try:
        report = _analytics_data()
    except ValueError as error:
        return render_template("ds/error.html", message=str(error)), 400
    buf = io.StringIO(newline="")
    writer = csv.writer(buf)
    writer.writerow([
        "イベントID", "イベント名", "カテゴリ", "開催開始日時", "参加申込方法",
        "現在のサイト内申込件数", "定員", "定員充足率(%)", "保存期間内の閲覧数", "現在のお気に入り数",
    ])
    for row in report["ranking"]:
        values = [
            row["id"], row["title"], row["category"], row["start_at"],
            events.REGISTRATION_MODES.get(row["registration_mode"], row["registration_mode"]),
            row["registered"], row["capacity"], row["occupancy_percent"], row["view_count"], row["favorite_count"],
        ]
        writer.writerow([analytics.csv_safe(value) for value in values])
    return Response(
        "\ufeff" + buf.getvalue(), mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=event-analytics.csv"},
    )


@bp.route("/events/<int:event_id>")
@login_required
def review(event_id):
    event = events.get_event(event_id) or abort(404)
    return render_template(
        "admin/review.html", event=event, registrations=events.list_registrations(event_id)
    )


@bp.route("/events/<int:event_id>/registrations.csv")
@login_required
def registrations_csv(event_id):
    event = events.get_event(event_id) or abort(404)
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["No.", "氏名", "学籍番号", "メールアドレス", "申込日時"])
    for i, r in enumerate(events.list_registrations(event_id), 1):
        writer.writerow([i, r["name"], r["student_number"] or "", r["email"], r["created_at"].replace("T", " ")])
    # Excel で文字化けしないよう BOM 付き UTF-8 で出力する
    return Response(
        "\ufeff" + buf.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename=registrations-{event['id']}.csv"},
    )


@bp.route("/registrations/<int:registration_id>/delete", methods=["POST"])
@login_required
def delete_registration(registration_id):
    event_id = request.form.get("event_id", type=int)
    events.delete_registration(registration_id)
    flash("参加申込を取り消しました。", "info")
    return redirect(url_for("admin.review", event_id=event_id) if event_id else url_for("admin.dashboard"))


@bp.route("/events/<int:event_id>/status", methods=["POST"])
@login_required
def change_status(event_id):
    events.get_event(event_id) or abort(404)
    status = request.form.get("status")
    if status not in events.STATUS_LABELS:
        abort(400)
    comment = request.form.get("review_comment", "").strip()
    if status == "rejected" and not comment:
        flash("差し戻す場合は理由をコメントに記入してください。", "error")
        return redirect(url_for("admin.review", event_id=event_id))
    events.set_status(event_id, status, comment)
    flash(f"ステータスを「{events.STATUS_LABELS[status]}」に変更しました。", "info")
    return redirect(url_for("admin.dashboard", tab="pending"))


@bp.route("/events/new", methods=["GET", "POST"])
@login_required
def new_event():
    if request.method == "POST":
        data, errors = events.validate_event_form(request.form, require_contact=False)
        poster = None
        if not errors:
            try:
                poster = events.save_poster(request.files.get("poster"))
            except ValueError as e:
                errors.append(str(e))
        if errors:
            for e in errors:
                flash(e, "error")
            return render_template("admin/edit.html", form=request.form, event=None), 400
        event_id, _ = events.create_event(
            data, source="office", status="approved", poster_filename=poster
        )
        flash("イベントを登録し、公開しました。", "info")
        return redirect(url_for("public.detail", event_id=event_id))
    return render_template(
        "admin/edit.html", form={"organizer_type": "学生課・大学"}, event=None
    )


@bp.route("/events/<int:event_id>/edit", methods=["GET", "POST"])
@login_required
def edit_event(event_id):
    event = events.get_event(event_id) or abort(404)
    if request.method == "POST":
        data, errors = events.validate_event_form(request.form, require_contact=False)
        poster = event["poster_filename"]
        if not errors:
            try:
                new_poster = events.save_poster(request.files.get("poster"))
            except ValueError as e:
                errors.append(str(e))
            else:
                if new_poster or request.form.get("remove_poster"):
                    events.delete_poster(poster)
                    poster = new_poster
        if errors:
            for e in errors:
                flash(e, "error")
            return render_template("admin/edit.html", form=request.form, event=event), 400
        events.update_event(event_id, data, poster_filename=poster)
        flash("イベント情報を更新しました。", "info")
        return redirect(url_for("admin.review", event_id=event_id))
    return render_template("admin/edit.html", form=dict(event), event=event)


@bp.route("/events/<int:event_id>/delete", methods=["POST"])
@login_required
def delete_event(event_id):
    event = events.get_event(event_id) or abort(404)
    events.delete_event(event_id)
    events.delete_poster(event["poster_filename"])
    flash("イベントを削除しました。", "info")
    return redirect(url_for("admin.dashboard"))


@bp.route("/campus-map", methods=["GET", "POST"])
@login_required
def campus_map():
    """キャンパスマップ上の施設のピン位置を設定する。"""
    if request.method == "POST":
        name = request.form.get("name", "")
        x = request.form.get("x", type=float)
        y = request.form.get("y", type=float)
        if request.form.get("reset"):
            places.reset_campus_pin(name)
            flash(f"「{name}」のピンを初期位置に戻しました。", "info")
        elif x is None or y is None:
            flash("ピンの位置を地図上でクリックして指定してください。", "error")
        else:
            try:
                places.set_campus_pin(name, x, y)
                flash(f"「{name}」のピンの位置を保存しました。", "info")
            except ValueError as e:
                flash(str(e), "error")
        return redirect(url_for("admin.campus_map", place=name))
    return render_template(
        "admin/campus_map.html",
        places=places.campus_places(),
        selected=request.args.get("place", ""),
        map_size=places.CAMPUS_MAP_SIZE,
    )
