import re
import shutil

import pytest

from campus_events import create_app, places
from campus_events.db import get_db


@pytest.fixture
def app(tmp_path):
    return create_app({
        "TESTING": True, "DATABASE": str(tmp_path / "test.sqlite"), "UPLOAD_FOLDER": str(tmp_path),
        "ADMIN_PASSWORD": "secret", "SECRET_KEY": "test", "PLACE_SEARCH_URL": "",
    })


@pytest.fixture
def source_copy(tmp_path, monkeypatch):
    """本物の places.py を書き換えないよう、コピーを書き戻し先にする。"""
    copy = tmp_path / "places_copy.py"
    shutil.copy(places.__file__, copy)
    monkeypatch.setattr(places, "__file__", str(copy))
    return copy


def csrf(client, path):
    html = client.get(path).get_data(as_text=True)
    return re.search(r'name="_csrf_token" value="([^"]+)"', html).group(1)


def login(client):
    client.post("/admin/login", data={"password": "secret", "staff_name": "テスト担当",
                                      "_csrf_token": csrf(client, "/admin/login")})


def set_pin(client, name, x, y):
    client.post("/admin/campus-map", data={"name": name, "x": x, "y": y,
                                           "_csrf_token": csrf(client, "/admin/campus-map")})


def test_pin_page_lists_changed_pins(app):
    client = app.test_client()
    login(client)
    set_pin(client, "L2号館", 930, 250)
    set_pin(client, "多目的研究棟C棟", 600, 200)
    page = client.get("/admin/campus-map").get_data(as_text=True)
    assert "修正したピン位置(2 件)" in page
    assert "L2号館: pin=(930, 250)  ※元: pin=(924, 247)" in page
    assert "多目的研究棟C棟: pin=(600, 200)  ※元: 未設定" in page
    assert "すべての建物名を表示する" in page


def test_apply_pins_rewrites_source_and_clears_overrides(app, source_copy):
    client = app.test_client()
    login(client)
    set_pin(client, "L2号館", 930, 250)
    set_pin(client, "多目的研究棟C棟", 600, 200)

    result = app.test_cli_runner().invoke(args=["apply-pins"])
    assert "2 件のピン位置を places.py に書き戻しました" in result.output

    text = source_copy.read_text(encoding="utf-8")
    assert 'dict(name="L2号館", keys=["L2号館"], lat=35.54082, lng=139.39489, pin=(930, 250))' in text
    assert 'dict(name="多目的研究棟C棟", keys=["多目的研究棟C棟", "C棟"], lat=None, lng=None, pin=(600, 200))' in text
    assert 'pin=(943, 297)' in text  # 修正していない施設はそのまま
    compile(text, str(source_copy), "exec")  # 書き戻した後も Python として正しい
    with app.app_context():
        assert get_db().execute("SELECT COUNT(*) FROM campus_pins").fetchone()[0] == 0


def test_apply_pins_without_changes(app, source_copy):
    before = source_copy.read_text(encoding="utf-8")
    result = app.test_cli_runner().invoke(args=["apply-pins"])
    assert "書き戻すピンの修正はありません" in result.output
    assert source_copy.read_text(encoding="utf-8") == before


def test_pin_equal_to_default_is_not_listed(app):
    client = app.test_client()
    login(client)
    set_pin(client, "L2号館", 924, 247)  # 初期位置と同じ
    assert "修正したピン位置(0 件)" in client.get("/admin/campus-map").get_data(as_text=True)
