"""明示的なCLI実行時だけ追加する、推薦・分析画面用の架空データ。"""

from datetime import timedelta

import click

from . import events
from .db import get_db

_DEMO_EVENTS = [
    (-14, "Pythonとデータ分析 入門", "学術・研究", "Python、pandasを使ったデータ分析の入門講座。データの読み込み、集計、グラフの可視化を体験します。", "site", 25, 20),
    (3, "Pythonで学ぶデータ可視化", "学術・研究", "Pythonとpandasでデータを集計し、matplotlibでグラフを作ります。データ分析と可視化の基礎を学ぶ実習です。", "site", 30, 12),
    (7, "機械学習・統計の勉強会", "学術・研究", "Pythonで機械学習を体験します。統計、回帰分析、分類モデルとデータの前処理を学びます。", "site", 20, 8),
    (12, "SQLとデータ分析 実践", "学術・研究", "SQLを使ってデータベースからデータを検索し、集計と可視化につなげる実習です。統計やPythonとの連携も紹介します。", "site", 35, 4),
    (-7, "自己分析とインターンシップ相談", "就職・キャリア", "自己分析、就職活動、インターンシップについて先輩と相談するキャリアイベントです。", "site", 60, 35),
    (5, "就活準備・面接練習会", "就職・キャリア", "就職活動に向けて自己分析と面接練習を行います。インターンシップの応募書類の書き方も扱います。", "site", 40, 18),
    (9, "音楽とサークル交流会", "部活動・サークル", "学生サークルの音楽ライブと交流会です。楽器演奏やバンド活動に興味がある学生を歓迎します。", "none", None, 0),
    (11, "地域清掃ボランティア", "ボランティア・地域", "地域の方と一緒に公園を清掃するボランティア活動です。環境保全と地域交流について考えます。", "external", 50, 0),
    (15, "地域交流ボランティア説明会", "ボランティア・地域", "地域交流や環境保全のボランティア活動を紹介します。公園の清掃活動などに参加する方法を説明します。", "site", 30, 6),
]


@click.command("seed-ds-demo")
def seed_ds_demo_command():
    """【DSデモ】の架空イベントを追加する。同じタイトルの重複追加はしない。"""
    db = get_db()
    now = events.now_jst().replace(second=0, microsecond=0)
    count = 0
    for index, (days, name, category, description, mode, capacity, registered) in enumerate(_DEMO_EVENTS):
        title = f"【DSデモ】{name}"
        if db.execute("SELECT 1 FROM events WHERE title=?", (title,)).fetchone():
            continue
        start = (now + timedelta(days=days)).replace(hour=16, minute=0)
        event_id, _ = events.create_event(
            {
                "title": title, "description": description, "category": category,
                "start_at": start.strftime(events.DATETIME_FORMAT),
                "end_at": (start + timedelta(hours=2)).strftime(events.DATETIME_FORMAT),
                "location": "オンライン（架空デモ）", "organizer_name": "DSデモ実行用の団体",
                "organizer_type": "学部・学科", "capacity": capacity,
                "registration_mode": mode,
                "registration_info": "架空の外部受付（デモでは申込不可）" if mode == "external" else None,
            }, source="office", status="approved",
        )
        signup_at = (min(start, now) - timedelta(days=3)).strftime(events.DATETIME_FORMAT)
        for number in range(registered):
            # 架空の過去申込も作るため、登録日時を指定して挿入する。
            db.execute(
                "INSERT INTO registrations(event_id,name,email,student_number,created_at) VALUES(?,?,?,?,?)",
                (event_id, f"デモ参加者{number + 1}", f"demo-{event_id}-{number}@example.invalid", None, signup_at),
            )
        for number in range((index % 4 + 1) * 3):
            visitor_id = f"demo-browser-{number:04d}"
            viewed_at = (now - timedelta(days=number % 3)).strftime(events.DATETIME_FORMAT)
            db.execute(
                "INSERT OR IGNORE INTO event_views(visitor_id,event_id,view_date,first_viewed_at,last_viewed_at) VALUES(?,?,?,?,?)",
                (visitor_id, event_id, viewed_at[:10], viewed_at, viewed_at),
            )
            if number % 3 == 0:
                db.execute(
                    "INSERT OR IGNORE INTO event_favorites(visitor_id,event_id,created_at) VALUES(?,?,?)",
                    (visitor_id, event_id, viewed_at),
                )
        db.commit()
        count += 1
    click.echo(f"架空のDSデモイベントを{count}件追加しました。実際の利用実績ではありません。")
