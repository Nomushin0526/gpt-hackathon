"""公開イベントの開催日を基準にした、学生課向け集計。

選択期間はイベントの開催開始日（日本時間）に適用する。対象イベントへの
申込は作成日にかかわらず現在残っている全件、閲覧は保存期間内の全記録、
お気に入りは現在残っている全件を集計する。申込数は site のイベントだけで
観測でき、外部申込・申込不要のイベントは 0 件として比較しない。
"""

from datetime import date, datetime
import math

from .db import get_db
from .events import CATEGORIES


WEEKDAY_LABELS = ("月曜日", "火曜日", "水曜日", "木曜日", "金曜日", "土曜日", "日曜日")


# 1 対多のテーブルを先に別々に集計する。生の申込・閲覧・お気に入りを
# 同時に JOIN すると、組合せの数だけ同じ記録を重複計上してしまう。
_EVENT_METRICS_SQL = """
WITH cohort AS (
    SELECT id, title, category, start_at, registration_mode, capacity
    FROM events
    WHERE status = 'approved'
      AND substr(start_at, 1, 10) >= ?
      AND substr(start_at, 1, 10) <= ?
), registration_totals AS (
    SELECT r.event_id, COUNT(*) AS registered
    FROM registrations r
    JOIN cohort e ON e.id = r.event_id
    WHERE e.registration_mode = 'site'
    GROUP BY r.event_id
), view_totals AS (
    SELECT v.event_id, COUNT(*) AS view_count
    FROM event_views v
    JOIN cohort e ON e.id = v.event_id
    GROUP BY v.event_id
), favorite_totals AS (
    SELECT f.event_id, COUNT(*) AS favorite_count
    FROM event_favorites f
    JOIN cohort e ON e.id = f.event_id
    GROUP BY f.event_id
)
SELECT e.*,
       CASE WHEN e.registration_mode = 'site'
            THEN COALESCE(r.registered, 0)
            ELSE NULL END AS registered,
       COALESCE(v.view_count, 0) AS view_count,
       COALESCE(f.favorite_count, 0) AS favorite_count,
       (SELECT COUNT(DISTINCT v2.visitor_id)
          FROM event_views v2
          JOIN cohort e2 ON e2.id = v2.event_id) AS unique_visitor_count
FROM cohort e
LEFT JOIN registration_totals r ON r.event_id = e.id
LEFT JOIN view_totals v ON v.event_id = e.id
LEFT JOIN favorite_totals f ON f.event_id = e.id
ORDER BY e.start_at, e.id
"""


def _positive_capacity(value):
    """過去データに不正値があっても、定員が判明するイベントだけを使う。"""
    try:
        capacity = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return capacity if math.isfinite(capacity) and capacity > 0 else None


def _empty_group(label):
    return {
        "label": label,
        "event_count": 0,
        "site_event_count": 0,
        "registration_count": None,
        "average_registrations": None,
        "view_count": 0,
        "favorite_count": 0,
    }


def _add_to_group(group, event):
    group["event_count"] += 1
    group["view_count"] += event["view_count"]
    group["favorite_count"] += event["favorite_count"]
    if event["registration_mode"] == "site":
        group["site_event_count"] += 1
        group["registration_count"] = (
            (group["registration_count"] or 0) + event["registered"]
        )


def _finish_group(group):
    if group["site_event_count"]:
        group["average_registrations"] = round(
            group["registration_count"] / group["site_event_count"], 2
        )
    return group


def build_dashboard(start_date: date, end_date: date):
    """開催開始日の両端を含む期間について、個人情報を含まない辞書を返す。

    契約:
      * summary / categories / weekdays: site_event_count が 0 の場合、
        registration_count と average_registrations は None。
      * ranking: 全対象イベントを返し、site は申込件数の降順で先頭に置く。
        external / none の registered と occupancy_percent は None。
      * view_count: 同一 visitor_id・event_id・日を 1 件とする保存中の閲覧数。
        unique_visitor_count: 対象イベントを閲覧した匿名 ID の重複なしの数。
        いずれも実人数・実際の参加人数を表さない。
      * capacity_utilization_percent: 正の有限な定員を持つ site イベントの
        申込合計 ÷ 定員合計 × 100。イベント別の率の単純平均は使わない。

    開催日と申込日・閲覧日を同じ期間で絞らない。例えば 10 月開催イベントの
    9 月申込も申込数に含む。集計値は現在の状態であり過去時点の再現ではない。
    """
    if (
        not isinstance(start_date, date)
        or isinstance(start_date, datetime)
        or not isinstance(end_date, date)
        or isinstance(end_date, datetime)
    ):
        raise TypeError("start_date と end_date には datetime.date を指定してください。")
    if start_date > end_date:
        raise ValueError("開始日は終了日以前にしてください。")

    rows = get_db().execute(
        _EVENT_METRICS_SQL, (start_date.isoformat(), end_date.isoformat())
    ).fetchall()

    categories = {label: _empty_group(label) for label in CATEGORIES}
    weekdays = [
        {**_empty_group(label), "weekday_index": index}
        for index, label in enumerate(WEEKDAY_LABELS)
    ]
    summary = _empty_group("全体")
    summary.pop("label")
    summary.update(
        unique_visitor_count=int(rows[0]["unique_visitor_count"]) if rows else 0,
        capacity_event_count=0,
        capacity_registration_count=0,
        capacity_total=0,
        capacity_utilization_percent=None,
    )
    ranking = []

    for row in rows:
        event = dict(row)
        # 全体の匿名 ID 数はランキングに含めない。
        event.pop("unique_visitor_count")
        capacity = _positive_capacity(event["capacity"])
        event["capacity"] = (
            int(capacity) if capacity is not None and capacity.is_integer() else capacity
        )
        event["occupancy_percent"] = None
        if event["registration_mode"] == "site" and capacity is not None:
            event["occupancy_percent"] = round(event["registered"] / capacity * 100, 1)
            summary["capacity_event_count"] += 1
            summary["capacity_registration_count"] += event["registered"]
            summary["capacity_total"] += capacity

        category = event["category"]
        if category not in categories:
            # 定数変更前に登録されたカテゴリがあっても集計から落とさない。
            categories[category] = _empty_group(category)
        weekday = date.fromisoformat(event["start_at"][:10]).weekday()
        _add_to_group(summary, event)
        _add_to_group(categories[category], event)
        _add_to_group(weekdays[weekday], event)
        ranking.append(event)

    if summary["capacity_total"]:
        summary["capacity_utilization_percent"] = round(
            summary["capacity_registration_count"] / summary["capacity_total"] * 100, 1
        )
    _finish_group(summary)
    ranking.sort(
        key=lambda event: (
            event["registration_mode"] != "site",
            -(event["registered"] or 0),
            event["start_at"],
            event["id"],
        )
    )

    return {
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "summary": summary,
        "categories": [_finish_group(group) for group in categories.values()],
        "weekdays": [_finish_group(group) for group in weekdays],
        "ranking": ranking,
    }


def csv_safe(value):
    """CSV セルの先頭を式として解釈されない文字列にする。

    csv.writer の引用符は区切り文字対策であり、表計算の式の実行は防がない。
    先頭空白の後の = + - @、先頭の制御文字にもアポストロフィを付ける。
    数値も文字列に統一し、欠測値 None は空欄として出力する。
    """
    text = "" if value is None else str(value)
    stripped = text.lstrip()
    leading_whitespace = text[: len(text) - len(stripped)]
    if stripped.startswith(("=", "+", "-", "@")) or any(
        ch in "\t\r\n\v\f" for ch in leading_whitespace
    ):
        return "'" + text
    return text
