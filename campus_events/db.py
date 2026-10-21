import sqlite3

import click
from flask import current_app, g
from flask.cli import with_appcontext


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
    _migrate_data_science_tables()


def _migrate_data_science_tables():
    """IF NOT EXISTS だけの追加DDL。既存レコードを保持する。"""
    with current_app.open_resource("schema_ds.sql") as f:
        get_db().executescript(f.read().decode("utf-8"))


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
    if "location_lat" not in columns:
        db.execute("ALTER TABLE events ADD COLUMN location_lat REAL")
        db.execute("ALTER TABLE events ADD COLUMN location_lng REAL")
    if "users" not in tables:
        db.executescript(
            """
            CREATE TABLE users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT NOT NULL UNIQUE,
                name TEXT NOT NULL,
                google_sub TEXT NOT NULL UNIQUE,
                visitor_id TEXT NOT NULL UNIQUE,
                created_at TEXT NOT NULL,
                last_login_at TEXT NOT NULL
            );
            """
        )
    if "submitted_by_user_id" not in columns:
        db.execute("ALTER TABLE events ADD COLUMN submitted_by_user_id INTEGER REFERENCES users (id) ON DELETE SET NULL")
    if "audit_logs" not in tables:
        db.executescript("""
CREATE TABLE audit_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    actor_name TEXT NOT NULL,
    actor_email TEXT,
    auth_method TEXT,
    action TEXT NOT NULL,
    event_id INTEGER REFERENCES events (id) ON DELETE SET NULL,
    event_title TEXT,
    detail TEXT
);
CREATE INDEX idx_audit_logs_event ON audit_logs (event_id, id);
        """)
    if "campus_pins" not in tables:
        db.execute("CREATE TABLE campus_pins (name TEXT PRIMARY KEY, x REAL NOT NULL, y REAL NOT NULL)")
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
    reg_columns = {r["name"] for r in db.execute("PRAGMA table_info(registrations)")}
    if "user_id" not in reg_columns:
        db.execute("ALTER TABLE registrations ADD COLUMN user_id INTEGER REFERENCES users (id) ON DELETE SET NULL")
    db.commit()
    _migrate_data_science_tables()


@click.command("migrate-db")
def migrate_db_command():
    """登録済みデータを残して、追加テーブルを作成する。"""
    migrate_db()
    click.echo("既存データを保持してデータベースを更新しました。")


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


@click.command("apply-pins")
@with_appcontext
@click.option("--keep", is_flag=True, help="書き戻した後も、データベースの修正値を残す")
def apply_pins_command(keep):
    """管理画面で修正したピン位置を campus_events/places.py に書き戻す(チームで共有するため)。"""
    from . import places

    changed = places.apply_pins_to_source()
    if not changed:
        click.echo("書き戻すピンの修正はありません。")
        return
    if not keep:
        db = get_db()
        db.executemany("DELETE FROM campus_pins WHERE name = ?", [(n,) for n in changed])
        db.commit()
    click.echo(f"{len(changed)} 件のピン位置を places.py に書き戻しました:")
    for name in changed:
        click.echo(f"  - {name}")
    click.echo("git diff で確認し、コミットしてください。")


def init_app(app):
    app.teardown_appcontext(close_db)
    app.cli.add_command(init_db_command)
    app.cli.add_command(seed_db_command)
    app.cli.add_command(migrate_db_command)
    app.cli.add_command(apply_pins_command)
    with app.app_context():
        migrate_db()
