"""動作確認用のサンプルデータ。

イベントの内容・団体名は架空だが、開催場所は北里大学 相模原キャンパスに実在する施設の
正式名称を使用している(出典: 相模原キャンパスマップ、中央図書館開館案内)。
"""

from datetime import timedelta

from .events import DATETIME_FORMAT, create_event, now_jst, register, set_status

_SAMPLES = [
    dict(days=2, hour=17, hours=1.5, title="【サンプル】就活スタートアップ講座", category="就職・キャリア",
         location="L1号館 6階 大講義室", organizer_name="学生課", organizer_type="学生課・大学",
         description="これから就職活動を始める学生向けの講座です。自己分析の進め方とインターンシップの探し方を解説します。",
         source="office", status="approved", registration_mode="site", capacity=120, registered=87),
    dict(days=5, hour=12, hours=1, title="【サンプル】昼休みミニコンサート", category="部活動・サークル",
         location="学生ホール", organizer_name="管弦楽部(サンプル)", organizer_type="部活動・サークル",
         description="昼休みに弦楽四重奏をお届けします。出入り自由です。", source="student", status="approved"),
    dict(days=7, hour=16, hours=1, title="【サンプル】北里大学中央図書館 活用ガイダンス", category="学術・研究",
         location="北里大学中央図書館", organizer_name="学生課", organizer_type="学生課・大学",
         description="2025年12月に開館した中央図書館の使い方(蔵書検索、学習スペース、電子ジャーナル)を紹介します。",
         source="office", status="approved", registration_mode="site", capacity=20, registered=18),
    dict(days=9, hour=13, hours=2, title="【サンプル】AED・心肺蘇生 体験講習会", category="健康・医療",
         location="総合体育館", organizer_name="救命講習サークル(サンプル)", organizer_type="部活動・サークル",
         description="AED の使い方と胸骨圧迫を実際に体験できます。動きやすい服装でお越しください。",
         source="student", status="approved", registration_mode="external", capacity=40,
         registration_info="Google フォーム(サンプルのため URL はありません)から申込。締切は開催 3 日前。"),
    dict(days=16, hour=18, hours=2, title="【サンプル】留学生と話そう! 国際交流カフェ", category="国際交流",
         location="L1号館 2階 学生食堂", organizer_name="学生課", organizer_type="学生課・大学",
         description="留学生と気軽に話せる交流会です。英語が苦手でも大丈夫。", source="office", status="approved"),
    dict(days=12, hour=16, hours=2, title="【サンプル】ボードゲーム交流会", category="部活動・サークル",
         location="部室棟", organizer_name="ボードゲーム同好会(サンプル)", organizer_type="部活動・サークル",
         description="新入生歓迎を兼ねたボードゲーム会です。", source="student", status="pending",
         registration_mode="site", capacity=30,
         note_to_office="部室棟の使用について事前に確認済みです。"),
]


def seed():
    base = now_jst().replace(minute=0, second=0, microsecond=0)
    for s in _SAMPLES:
        start = (base + timedelta(days=s["days"])).replace(hour=s["hour"])
        end = start + timedelta(hours=s["hours"])
        data = {k: v for k, v in s.items()
                if k not in ("days", "hour", "hours", "source", "status", "registered")}
        data.update(
            start_at=start.strftime(DATETIME_FORMAT),
            end_at=end.strftime(DATETIME_FORMAT),
            contact_email="sample@example.com",
        )
        data.setdefault("registration_mode", "none")
        event_id, _ = create_event(data, source=s["source"], status=s["status"])
        if s["status"] == "approved" and s["source"] == "student":
            set_status(event_id, "approved", "掲載を承認しました。")
        for i in range(s.get("registered", 0)):
            register(event_id, {
                "name": f"サンプル学生{i + 1}",
                "email": f"sample{i + 1}@example.com",
                "student_number": None,
            })
