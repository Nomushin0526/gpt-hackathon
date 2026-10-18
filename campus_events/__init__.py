import os
import secrets

from flask import Flask, abort, request, session

from . import db, places
from .events import (
    CATEGORIES, ORGANIZER_TYPES, REGISTRATION_MODES, SOURCE_LABELS,
    STATUS_LABELS, is_full, is_open_for_registration, parse_dt,
)

WEEKDAYS = "月火水木金土日"


def create_app(test_config=None):
    app = Flask(__name__, instance_relative_config=True)
    app.config.from_mapping(
        SECRET_KEY=os.environ.get("SECRET_KEY", "dev-secret-change-me"),
        DATABASE=os.path.join(app.instance_path, "campus_events.sqlite"),
        UPLOAD_FOLDER=os.path.join(app.instance_path, "uploads"),
        MAX_CONTENT_LENGTH=5 * 1024 * 1024,
        # 学生課の管理画面パスワード。本番では必ず環境変数で設定すること。
        ADMIN_PASSWORD=os.environ.get("ADMIN_PASSWORD", "admin"),
        # 学外の開催場所の検索に使う Photon(OpenStreetMap)互換の API。空にすると学内施設のみ候補に出す
        PLACE_SEARCH_URL=os.environ.get("PLACE_SEARCH_URL", "https://photon.komoot.io/api/"),
        DS_HISTORY_DAYS=90,
        DS_COOKIE_MAX_AGE=180 * 24 * 60 * 60,
        DS_VIEW_WEIGHT=1.0,
        DS_VIEW_CAP=3,
        DS_FAVORITE_WEIGHT=5.0,
    )
    if test_config:
        app.config.update(test_config)

    os.makedirs(app.instance_path, exist_ok=True)
    os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)

    db.init_app(app)

    @app.before_request
    def csrf_protect():
        if request.method == "POST" and not app.config.get("WTF_CSRF_DISABLED"):
            token = session.get("_csrf_token")
            if not token or token != request.form.get("_csrf_token"):
                abort(400, "フォームの有効期限が切れました。ページを再読み込みしてください。")

    def csrf_token():
        if "_csrf_token" not in session:
            session["_csrf_token"] = secrets.token_urlsafe(32)
        return session["_csrf_token"]

    def format_dt(value, with_weekday=True):
        if not value:
            return ""
        dt = parse_dt(value)
        weekday = f"({WEEKDAYS[dt.weekday()]})" if with_weekday else ""
        return f"{dt.year}/{dt.month}/{dt.day}{weekday} {dt:%H:%M}"

    def format_range(start, end):
        if not end:
            return format_dt(start)
        s, e = parse_dt(start), parse_dt(end)
        if s.date() == e.date():
            return f"{format_dt(start)}〜{e:%H:%M}"
        return f"{format_dt(start)} 〜 {format_dt(end)}"

    app.jinja_env.filters["dt"] = format_dt
    app.jinja_env.globals.update(
        csrf_token=csrf_token,
        format_range=format_range,
        CATEGORIES=CATEGORIES,
        ORGANIZER_TYPES=ORGANIZER_TYPES,
        STATUS_LABELS=STATUS_LABELS,
        SOURCE_LABELS=SOURCE_LABELS,
        REGISTRATION_MODES=REGISTRATION_MODES,
        CAMPUS_MAP_IMAGE=places.CAMPUS_MAP_IMAGE,
        is_full=is_full,
        is_open_for_registration=is_open_for_registration,
        map_info=places.map_info,
    )

    from . import admin, behavior, data_science, public

    app.register_blueprint(public.bp)
    app.register_blueprint(admin.bp)
    app.register_blueprint(data_science.bp)
    behavior.init_app(app)
    from .seed_ds import seed_ds_demo_command
    app.cli.add_command(seed_ds_demo_command)
    return app
