"""既存画面に依存せず表示できる推薦・お気に入り画面。"""

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for

from . import behavior, events
from .recommendations import get_recommendations

bp = Blueprint("ds", __name__)


@bp.get("/recommendations")
def recommendations_page():
    behavior.purge_old_views()
    result = get_recommendations(behavior.get_visitor_id(), limit=12)
    return render_template("ds/recommendations.html", result=result)


@bp.get("/favorites")
def favorites_page():
    return render_template(
        "ds/favorites.html", favorites=behavior.list_favorites(behavior.get_visitor_id()),
        now=events.now_jst().strftime(events.DATETIME_FORMAT),
    )


@bp.post("/events/<int:event_id>/favorite")
def favorite(event_id):
    action = request.form.get("action", "")
    if action == "add":
        event = events.get_event(event_id)
        if event is None or event["status"] != "approved":
            abort(404)
        behavior.save_favorite(event_id)
        flash("お気に入りに保存しました。次のおすすめに反映します。", "info")
    elif action == "remove":
        behavior.remove_favorite(event_id)
        flash("お気に入りを解除しました。", "info")
    else:
        abort(400, "操作が正しくありません。")
    # 自由なURLは受け付けず、決められた戻り先だけを選ぶ。
    back = request.form.get("back", "recommendations")
    if back == "detail":
        return redirect(url_for("public.detail", event_id=event_id))
    if back == "favorites":
        return redirect(url_for("ds.favorites_page"))
    return redirect(url_for("ds.recommendations_page"))


@bp.post("/preferences/clear-history")
def clear_history():
    behavior.clear_preferences()
    flash("このブラウザーの閲覧履歴を削除しました。お気に入りは引き続き推薦に使われます。", "info")
    return redirect(url_for("ds.recommendations_page"))


@bp.post("/preferences/clear-all")
def clear_all():
    behavior.clear_preferences(include_favorites=True)
    flash("このブラウザーの閲覧履歴とお気に入りを削除しました。", "info")
    return redirect(url_for("ds.recommendations_page"))
