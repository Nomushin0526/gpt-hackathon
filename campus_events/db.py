import sqlite3

import click
from flask import current_app, g


def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(current_app.config["DATABASE"])
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


def close_db(_exc=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    db = get_db()
    with current_app.open_resource("schema.sql") as f:
        db.executescript(f.read().decode("utf-8"))


def migrate_db():
    """既存のデータベースを最新の構成に合わせる(登録済みのデータは残す)。

    データベースがまだ無い場合は新規に作成する。
    """
    db = get_db()
    tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    if "events" not in tables:
        init_db()
        return
    columns = {r["name"] for r in db.execute("PRAGMA table_info(events)")}
    if "registration_mode" not in columns:
        # 旧版の「事前申込が必要」チェックは外部申込として引き継ぐ
        db.execute("ALTER TABLE events ADD COLUMN registration_mode TEXT NOT NULL DEFAULT 'none'")
        if "requires_registration" in columns:
            db.execute("UPDATE events SET registration_mode = 'external' WHERE requires_registration = 1")
    if "registrations" not in tables:
        db.executescript(
            """
            CREATE TABLE registrations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id INTEGER NOT NULL REFERENCES events (id) ON DELETE CASCADE,
                name TEXT NOT NULL,
                email TEXT NOT NULL,
                student_number TEXT,
                created_at TEXT NOT NULL,
                UNIQUE (event_id, email)
            );
            """
        )
    db.commit()


@click.command("init-db")
def init_db_command():
    """データベースを初期化する(既存データは削除される)。"""
    init_db()
    click.echo("データベースを初期化しました。")


@click.command("seed-db")
def seed_db_command():
    """動作確認用のサンプルイベントを投入する。"""
    from .seed import seed

    seed()
    click.echo("サンプルデータを投入しました。")


def init_app(app):
    app.teardown_appcontext(close_db)
    app.cli.add_command(init_db_command)
    app.cli.add_command(seed_db_command)
    with app.app_context():
        migrate_db()
