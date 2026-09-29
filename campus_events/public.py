"""学生向け画面: イベント一覧・詳細・応募・審査状況の確認。"""

from flask import (
    Blueprint, Response, abort, current_app, flash, redirect, render_template,
    request, send_from_directory, url_for,
)

from . import events

bp = Blueprint("public", __name__)


@bp.route("/")
def index():
    filters = {
        "keyword": request.args.get("q", "").strip(),
        "category": request.args.get("category", ""),
        "organizer_type": request.args.get("organizer_type", ""),
        "period": request.args.get("period", "upcoming"),
    }
    if filters["period"] not in ("upcoming", "week", "month", "past"):
        filters["period"] = "upcoming"
    results = events.search_public_events(**filters)
    return render_template("index.html", events=results, filters=filters)


@bp.route("/events/<int:event_id>")
def detail(event_id):
    event = events.get_event(event_id)
    if event is None or event["status"] != "approved":
        abort(404)
    return render_template("detail.html", event=event)


@bp.route("/events/<int:event_id>/calendar.ics")
def ics(event_id):
    event = events.get_event(event_id)
    if event is None or event["status"] != "approved":
        abort(404)
    return Response(
        events.to_ics(event),
        mimetype="text/calendar",
        headers={"Content-Disposition": f"attachment; filename=event-{event_id}.ics"},
    )


@bp.route("/posters/<path:filename>")
def poster(filename):
    return send_from_directory(current_app.config["UPLOAD_FOLDER"], filename)


@bp.route("/submit", methods=["GET", "POST"])
def submit():
    if request.method == "POST":
        data, errors = events.validate_event_form(request.form, require_contact=True)
        if not request.form.get("agree"):
            errors.append("掲載ルールへの同意にチェックしてください。")
        poster = None
        if not errors:
            try:
                poster = events.save_poster(request.files.get("poster"))
            except ValueError as e:
                errors.append(str(e))
        if errors:
            for e in errors:
                flash(e, "error")
            return render_template("submit.html", form=request.form), 400
        _, receipt = events.create_event(
            data, source="student", status="pending", poster_filename=poster
        )
        return redirect(url_for("public.submitted", code=receipt))
    return render_template("submit.html", form={})


@bp.route("/submit/done/<code>")
def submitted(code):
    event = events.get_by_receipt(code)
    if event is None:
        abort(404)
    return render_template("submitted.html", event=event)


@bp.route("/status")
def status():
    code = request.args.get("code", "").strip()
    event = events.get_by_receipt(code) if code else None
    if code and event is None:
        flash("受付番号が見つかりません。入力内容をご確認ください。", "error")
    return render_template("status.html", code=code, event=event)


@bp.app_errorhandler(404)
def not_found(_e):
    return render_template("error.html", message="ページが見つかりません。"), 404


@bp.app_errorhandler(413)
def too_large(_e):
    return render_template("error.html", message="ファイルサイズが大きすぎます(5MBまで)。"), 413
