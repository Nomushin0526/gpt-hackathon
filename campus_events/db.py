import sqlite3

import click
from flask import current_app, g


def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(current_app.config["DATABASE"])
        g.db.row_factory = sqlite3.Row
    return g.db


def close_db(_exc=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    db = get_db()
    with current_app.open_resource("schema.sql") as f:
        db.executescript(f.read().decode("utf-8"))


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
