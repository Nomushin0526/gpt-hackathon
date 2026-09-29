"""学生課向け管理画面: 応募の審査、イベントの登録(紙掲示物の転載)・編集・削除。"""

import functools
import hmac

from flask import (
    Blueprint, abort, current_app, flash, redirect, render_template, request,
    session, url_for,
)

from . import events

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


@bp.route("/events/<int:event_id>")
@login_required
def review(event_id):
    event = events.get_event(event_id) or abort(404)
    return render_template("admin/review.html", event=event)


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
