"""公開イベントの文章と、このブラウザの行動履歴から推薦を作る。

日本語を空白で分割せず、NFKC 正規化後の文字 2〜4-gram を使う。
タイトルを 2 回含めた文章を TF-IDF 化し、閲覧・お気に入りの重みで
合成したプロフィールとのコサイン類似度を求める。類似度は確率ではない。
行列積は「候補数 × 1」であり、イベント間の総当たり行列は作成しない。
"""

from datetime import timedelta
import math
import re
import unicodedata

import numpy as np
from flask import current_app
from scipy.sparse import csr_matrix
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.preprocessing import normalize

from . import events
from .db import get_db


_MAX_FEATURES = 40000
_POSITIVE_EPSILON = 1e-12


def _normalise_text(value):
    """全角英数字などを揃え、不要な連続空白を除く。"""
    value = unicodedata.normalize("NFKC", str(value or "")).lower()
    return re.sub(r"\s+", " ", value).strip()


def _event_text(event):
    """非公開の連絡先・審査情報は特徴量に含めない。"""
    title = _normalise_text(event.get("title"))
    parts = (
        title,
        title,
        _normalise_text(event.get("description")),
        _normalise_text(event.get("category")),
    )
    return "\n".join(part for part in parts if part)


def _bounded_int(value, default, minimum, maximum):
    try:
        value = int(value)
    except (TypeError, ValueError, OverflowError):
        value = default
    return min(maximum, max(minimum, value))


def _weight(name, default):
    try:
        value = float(current_app.config.get(name, default))
    except (TypeError, ValueError, OverflowError):
        return default
    return value if math.isfinite(value) and value >= 0 else default


def _settings():
    return {
        "history_days": _bounded_int(
            current_app.config.get("DS_HISTORY_DAYS", 90), 90, 1, 3650
        ),
        "view_cap": _bounded_int(
            current_app.config.get("DS_VIEW_CAP", 3), 3, 1, 3650
        ),
        "view_weight": _weight("DS_VIEW_WEIGHT", 1.0),
        "favorite_weight": _weight("DS_FAVORITE_WEIGHT", 5.0),
        "registration_weight": _weight("DS_REGISTRATION_WEIGHT", 8.0),
    }


def _profile_activity(visitor_id, approved_ids, first_day, today, as_of, settings):
    """公開中の履歴だけを使い、同じ日の再読み込みを重複加点しない。"""
    if not visitor_id:
        return {}, set(), set(), set()

    db = get_db()
    viewed = {
        row["event_id"]: min(row["viewed_days"], settings["view_cap"])
        for row in db.execute(
            """
            SELECT event_id, COUNT(DISTINCT view_date) AS viewed_days
              FROM event_views
             WHERE visitor_id = ? AND view_date >= ? AND view_date <= ?
               AND first_viewed_at <= ?
             GROUP BY event_id
            """,
            (visitor_id, first_day, today, as_of),
        )
        if row["event_id"] in approved_ids
    }
    favorites = {
        row["event_id"]
        for row in db.execute(
            """
            SELECT event_id FROM event_favorites
             WHERE visitor_id = ? AND created_at <= ?
            """,
            (visitor_id, as_of),
        )
        if row["event_id"] in approved_ids
    }
    # ログイン中の学生がこのサイトで申し込んだイベント(どの端末からの申込も含む)
    registered = {
        row["event_id"]
        for row in db.execute(
            """
            SELECT r.event_id FROM registrations r
              JOIN users u ON u.id = r.user_id
             WHERE u.visitor_id = ? AND r.created_at <= ?
            """,
            (visitor_id, as_of),
        )
        if row["event_id"] in approved_ids
    }
    weights = {
        event_id: (
            viewed.get(event_id, 0) * settings["view_weight"]
            + (settings["favorite_weight"] if event_id in favorites else 0)
            + (settings["registration_weight"] if event_id in registered else 0)
        )
        for event_id in viewed.keys() | favorites | registered
    }
    return weights, set(viewed), favorites, registered


def _available(event, now):
    """未開始で、サイト内申込の満席制限に該当しないイベント。"""
    try:
        if events.parse_dt(event["start_at"]) <= now:
            return False
    except (TypeError, ValueError):
        # 古い DB に不正な日付があっても、推薦ページ全体を停止しない。
        return False

    return not (
        event["registration_mode"] == "site"
        and event["capacity"] is not None
        and event["registered"] >= event["capacity"]
    )


def _popular(candidates, limit, first_day, today, as_of, settings, context):
    """観測された活動だけで人気順を作る。実績ゼロは開催日時順。"""
    db = get_db()
    view_days = {
        row["event_id"]: row["viewed_days"]
        for row in db.execute(
            """
            SELECT event_id, SUM(MIN(viewed_days, ?)) AS viewed_days
              FROM (
                  SELECT event_id, visitor_id,
                         COUNT(DISTINCT view_date) AS viewed_days
                    FROM event_views
                   WHERE view_date >= ? AND view_date <= ?
                     AND first_viewed_at <= ?
                   GROUP BY event_id, visitor_id
              )
             GROUP BY event_id
            """,
            (settings["view_cap"], first_day, today, as_of),
        )
    }
    favorite_counts = {
        row["event_id"]: row["favorite_count"]
        for row in db.execute(
            """
            SELECT event_id, COUNT(*) AS favorite_count
              FROM event_favorites
             WHERE created_at <= ?
             GROUP BY event_id
            """,
            (as_of,),
        )
    }
    site_counts = {
        row["event_id"]: row["registered"]
        for row in db.execute(
            """
            SELECT r.event_id, COUNT(*) AS registered
              FROM registrations r
              JOIN events e ON e.id = r.event_id
             WHERE e.registration_mode = 'site' AND r.created_at <= ?
             GROUP BY r.event_id
            """,
            (as_of,),
        )
    }

    def popularity(event):
        event_id = event["id"]
        return (
            favorite_counts.get(event_id, 0) * settings["favorite_weight"]
            + view_days.get(event_id, 0) * settings["view_weight"]
            + site_counts.get(event_id, 0)
        )

    ordered = sorted(
        candidates,
        key=lambda event: (-popularity(event), event["start_at"], event["id"]),
    )
    items = []
    for event in ordered[:limit]:
        event_id = event["id"]
        if popularity(event) > 0:
            observed = (
                f"お気に入り {favorite_counts.get(event_id, 0)} 件・"
                f"閲覧 {view_days.get(event_id, 0)} 件"
            )
            if event["registration_mode"] == "site":
                observed += f"・サイト内申込 {site_counts.get(event_id, 0)} 件"
            reason = (
                f"{context}{observed}から紹介しています。"
                "閲覧の集計：同じブラウザー・イベントは1日1件、"
                f"最大{settings['view_cap']}日分。"
            )
        else:
            reason = (
                f"{context}集計対象の利用実績がまだないため、"
                "開催日時の近い順に紹介しています。"
            )
        items.append({"event": event, "similarity": None, "reason": reason})
    return items


def get_recommendations(visitor_id, limit=6, now=None):
    """推薦一覧と、推薦の種類・使用履歴件数を返す。

    mode は personalized / popular / empty のいずれか。
    similarity は 0〜1 のコサイン類似度で、人気順では None になる。
    初期状態や内容の重なりがない場合に類似度を捏造しない。
    履歴・お気に入りに含まれる公開イベントは新しい発見の候補から除く。
    開催済みの公開イベントは興味の推定に使えるが、推薦候補にはならない。
    """
    limit = _bounded_int(limit, 6, 1, 50)
    now = events.now_jst() if now is None else now
    if now.tzinfo is not None:
        now = now.astimezone(events.JST).replace(tzinfo=None)
    settings = _settings()
    # 「90日」は今日を含む90暦日。秒数で引いて日境界を曖昧にしない。
    first_day = (now.date() - timedelta(days=settings["history_days"] - 1)).isoformat()
    today = now.date().isoformat()
    as_of = now.isoformat(timespec="microseconds")

    corpus = [
        dict(row)
        for row in get_db().execute(
            """
            SELECT e.*,
                   (SELECT COUNT(*) FROM registrations r
                     WHERE r.event_id = e.id) AS registered
              FROM events e
             WHERE e.status = 'approved'
             ORDER BY e.id
            """
        )
    ]
    approved_ids = {event["id"] for event in corpus}
    weights, viewed, favorites, registered = _profile_activity(
        visitor_id, approved_ids, first_day, today, as_of, settings
    )
    result = {
        "items": [],
        "mode": "empty",
        "history_event_count": len(viewed),
        "favorite_event_count": len(favorites),
        "registered_event_count": len(registered),
        "profile_event_count": len(weights),
    }
    candidates = [
        event for event in corpus
        if event["id"] not in weights and _available(event, now)
    ]
    if not candidates:
        return result

    context = (
        "閲覧・お気に入りの履歴がないため、"
        if not weights else "内容の近い未閲覧イベントが見つからないため、"
    )
    if any(weight > 0 for weight in weights.values()):
        vectorizer = TfidfVectorizer(
            analyzer="char",
            ngram_range=(2, 4),
            lowercase=False,  # _event_text 内で NFKC 正規化と小文字化を済ませる。
            norm="l2",
            smooth_idf=True,
            sublinear_tf=True,
            min_df=1,
            max_features=_MAX_FEATURES,
            dtype=np.float64,
        )
        try:
            matrix = vectorizer.fit_transform([_event_text(event) for event in corpus])
        except ValueError as exc:
            # 空文書や1文字だけのデータには 2-gram が存在しない。
            if "empty vocabulary" not in str(exc):
                raise
        else:
            positions = {event["id"]: index for index, event in enumerate(corpus)}
            profile_ids = sorted(event_id for event_id, weight in weights.items() if weight > 0)
            weight_row = csr_matrix([[weights[event_id] for event_id in profile_ids]])
            profile = weight_row @ matrix[[positions[event_id] for event_id in profile_ids]]
            if profile.nnz:
                profile = normalize(profile, norm="l2", copy=False)
                # 候補数 × 1 の結果だけを dense 化する（N × N は作らない）。
                scores = (
                    matrix[[positions[event["id"]] for event in candidates]] @ profile.T
                ).toarray().ravel()
                scored = [
                    (event, float(np.clip(score, 0.0, 1.0)))
                    for event, score in zip(candidates, scores)
                    if score > _POSITIVE_EPSILON
                ]
                scored.sort(key=lambda pair: (-pair[1], pair[0]["start_at"], pair[0]["id"]))
                if scored:
                    result["mode"] = "personalized"
                    result["items"] = [
                        {
                            "event": event,
                            "similarity": score,
                            "reason": (
                                "閲覧・お気に入り・申込した公開イベントと、"
                                "タイトル・説明文・カテゴリの文字特徴が近い候補です。"
                            ),
                        }
                        for event, score in scored[:limit]
                    ]
                    return result

    result["mode"] = "popular"
    result["items"] = _popular(
        candidates, limit, first_day, today, as_of, settings, context
    )
    return result
