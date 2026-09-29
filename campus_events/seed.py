"""動作確認用のサンプルデータ(内容はすべて架空)。"""

from datetime import timedelta

from .events import DATETIME_FORMAT, create_event, now_jst, set_status

_SAMPLES = [
    dict(days=2, hour=17, hours=1.5, title="【サンプル】就活スタートアップ講座", category="就職・キャリア",
         location="講義棟 大講義室", organizer_name="学生課 キャリア支援担当", organizer_type="学生課・大学",
         description="これから就職活動を始める学生向けの講座です。自己分析の進め方とインターンシップの探し方を解説します。",
         source="office", status="approved", requires_registration=1, capacity=120,
         registration_info="学生課窓口または掲示のQRコードから申込"),
    dict(days=5, hour=12, hours=1, title="【サンプル】昼休みミニコンサート", category="部活動・サークル",
         location="学生食堂前 広場", organizer_name="管弦楽部(サンプル)", organizer_type="部活動・サークル",
         description="昼休みに弦楽四重奏をお届けします。出入り自由です。", source="student", status="approved"),
    dict(days=9, hour=13, hours=3, title="【サンプル】献血・健康チェックデー", category="健康・医療",
         location="キャンパス内 献血バス", organizer_name="学生ボランティア有志(サンプル)", organizer_type="学生個人・有志",
         description="献血へのご協力をお願いします。血圧測定コーナーもあります。", source="student", status="approved"),
    dict(days=16, hour=18, hours=2, title="【サンプル】留学生と話そう! 国際交流カフェ", category="国際交流",
         location="図書館 ラーニングコモンズ", organizer_name="学生課 国際交流担当", organizer_type="学生課・大学",
         description="留学生と気軽に話せる交流会です。英語が苦手でも大丈夫。", source="office", status="approved"),
    dict(days=12, hour=16, hours=2, title="【サンプル】ボードゲーム交流会", category="部活動・サークル",
         location="サークル棟 集会室", organizer_name="ボードゲーム同好会(サンプル)", organizer_type="部活動・サークル",
         description="新入生歓迎を兼ねたボードゲーム会です。", source="student", status="pending",
         note_to_office="集会室の使用申請は別途提出済みです。"),
]


def seed():
    base = now_jst().replace(minute=0, second=0, microsecond=0)
    for s in _SAMPLES:
        start = (base + timedelta(days=s["days"])).replace(hour=s["hour"])
        end = start + timedelta(hours=s["hours"])
        data = {k: v for k, v in s.items() if k not in ("days", "hour", "hours", "source", "status")}
        data.update(
            start_at=start.strftime(DATETIME_FORMAT),
            end_at=end.strftime(DATETIME_FORMAT),
            contact_email="sample@example.com",
        )
        data.setdefault("requires_registration", 0)
        event_id, _ = create_event(data, source=s["source"], status=s["status"])
        if s["status"] == "approved" and s["source"] == "student":
            set_status(event_id, "approved", "掲載を承認しました。")
