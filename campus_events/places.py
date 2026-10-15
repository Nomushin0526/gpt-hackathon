"""開催場所: 学内施設の位置、学外の場所検索、地図表示用の URL。"""

import json
import re
import unicodedata
import urllib.parse
import urllib.request

from flask import current_app

from .db import get_db

# キャンパスマップ(static/campus_map.webp)の画像サイズ。ピン位置はこの画像上のピクセル座標
CAMPUS_MAP_IMAGE = "campus_map.webp"
CAMPUS_MAP_SIZE = (1470, 830)
CAMPUS_CENTER = (35.5392, 139.3938)

# 学内施設。keys は開催場所の入力がこの文字列で始まれば、その施設とみなす(「L1号館 6階 大講義室」など)。
# lat/lng: Google マップ / OpenStreetMap に登録された位置。不明な施設は None
# pin: キャンパスマップ上の位置。緯度経度から計算した概算(_LATLNG_TO_PIN)を元にしており、
#      位置が不明な施設は None。学生課の管理画面(/admin/campus-map)でクリックして修正できる
CAMPUS_PLACES = [
    dict(name="L1号館(一般教育棟)", keys=["L1号館"], lat=35.540359, lng=139.394931, pin=(943, 297)),
    dict(name="L2号館", keys=["L2号館"], lat=35.54082, lng=139.39489, pin=(924, 247)),
    dict(name="S号館(理学部校舎)", keys=["S号館"], lat=None, lng=None, pin=None),
    dict(name="MB号館(海洋生命科学部校舎)", keys=["MB号館"], lat=35.541743, lng=139.394402, pin=(820, 149)),
    dict(name="新M号館(医学部校舎)", keys=["新M号館"], lat=None, lng=None, pin=None),
    dict(name="A1号館(医療衛生学部校舎)", keys=["A1号館"], lat=35.540309, lng=139.393426, pin=(697, 314)),
    dict(name="A2号館(医療衛生学部校舎)", keys=["A2号館"], lat=35.54049, lng=139.39393, pin=(775, 290)),
    dict(name="N号館(看護学部校舎)", keys=["N号館"], lat=35.539951, lng=139.39665, pin=(1236, 328)),
    dict(name="FR号館(未来工学部校舎)", keys=["FR号館"], lat=None, lng=None, pin=None),
    dict(name="V号館(獣医学部校舎)", keys=["V号館"], lat=None, lng=None, pin=None),
    dict(name="臨床教育研究棟(IPE棟)", keys=["臨床教育研究棟", "IPE棟"], lat=None, lng=None, pin=None),
    dict(name="多目的研究棟C棟", keys=["多目的研究棟C棟", "C棟"], lat=None, lng=None, pin=None),
    dict(name="北里大学中央図書館", keys=["北里大学中央図書館", "中央図書館"], lat=35.539636, lng=139.395013, pin=(975, 375)),
    dict(name="IPE棟図書館", keys=["IPE棟図書館"], lat=None, lng=None, pin=None),
    dict(name="学生ホール", keys=["学生ホール"], lat=35.54031, lng=139.392623, pin=(565, 320)),
    dict(name="総合体育館", keys=["総合体育館"], lat=35.540634, lng=139.391913, pin=(440, 290)),
    dict(name="部室棟", keys=["部室棟"], lat=35.541, lng=139.39198, pin=(442, 249)),
    dict(name="第一総合グラウンド", keys=["第一総合グラウンド"], lat=35.54239, lng=139.38956, pin=(175, 110)),
    dict(name="薬用植物園", keys=["薬用植物園", "北里大学薬学部附属薬用植物園"], lat=35.53995, lng=139.39163, pin=(411, 367)),
    dict(name="クレセント", keys=["クレセント"], lat=35.54014, lng=139.39193, pin=(455, 344)),
    dict(name="北里大学病院", keys=["北里大学病院"], lat=35.53817, lng=139.39311, pin=(700, 550)),
]

# 緯度経度 → キャンパスマップ上の座標 の変換(病院・総合体育館・中央図書館の 3 点で合わせた一次変換)。
# 位置が不明な施設に学生課がピンを置いたとき、逆変換で Google マップ用の緯度経度を求めるのに使う
_LATLNG_TO_PIN = (
    (-25733.24, 164303.99, -21987632.02),
    (-109310.83, -7761.35, 4967136.01),
)

# 地図を表示しない開催場所(オンライン開催など)に含まれる語
_NO_MAP_KEYWORDS = ("オンライン", "online", "zoom", "teams", "webex", "youtube")


def _normalize(text):
    """全角・半角や括弧書き、空白の違いを無視して比較するための正規化。"""
    text = unicodedata.normalize("NFKC", text or "")
    text = re.sub(r"\([^)]*\)", "", text)
    return re.sub(r"\s+", "", text).lower()


def _pin_to_latlng(x, y):
    (a, b, c), (d, e, f) = _LATLNG_TO_PIN
    det = a * e - b * d
    lat = (e * (x - c) - b * (y - f)) / det
    lng = (a * (y - f) - d * (x - c)) / det
    return round(lat, 6), round(lng, 6)


def _pin_overrides():
    rows = get_db().execute("SELECT name, x, y FROM campus_pins").fetchall()
    return {r["name"]: (r["x"], r["y"]) for r in rows}


def campus_places():
    """学生課が修正したピン位置を反映した学内施設の一覧。"""
    overrides = _pin_overrides()
    places = []
    for p in CAMPUS_PLACES:
        place = dict(p)
        if p["name"] in overrides:
            place["pin"] = overrides[p["name"]]
        if place["lat"] is None and place["pin"]:
            place["lat"], place["lng"] = _pin_to_latlng(*place["pin"])
        if place["pin"]:
            w, h = CAMPUS_MAP_SIZE
            place["pin_pct"] = (round(place["pin"][0] * 100 / w, 2), round(place["pin"][1] * 100 / h, 2))
        places.append(place)
    return places


def set_campus_pin(name, x, y):
    if name not in {p["name"] for p in CAMPUS_PLACES}:
        raise ValueError("施設が見つかりません。")
    w, h = CAMPUS_MAP_SIZE
    if not (0 <= x <= w and 0 <= y <= h):
        raise ValueError("ピンの位置が地図の範囲外です。")
    db = get_db()
    db.execute(
        "INSERT INTO campus_pins (name, x, y) VALUES (?, ?, ?) "
        "ON CONFLICT (name) DO UPDATE SET x = excluded.x, y = excluded.y",
        (name, round(x), round(y)),
    )
    db.commit()


def reset_campus_pin(name):
    db = get_db()
    db.execute("DELETE FROM campus_pins WHERE name = ?", (name,))
    db.commit()


def find_campus_place(location, places=None):
    """開催場所が学内施設(またはその中の部屋)なら、その施設を返す。"""
    text = _normalize(location)
    if not text:
        return None
    best, best_len = None, 0
    for place in places or campus_places():
        for key in [place["name"], *place["keys"]]:
            k = _normalize(key)
            if text.startswith(k) and len(k) > best_len:
                best, best_len = place, len(k)
    return best


def is_online(location):
    lowered = (location or "").lower()
    return any(k in lowered for k in _NO_MAP_KEYWORDS)


def map_info(location, lat=None, lng=None):
    """詳細ページの地図表示に必要な情報を返す。地図を出せない場所では None。

    - 学内施設: キャンパスマップ上のピンと、施設の位置の Google マップ
    - 候補から選んだ学外の場所: 選んだ位置(緯度経度)の Google マップ
    - 自由入力の学外の場所: 場所名で検索した Google マップ
    API キー不要の埋め込み形式を使う。
    """
    if not location or is_online(location):
        return None
    campus = find_campus_place(location)
    if campus:
        lat, lng = campus["lat"], campus["lng"]
        query = f"北里大学 相模原キャンパス {campus['keys'][0]}"
    else:
        query = location
    if lat is not None and lng is not None:
        query = f"{lat},{lng}"
    q = urllib.parse.quote(query)
    return {
        "campus": campus,
        "embed": f"https://maps.google.com/maps?q={q}&hl=ja&z=17&output=embed",
        "link": f"https://www.google.com/maps/search/?api=1&query={q}",
    }


def _address(props):
    parts = [props.get(k) for k in ("state", "city", "district", "locality", "street", "housenumber")]
    seen = []
    for p in parts:
        if p and p not in seen:
            seen.append(p)
    return "".join(seen)


def _fetch_external(query):
    """OpenStreetMap ベースの場所検索(Photon)で学外の場所を探す。"""
    base = current_app.config.get("PLACE_SEARCH_URL")
    if not base:
        return []
    params = urllib.parse.urlencode({
        "q": query, "limit": 10, "lat": CAMPUS_CENTER[0], "lon": CAMPUS_CENTER[1],
    })
    req = urllib.request.Request(
        f"{base}?{params}", headers={"User-Agent": "sagamihara-campus-events/1.0"}
    )
    with urllib.request.urlopen(req, timeout=5) as res:
        data = json.load(res)
    results, seen = [], set()
    for f in data.get("features", []):
        props = f.get("properties", {})
        if props.get("countrycode") not in (None, "JP") or not props.get("name"):
            continue
        lng, lat = f["geometry"]["coordinates"]
        address = _address(props)
        # OpenStreetMap では同じ施設が建物と地点の両方で登録されていることがある
        if (props["name"], address) in seen:
            continue
        seen.add((props["name"], address))
        results.append({
            "name": props["name"],
            "address": address,
            "lat": round(lat, 6),
            "lng": round(lng, 6),
            "campus": False,
        })
    return results


def search(query):
    """開催場所の候補を返す(学内施設を先に、続けて学外の場所)。"""
    q = _normalize(query)
    places = campus_places()
    campus = [
        {"name": p["name"], "address": "北里大学 相模原キャンパス", "campus": True}
        for p in places
        if not q or any(q in _normalize(k) for k in [p["name"], *p["keys"]])
    ]
    external, error = [], False
    if len(q) >= 2:
        try:
            external = _fetch_external(query.strip())
        except Exception:  # 外部サービスの障害時も学内施設の候補は返す
            current_app.logger.warning("場所検索に失敗しました", exc_info=True)
            error = True
        campus_names = {_normalize(p["name"]) for p in places}
        external = [e for e in external if _normalize(e["name"]) not in campus_names]
    return {"results": campus + external, "error": error}
